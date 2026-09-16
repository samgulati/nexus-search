from __future__ import annotations

import re
import time

import httpx

from ..config import settings
from ..models import AskResponse, Citation, EvidenceSummary, SearchResponse

SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")

OFFICIAL_AUTHORITY = {
    "developer.mozilla.org": 1.00,
    "kubernetes.io": 1.00,
    "postgresql.org": 1.00,
    "redis.io": 1.00,
    "docs.docker.com": 1.00,
    "kafka.apache.org": 1.00,
    "opentelemetry.io": 1.00,
    "prometheus.io": 1.00,
    "cheatsheetseries.owasp.org": 0.98,
    "docs.python.org": 1.00,
    "fastapi.tiangolo.com": 0.98,
    "react.dev": 1.00,
    "docs.oracle.com": 1.00,
    "aws.amazon.com": 0.98,
    "sre.google": 0.98,
}

QUERY_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "does",
    "for", "from", "how", "i", "in", "is", "it", "of", "on", "or", "should",
    "the", "this", "to", "use", "what", "when", "where", "which", "why", "with",
}


class AnswerService:
    async def answer_from_search(
        self, query: str, search: SearchResponse, *, force_extractive: bool = False
    ) -> AskResponse:
        citations = [
            Citation(index=i, title=r.title, url=r.url, document_id=r.id)
            for i, r in enumerate(search.results, start=1)
        ]

        evidence = self._evaluate_evidence(query, search)

        if not search.results or evidence.decision == "abstain":
            return AskResponse(
                query=query,
                answer=(
                    "No reliable evidence was found in Nexus's trusted sources. "
                    "Nexus does not generate unsupported answers. Try rephrasing the query "
                    "or search a broader source set."
                ),
                citations=citations if search.results else [],
                retrieval_ms=search.took_ms,
                generation_ms=0.0,
                model="evidence-gate",
                grounded=True,
                evidence=evidence,
                plan=search.plan,
            )

        t0 = time.perf_counter()
        if force_extractive:
            answer = self._extractive_answer(query, search.results)
            model = "adaptive-extractive"
        elif settings.openai_api_key:
            try:
                answer = await self._openai_answer(query, search.results)
                model = settings.answer_model
            except Exception:
                answer = self._extractive_answer(query, search.results)
                model = "extractive-fallback"
        else:
            answer = self._extractive_answer(query, search.results)
            model = "extractive-grounded"

        generation_ms = (time.perf_counter() - t0) * 1000
        if evidence.decision == "answer_with_caveat":
            answer = (
                "Evidence is partial, so treat this answer as qualified rather than complete. "
                + answer
            )

        return AskResponse(
            query=query,
            answer=answer,
            citations=citations,
            retrieval_ms=search.took_ms,
            generation_ms=round(generation_ms, 3),
            model=model,
            grounded=True,
            evidence=evidence,
            plan=search.plan,
        )

    @staticmethod
    def _source_host(result) -> str:
        from urllib.parse import urlparse

        if result.url:
            try:
                return (urlparse(result.url).hostname or "").lower()
            except Exception:
                return ""
        source = (result.source or "").lower()
        if source.startswith("trusted-docs:"):
            return source.split(":", 1)[1]
        return ""

    @classmethod
    def _authority_score(cls, result) -> float:
        host = cls._source_host(result)
        if host in OFFICIAL_AUTHORITY:
            return OFFICIAL_AUTHORITY[host]
        for domain, score in OFFICIAL_AUTHORITY.items():
            if host.endswith("." + domain):
                return score
        if (result.source or "").startswith("trusted-docs:"):
            return 0.85
        return 0.60

    @staticmethod
    def _query_terms(query: str) -> set[str]:
        terms = set(re.findall(r"[a-z0-9][a-z0-9_+.#-]*", query.lower()))
        return {term for term in terms if len(term) > 1 and term not in QUERY_STOPWORDS}

    @classmethod
    def _evaluate_evidence(cls, query: str, search: SearchResponse) -> EvidenceSummary:
        if not search.results:
            return EvidenceSummary(
                decision="abstain",
                confidence=0.0,
                coverage=0.0,
                authority=0.0,
                independent_sources=0,
                reasons=["No indexed results matched the query."],
            )

        query_terms = cls._query_terms(query)
        covered: set[str] = set()
        authorities: list[float] = []
        hosts: set[str] = set()

        for result in search.results:
            haystack = f"{result.title} {result.snippet}".lower()
            covered.update(term for term in query_terms if term in haystack)
            authorities.append(cls._authority_score(result))
            host = cls._source_host(result)
            if host:
                hosts.add(host)

        coverage = 1.0 if not query_terms else len(covered) / len(query_terms)
        authority = sum(authorities) / len(authorities) if authorities else 0.0
        independent_sources = len(hosts) if hosts else len(
            {r.source for r in search.results if r.source}
        )

        source_strength = min(independent_sources, 3) / 3.0
        confidence = 0.55 * coverage + 0.30 * authority + 0.15 * source_strength
        confidence = max(0.0, min(confidence, 1.0))

        reasons: list[str] = []
        if coverage < 0.45:
            reasons.append("Retrieved passages cover too little of the query.")
        if authority < 0.75:
            reasons.append("Retrieved evidence is not dominated by high-authority sources.")
        if independent_sources < 2:
            reasons.append("Evidence comes from fewer than two independent sources.")

        if coverage < 0.30 or confidence < 0.50:
            decision = "abstain"
        elif confidence < 0.75 or coverage < 0.65:
            decision = "answer_with_caveat"
        else:
            decision = "answer"

        if not reasons:
            reasons.append("Evidence coverage and source authority are sufficient.")

        return EvidenceSummary(
            decision=decision,
            confidence=round(confidence, 3),
            coverage=round(coverage, 3),
            authority=round(authority, 3),
            independent_sources=independent_sources,
            reasons=reasons,
        )

    async def _openai_answer(self, query: str, results) -> str:
        context_blocks = []
        for i, result in enumerate(results, start=1):
            context_blocks.append(
                f"[{i}] {result.title}\nURL: {result.url or 'local'}\n{result.snippet[:3500]}"
            )
        context = "\n\n".join(context_blocks)

        prompt = (
            "You are the grounded answer layer of a search engine. Answer ONLY from the supplied sources. "
            "If the sources are insufficient, say so. Cite factual claims inline using [1], [2], etc. "
            "Do not invent sources or citations. Keep the answer concise but useful.\n\n"
            f"QUERY:\n{query}\n\nSOURCES:\n{context}"
        )
        headers = {
            "Authorization": f"Bearer {settings.openai_api_key}",
            "Content-Type": "application/json",
        }
        payload = {"model": settings.answer_model, "input": prompt}
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(f"{settings.openai_base_url}/responses", headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()

        if data.get("output_text"):
            return str(data["output_text"]).strip()
        chunks = []
        for item in data.get("output", []):
            for content in item.get("content", []):
                if content.get("type") in {"output_text", "text"} and content.get("text"):
                    chunks.append(content["text"])
        if not chunks:
            raise ValueError("No output text returned")
        return "\n".join(chunks).strip()

    @staticmethod
    def _extractive_answer(query: str, results) -> str:
        query_terms = set(re.findall(r"[a-z0-9]+", query.lower()))
        candidates: list[tuple[float, str, int]] = []
        for source_idx, result in enumerate(results, start=1):
            sentences = SENTENCE_RE.split(result.snippet.replace("…", " "))
            for sentence in sentences:
                clean = sentence.strip()
                if len(clean) < 35:
                    continue
                terms = set(re.findall(r"[a-z0-9]+", clean.lower()))
                overlap = len(query_terms & terms)
                score = overlap + max(result.semantic_score, 0) * 2
                candidates.append((score, clean, source_idx))
        candidates.sort(key=lambda x: x[0], reverse=True)
        selected = []
        seen = set()
        for _score, sentence, source_idx in candidates:
            key = sentence.lower()[:100]
            if key in seen:
                continue
            seen.add(key)
            selected.append(f"{sentence} [{source_idx}]")
            if len(selected) == 3:
                break
        if not selected:
            return "The indexed sources matched the query, but they did not contain enough extractable detail for a grounded answer."
        return " ".join(selected)


answer_service = AnswerService()
