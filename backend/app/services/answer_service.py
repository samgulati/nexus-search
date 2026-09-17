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
    "every", "for", "from", "how", "i", "in", "is", "it", "of", "on", "or",
    "should", "the", "this", "to", "use", "what", "when", "where", "which",
    "why", "with", "always", "inside",
}

DOMAIN_TOPICS = {
    "developer.mozilla.org": {
        "http", "https", "status", "header", "cookie", "cors", "csp", "html",
        "css", "javascript", "browser", "web", "fetch",
    },
    "kubernetes.io": {
        "kubernetes", "k8s", "pod", "deployment", "service", "container",
        "cluster", "probe", "scheduler",
    },
    "postgresql.org": {
        "postgres", "postgresql", "sql", "database", "transaction", "index",
        "replication", "query",
    },
    "redis.io": {
        "redis", "cache", "caching", "key", "ttl", "stream", "pubsub",
    },
    "docs.docker.com": {
        "docker", "container", "image", "compose", "dockerfile", "registry",
    },
    "kafka.apache.org": {
        "kafka", "stream", "streams", "event", "events", "producer", "consumer",
        "partition", "offset", "exactly-once", "processing",
    },
    "opentelemetry.io": {
        "opentelemetry", "otel", "trace", "tracing", "span", "metric", "metrics",
        "telemetry",
    },
    "prometheus.io": {
        "prometheus", "metric", "metrics", "alert", "alertmanager", "scrape",
        "monitoring",
    },
    "cheatsheetseries.owasp.org": {
        "owasp", "security", "authentication", "authorization", "xss", "csrf",
        "injection",
    },
    "docs.python.org": {
        "python", "asyncio", "typing", "exception", "iterator", "decorator",
    },
    "fastapi.tiangolo.com": {
        "fastapi", "pydantic", "api", "endpoint", "dependency", "async",
    },
    "react.dev": {
        "react", "component", "hook", "state", "jsx", "frontend",
    },
    "docs.oracle.com": {
        "java", "jvm", "thread", "class", "interface", "collection",
    },
    "aws.amazon.com": {
        "aws", "cloud", "availability", "scaling", "distributed", "latency",
        "reliability",
    },
    "sre.google": {
        "sre", "reliability", "distributed", "availability", "latency", "incident",
        "error", "budget",
    },
}

MIN_RESULT_RELEVANCE = 0.18
RELATIVE_RELEVANCE_FLOOR = 0.58
JUDGMENT_TERMS = {
    "should", "always", "better", "best", "when", "tradeoff", "tradeoffs",
    "appropriate", "worth", "prefer", "choose", "recommended",
}
TRADEOFF_TERMS = {
    "tradeoff", "tradeoffs", "cost", "costs", "overhead", "latency", "throughput",
    "limitation", "limitations", "downside", "downsides", "advantage", "advantages",
    "disadvantage", "disadvantages", "depends", "appropriate", "when", "if",
    "guarantee", "guarantees", "failure", "failures", "retry", "retries",
    "duplicate", "duplicates", "idempotent", "idempotency",
}

NEGATION_TERMS = {
    "not", "no", "never", "none", "cannot", "can't", "cant", "doesn't", "doesnt",
    "isn't", "isnt", "won't", "wont", "without", "avoid", "avoids", "unsupported",
    "deprecated", "disabled", "false", "incorrect",
}
CLAIM_STOPWORDS = QUERY_STOPWORDS | {
    "means", "mean", "using", "used", "also", "may", "might", "could", "would",
    "server", "system", "systems", "service", "services",
}


