from __future__ import annotations

import re
import time

import httpx

from ..config import settings
from ..models import AskResponse, Citation, SearchResponse

SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


class AnswerService:
    async def answer_from_search(
        self, query: str, search: SearchResponse, *, force_extractive: bool = False
    ) -> AskResponse:
        citations = [
            Citation(index=i, title=r.title, url=r.url, document_id=r.id)
            for i, r in enumerate(search.results, start=1)
        ]

        if not search.results:
            return AskResponse(
                query=query,
                answer="I could not find enough indexed evidence to answer that question.",
                citations=[],
                retrieval_ms=search.took_ms,
                generation_ms=0.0,
                model="retrieval-only",
                grounded=True,
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
        return AskResponse(
            query=query,
            answer=answer,
            citations=citations,
            retrieval_ms=search.took_ms,
            generation_ms=round(generation_ms, 3),
            model=model,
            grounded=True,
            plan=search.plan,
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