class AnswerService:
    async def answer_from_search(
        self,
        query: str,
        search: SearchResponse,
        *,
        force_extractive: bool = False,
        result_limit: int | None = None,
    ) -> AskResponse:
        candidate_count = len(search.results)
        relevant_results = self._rank_relevant_results(
            query,
            search.results,
            limit=result_limit,
        )
        supported_results = self._prune_to_supported_results(query, relevant_results)
        search = search.model_copy(
            update={"results": supported_results, "total": len(supported_results)}
        )

        citations = [
            Citation(index=i, title=r.title, url=r.url, document_id=r.id)
            for i, r in enumerate(search.results, start=1)
        ]

        evidence = self._evaluate_evidence(
            query,
            search,
            candidate_count=candidate_count,
            relevant_candidate_count=len(relevant_results),
        )

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
            if evidence.conflict_detected:
                answer = (
                    "Relevant sources contain potentially conflicting claims, so treat this "
                    "answer as qualified rather than definitive. " + answer
                )
            else:
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
    def _term_coverage(query_terms: set[str], text: str) -> float:
        if not query_terms:
            return 1.0
        lowered = text.lower()
        matched = sum(1 for term in query_terms if term in lowered)
        return matched / len(query_terms)

    @classmethod
    def _result_relevance(cls, query: str, result) -> float:
        query_terms = cls._query_terms(query)
        if not query_terms:
            return 1.0

        title_coverage = cls._term_coverage(query_terms, result.title or "")
        snippet_coverage = cls._term_coverage(query_terms, result.snippet or "")

        if title_coverage > 0:
            relevance = 0.72 * title_coverage + 0.28 * snippet_coverage
        else:
            relevance = 0.50 * snippet_coverage

        identifier_terms = {
            term for term in query_terms
            if any(ch.isdigit() for ch in term) or "-" in term or len(term) <= 5
        }
        if identifier_terms:
            matched_identifiers = sum(
                1 for term in identifier_terms if term in (result.title or "").lower()
            )
            relevance += 0.12 * (matched_identifiers / len(identifier_terms))

        return max(0.0, min(relevance, 1.0))

    @classmethod
    def _rank_relevant_results(cls, query: str, results, *, limit: int | None = None):
        ranked = []
        for result in results:
            relevance = cls._result_relevance(query, result)
            if relevance < MIN_RESULT_RELEVANCE:
                continue
            ranked.append((relevance, result.score, result))

        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        if not ranked:
            return []

        # Dynamic pruning: once one candidate is clearly stronger, weak tail
        # matches are not allowed to survive just because they cleared a fixed
        # global threshold.
        best_relevance = ranked[0][0]
        relative_floor = max(MIN_RESULT_RELEVANCE, best_relevance * RELATIVE_RELEVANCE_FLOOR)

        query_terms = cls._query_terms(query)
        identifier_terms = {
            term for term in query_terms
            if any(ch.isdigit() for ch in term) or "-" in term
        }

        selected = []
        for relevance, _score, result in ranked:
            if relevance < relative_floor:
                continue
            haystack = f"{result.title} {result.snippet}".lower()
            if identifier_terms and not all(term in haystack for term in identifier_terms):
                continue
            selected.append(result)
            if limit is not None and len(selected) >= limit:
                break
        return selected

    @classmethod
    def _is_judgment_query(cls, query: str) -> bool:
        terms = set(re.findall(r"[a-z0-9-]+", query.lower()))
        return bool(terms & JUDGMENT_TERMS)

    @classmethod
    def _sentence_support_score(cls, query: str, result, sentence: str) -> float:
        query_terms = cls._query_terms(query)
        if not query_terms:
            return 0.0

        lowered = sentence.lower()
        sentence_terms = set(re.findall(r"[a-z0-9][a-z0-9_+.#-]*", lowered))
        matched = query_terms & sentence_terms
        coverage = len(matched) / len(query_terms)

        identifier_terms = {
            term for term in query_terms
            if any(ch.isdigit() for ch in term) or "-" in term
        }
        identifier_bonus = 0.0
        if identifier_terms:
            identifier_hits = len(identifier_terms & sentence_terms)
            identifier_bonus = 0.25 * (identifier_hits / len(identifier_terms))

        title_terms = set(re.findall(r"[a-z0-9][a-z0-9_+.#-]*", (result.title or "").lower()))
        title_overlap = len(query_terms & title_terms) / len(query_terms)

        score = 0.65 * coverage + 0.20 * title_overlap + identifier_bonus

        if cls._is_judgment_query(query):
            tradeoff_hits = sentence_terms & TRADEOFF_TERMS
            if not tradeoff_hits:
                score *= 0.45
            else:
                score += min(0.20, 0.05 * len(tradeoff_hits))

        return max(0.0, min(score, 1.0))

    @classmethod
    def _supporting_sentences(cls, query: str, result) -> list[tuple[float, str]]:
        sentences = SENTENCE_RE.split((result.snippet or "").replace("…", " "))
        supported: list[tuple[float, str]] = []
        threshold = 0.34 if cls._is_judgment_query(query) else 0.30

        for sentence in sentences:
            clean = sentence.strip()
            if len(clean) < 35:
                continue
            score = cls._sentence_support_score(query, result, clean)
            if score >= threshold:
                supported.append((score, clean))

        supported.sort(key=lambda item: item[0], reverse=True)
        return supported

    @classmethod
    def _prune_to_supported_results(cls, query: str, results):
        supported_results = []
        for result in results:
            if cls._supporting_sentences(query, result):
                supported_results.append(result)
        return supported_results

    @classmethod
    def _claim_tokens(cls, query: str, sentence: str) -> set[str]:
        query_terms = cls._query_terms(query)

        # The general Nexus tokenizer intentionally permits characters such as
        # ".", "#", "+", "_" and "-" because they are useful inside technical
        # identifiers. For claim comparison, however, trailing punctuation must
        # not turn "charges" and "charges." into different tokens.
        raw_tokens = re.findall(r"[a-z0-9][a-z0-9_+.#-]*", sentence.lower())
        tokens = {
            token.strip("._+#-")
            for token in raw_tokens
            if token.strip("._+#-")
        }

        return {
            token for token in tokens
            if len(token) > 2
            and token not in CLAIM_STOPWORDS
            and token not in query_terms
            and token not in NEGATION_TERMS
        }

    @classmethod
    def _sentence_polarity(cls, query: str, sentence: str) -> int:
        # Detect negation of the query's focal claim, not any negation anywhere
        # in the sentence. Example:
        #   "exactly-once is required when duplicates cannot be tolerated"
        # supports "required" even though "cannot" appears later.
        tokens = re.findall(r"[a-z0-9][a-z0-9_+.#'-]*", sentence.lower())
        query_terms = cls._query_terms(query)

        for idx, token in enumerate(tokens):
            if token not in query_terms:
                continue
            window = tokens[max(0, idx - 3):idx]
            if any(term in NEGATION_TERMS for term in window):
                return -1

        return 1

    @staticmethod
    def _jaccard(left: set[str], right: set[str]) -> float:
        if not left or not right:
            return 0.0
        return len(left & right) / len(left | right)

    @classmethod
    def _claims_comparable(
        cls,
        left_tokens: set[str],
        right_tokens: set[str],
    ) -> bool:
        if not left_tokens or not right_tokens:
            return False

        shared = left_tokens & right_tokens
        if len(shared) >= 2:
            return True

        return cls._jaccard(left_tokens, right_tokens) >= 0.18

    @classmethod
    def _agreement_summary(cls, query: str, results) -> tuple[str, bool, int, int]:
        if not results:
            return "insufficient", False, 0, 0
        if len(results) == 1:
            return "single_source", False, 1, 0

        claims: list[tuple[set[str], int]] = []
        for result in results:
            supported = cls._supporting_sentences(query, result)
            if not supported:
                continue
            sentence = supported[0][1]
            claims.append((
                cls._claim_tokens(query, sentence),
                cls._sentence_polarity(query, sentence),
            ))

        if len(claims) < 2:
            return "insufficient", False, len(claims), 0

        comparable_pairs = 0
        conflicting_pairs = 0
        aligned_indexes: set[int] = set()
        conflict_indexes: set[int] = set()

        for i in range(len(claims)):
            for j in range(i + 1, len(claims)):
                left_tokens, left_polarity = claims[i]
                right_tokens, right_polarity = claims[j]
                if not cls._claims_comparable(left_tokens, right_tokens):
                    continue
                comparable_pairs += 1
                if left_polarity != right_polarity:
                    conflicting_pairs += 1
                    conflict_indexes.update({i, j})
                else:
                    aligned_indexes.update({i, j})

        if comparable_pairs == 0:
            return "mixed", False, len(claims), 0

        if conflicting_pairs > 0:
            conflicting_sources = len(conflict_indexes)
            supporting_sources = max(0, len(claims) - conflicting_sources)
            return "mixed", True, supporting_sources, conflicting_sources

        supporting_sources = len(aligned_indexes) if aligned_indexes else len(claims)
        return "agreement", False, supporting_sources, 0

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

    @classmethod
    def _topic_authority_score(cls, query: str, result) -> float:
        base = cls._authority_score(result)
        relevance = cls._result_relevance(query, result)
        host = cls._source_host(result)
        query_terms = cls._query_terms(query)

        topics = DOMAIN_TOPICS.get(host, set())
        topic_hits = len(query_terms & topics)
        topic_affinity = min(1.0, topic_hits / max(1, min(2, len(query_terms))))

        topicality = max(relevance, topic_affinity)
        return max(0.0, min(base * (0.45 + 0.55 * topicality), 1.0))

    @staticmethod
    def _query_terms(query: str) -> set[str]:
        terms = set(re.findall(r"[a-z0-9][a-z0-9_+.#-]*", query.lower()))
        return {term for term in terms if len(term) > 1 and term not in QUERY_STOPWORDS}

    @classmethod
    def _evaluate_evidence(
        cls,
        query: str,
        search: SearchResponse,
        *,
        candidate_count: int | None = None,
        relevant_candidate_count: int | None = None,
    ) -> EvidenceSummary:
        candidate_count = len(search.results) if candidate_count is None else candidate_count
        relevant_candidate_count = (
            len(search.results)
            if relevant_candidate_count is None
            else relevant_candidate_count
        )
        discarded = max(0, candidate_count - len(search.results))

        if not search.results:
            if relevant_candidate_count > 0:
                reason = (
                    "Retrieved results matched the query, but none contained enough "
                    "claim-level support to answer safely."
                )
            else:
                reason = "No retrieved result passed the minimum query-relevance threshold."

            return EvidenceSummary(
                decision="abstain",
                confidence=0.0,
                coverage=0.0,
                authority=0.0,
                relevance=0.0,
                independent_sources=0,
                relevant_evidence_count=0,
                discarded_results=discarded,
                agreement="insufficient",
                conflict_detected=False,
                supporting_sources=0,
                conflicting_sources=0,
                reasons=[reason],
            )

        query_terms = cls._query_terms(query)
        covered: set[str] = set()
        authorities: list[float] = []
        relevances: list[float] = []
        hosts: set[str] = set()

        for result in search.results:
            haystack = f"{result.title} {result.snippet}".lower()
            covered.update(term for term in query_terms if term in haystack)
            relevances.append(cls._result_relevance(query, result))
            authorities.append(cls._topic_authority_score(query, result))
            host = cls._source_host(result)
            if host:
                hosts.add(host)

        coverage = 1.0 if not query_terms else len(covered) / len(query_terms)
        relevance = sum(relevances) / len(relevances) if relevances else 0.0
        authority = sum(authorities) / len(authorities) if authorities else 0.0
        independent_sources = len(hosts) if hosts else len(
            {r.source for r in search.results if r.source}
        )

        source_strength = min(independent_sources, 3) / 3.0
        evidence_depth = min(len(search.results), 3) / 3.0
        agreement, conflict_detected, supporting_sources, conflicting_sources = (
            cls._agreement_summary(query, search.results)
        )
        confidence = (
            0.38 * coverage
            + 0.27 * relevance
            + 0.20 * authority
            + 0.10 * source_strength
            + 0.05 * evidence_depth
        )
        confidence = max(0.0, min(confidence, 1.0))

        strong_single_source_factual = (
            not cls._is_judgment_query(query)
            and len(search.results) == 1
            and coverage >= 0.85
            and relevance >= 0.80
            and authority >= 0.90
        )

        reasons: list[str] = []
        if coverage < 0.50:
            reasons.append("Relevant passages cover too little of the query.")
        if relevance < 0.35:
            reasons.append("Retrieved evidence has weak direct query relevance.")
        if authority < 0.60:
            reasons.append("Sources are not sufficiently authoritative for this specific query.")
        if independent_sources < 2 and not strong_single_source_factual:
            reasons.append("Evidence comes from fewer than two independent relevant sources.")
        if conflict_detected:
            reasons.append(
                "Potentially conflicting claims were detected across comparable relevant sources."
            )
        elif agreement == "agreement":
            reasons.append("Comparable relevant sources provide mutually consistent support.")
        elif agreement == "mixed":
            reasons.append(
                "Relevant sources discuss different aspects, so cross-source agreement is limited."
            )
        if discarded:
            reasons.append(f"Discarded {discarded} weak or lower-ranked retrieval candidates.")

        if (
            len(search.results) == 0
            or coverage < 0.30
            or relevance < 0.22
            or confidence < 0.48
        ):
            decision = "abstain"
        elif conflict_detected:
            decision = "answer_with_caveat"
        elif strong_single_source_factual:
            decision = "answer"
        elif (
            confidence < 0.76
            or coverage < 0.70
            or relevance < 0.45
            or independent_sources < 2
        ):
            decision = "answer_with_caveat"
        else:
            decision = "answer"

        if not reasons:
            reasons.append(
                "Relevant evidence has sufficient coverage, topical authority, and source diversity."
            )

        return EvidenceSummary(
            decision=decision,
            confidence=round(confidence, 3),
            coverage=round(coverage, 3),
            authority=round(authority, 3),
            relevance=round(relevance, 3),
            independent_sources=independent_sources,
            relevant_evidence_count=len(search.results),
            discarded_results=discarded,
            agreement=agreement,
            conflict_detected=conflict_detected,
            supporting_sources=supporting_sources,
            conflicting_sources=conflicting_sources,
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

    @classmethod
    def _extractive_answer(cls, query: str, results) -> str:
        candidates: list[tuple[float, str, int]] = []
        for source_idx, result in enumerate(results, start=1):
            for score, sentence in cls._supporting_sentences(query, result):
                candidates.append((score, sentence, source_idx))

        candidates.sort(key=lambda item: item[0], reverse=True)
        selected: list[str] = []
        seen = set()

        for _score, sentence, source_idx in candidates:
            key = re.sub(r"\s+", " ", sentence.lower())[:140]
            if key in seen:
                continue
            seen.add(key)
            selected.append(f"{sentence} [{source_idx}]")
            if len(selected) == 3:
                break

        if not selected:
            return (
                "The indexed sources matched the query, but Nexus could not identify "
                "a sentence with enough direct support to produce a grounded answer."
            )
        return " ".join(selected)



answer_service = AnswerService()
