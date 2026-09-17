#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.models import SearchResult  # noqa: E402
from app.services.answer_service import (  # noqa: E402
    AnswerService,
    MIN_RESULT_RELEVANCE,
    RELATIVE_RELEVANCE_FLOOR,
    SENTENCE_RE,
)


def load_cases(path: str) -> list[dict]:
    cases = []
    for line_no, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            cases.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{path}:{line_no}: invalid JSON: {exc}") from exc
    return cases


def domain_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def get_json(url: str, timeout: float) -> dict:
    try:
        with urlopen(Request(url, method="GET"), timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GET HTTP {exc.code}: {raw[:500]}") from exc
    except URLError as exc:
        raise RuntimeError(str(exc)) from exc


def post_json(url: str, payload: dict, timeout: float) -> dict:
    req = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"POST HTTP {exc.code}: {raw[:500]}") from exc
    except URLError as exc:
        raise RuntimeError(str(exc)) from exc


def normalized_sentences(snippet: str) -> list[str]:
    return [
        sentence.strip()
        for sentence in SENTENCE_RE.split((snippet or "").replace("…", " "))
        if sentence.strip()
    ]


def compute_relative_floor(query: str, results: list[SearchResult]) -> tuple[float, float]:
    relevances = [AnswerService._result_relevance(query, result) for result in results]
    eligible = [score for score in relevances if score >= MIN_RESULT_RELEVANCE]
    best = max(eligible) if eligible else 0.0
    relative_floor = (
        max(MIN_RESULT_RELEVANCE, best * RELATIVE_RELEVANCE_FLOOR)
        if eligible
        else MIN_RESULT_RELEVANCE
    )
    return best, relative_floor


def stage_trace(
    query: str,
    result: SearchResult,
    *,
    raw_rank: int,
    relative_floor: float,
) -> dict:
    svc = AnswerService()
    relevance = svc._result_relevance(query, result)
    minimum_relevance_pass = relevance >= MIN_RESULT_RELEVANCE
    relative_relevance_pass = minimum_relevance_pass and relevance >= relative_floor

    identifier_terms = svc._explicit_identifier_terms(query)
    haystack = f"{result.title} {result.snippet}".lower()
    identifier_pass = not identifier_terms or all(term in haystack for term in identifier_terms)

    relevance_stage_pass = relative_relevance_pass and identifier_pass

    sentences = normalized_sentences(result.snippet or "")
    sentence_scores = [
        {
            "sentence": sentence,
            "length": len(sentence),
            "support_score": round(svc._sentence_support_score(query, result, sentence), 4),
        }
        for sentence in sentences
    ]
    support_threshold = 0.34 if svc._is_judgment_query(query) else 0.30
    supporting = [
        item
        for item in sentence_scores
        if item["length"] >= 35 and item["support_score"] >= support_threshold
    ]
    support_pass = bool(supporting)

    definition_query = svc._is_definition_query(query)
    definition_subject_terms = sorted(svc._definition_subject_terms(query))
    definition_checks = [
        {
            "sentence": sentence,
            "defines_subject": svc._sentence_defines_subject(query, sentence),
        }
        for sentence in sentences
        if len(sentence) >= 20
    ]
    definition_pass = (
        True
        if not definition_query
        else any(item["defines_subject"] for item in definition_checks)
    )

    authority = svc._authority_score(result)
    topical_authority = svc._topic_authority_score(query, result)

    if not minimum_relevance_pass:
        final_stage = "pruned_minimum_relevance"
    elif not relative_relevance_pass:
        final_stage = "pruned_relative_relevance"
    elif not identifier_pass:
        final_stage = "pruned_identifier_grounding"
    elif not support_pass:
        final_stage = "pruned_sentence_support"
    elif not definition_pass:
        final_stage = "pruned_definition_answerability"
    else:
        final_stage = "survives_local_evidence_pruning"

    return {
        "raw_rank": raw_rank,
        "document_id": result.id,
        "title": result.title,
        "url": result.url,
        "domain": domain_of(result.url or ""),
        "source": result.source,
        "snippet": result.snippet,
        "retrieval": {
            "score": result.score,
            "bm25_score": result.bm25_score,
            "semantic_score": result.semantic_score,
        },
        "relevance": {
            "score": round(relevance, 4),
            "minimum_threshold": MIN_RESULT_RELEVANCE,
            "relative_threshold": round(relative_floor, 4),
            "minimum_pass": minimum_relevance_pass,
            "relative_pass": relative_relevance_pass,
        },
        "identifier": {
            "terms": sorted(identifier_terms),
            "pass": identifier_pass,
        },
        "support": {
            "threshold": support_threshold,
            "pass": support_pass,
            "sentences": sentence_scores,
            "supporting_sentences": supporting,
        },
        "definition": {
            "is_definition_query": definition_query,
            "subject_terms": definition_subject_terms,
            "pass": definition_pass,
            "sentence_checks": definition_checks,
        },
        "authority": {
            "base": round(authority, 4),
            "topical": round(topical_authority, 4),
        },
        "final_stage": final_stage,
    }


def summarize_case(case: dict, traces: list[dict], ask: dict) -> dict:
    judged = set(case.get("preferred_domains", []) + case.get("acceptable_domains", []))
    authoritative = [trace for trace in traces if trace["domain"] in judged] if judged else []

    stage_counts: dict[str, int] = {}
    for trace in authoritative:
        stage_counts[trace["final_stage"]] = stage_counts.get(trace["final_stage"], 0) + 1

    first_authoritative = authoritative[0] if authoritative else None
    return {
        "id": case["id"],
        "category": case["category"],
        "query": case["query"],
        "judged_domains": sorted(judged),
        "ask_decision": ask.get("evidence", {}).get("decision"),
        "ask_reasons": ask.get("evidence", {}).get("reasons", []),
        "citation_domains": [domain_of(c.get("url", "")) for c in ask.get("citations", [])],
        "authoritative_candidates": len(authoritative),
        "authoritative_stage_counts": stage_counts,
        "first_authoritative_stage": first_authoritative["final_stage"] if first_authoritative else None,
        "first_authoritative_rank": first_authoritative["raw_rank"] if first_authoritative else None,
        "traces": traces,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Trace Nexus candidates through relevance, identifier, support, definition, and authority stages."
    )
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--dataset", default="eval/technical_search_v1.jsonl")
    parser.add_argument("--search-k", type=int, default=20)
    parser.add_argument("--ask-k", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--category", action="append")
    parser.add_argument("--case-id", action="append")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--output", default="eval/latest_support_trace.json")
    parser.add_argument("--markdown", default="eval/latest_support_trace.md")
    args = parser.parse_args()

    cases = load_cases(args.dataset)
    if args.category:
        wanted = set(args.category)
        cases = [case for case in cases if case.get("category") in wanted]
    if args.case_id:
        wanted_ids = set(args.case_id)
        cases = [case for case in cases if case.get("id") in wanted_ids]
    if args.limit is not None:
        cases = cases[: args.limit]

    base = args.base_url.rstrip("/")
    rows = []
    failures = 0

    for index, case in enumerate(cases, start=1):
        try:
            search_url = base + "/api/search?" + urlencode(
                {"q": case["query"], "mode": "auto", "top_k": args.search_k}
            )
            search_payload = get_json(search_url, args.timeout)
            ask_payload = post_json(
                base + "/api/ask",
                {"query": case["query"], "top_k": args.ask_k},
                args.timeout,
            )

            results = [SearchResult.model_validate(item) for item in search_payload.get("results", [])]
            _best, relative_floor = compute_relative_floor(case["query"], results)
            traces = [
                stage_trace(
                    case["query"],
                    result,
                    raw_rank=rank,
                    relative_floor=relative_floor,
                )
                for rank, result in enumerate(results, start=1)
            ]
            row = summarize_case(case, traces, ask_payload)
            row["error"] = None
        except Exception as exc:
            failures += 1
            row = {
                "id": case["id"],
                "category": case["category"],
                "query": case["query"],
                "judged_domains": case.get("preferred_domains", []) + case.get("acceptable_domains", []),
                "ask_decision": None,
                "ask_reasons": [],
                "citation_domains": [],
                "authoritative_candidates": 0,
                "authoritative_stage_counts": {},
                "first_authoritative_stage": None,
                "first_authoritative_rank": None,
                "traces": [],
                "error": str(exc),
            }

        rows.append(row)
        print(
            f"[{index:02d}/{len(cases):02d}] {case['id']}: "
            f"rank={row['first_authoritative_rank'] or '-'} "
            f"stage={row['first_authoritative_stage'] or '-'} "
            f"ask={row['ask_decision']}"
        )

    aggregate: dict[str, int] = {}
    for row in rows:
        if row["first_authoritative_stage"]:
            aggregate[row["first_authoritative_stage"]] = (
                aggregate.get(row["first_authoritative_stage"], 0) + 1
            )

    report = {
        "base_url": args.base_url,
        "dataset": args.dataset,
        "cases": len(rows),
        "request_failures": failures,
        "first_authoritative_stage_counts": aggregate,
        "results": rows,
    }
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(report, indent=2), encoding="utf-8")

    md = [
        "# Nexus Support-Stage Trace",
        "",
        f"- Cases: {len(rows)}",
        f"- Request failures: {failures}",
        "",
        "## First authoritative candidate outcome",
        "",
        "| Stage | Count |",
        "|---|---:|",
    ]
    for stage, count in sorted(aggregate.items()):
        md.append(f"| {stage} | {count} |")

    md += [
        "",
        "## Per-query trace",
        "",
        "| ID | Rank | First authoritative stage | Ask decision | Citation domains |",
        "|---|---:|---|---|---|",
    ]
    for row in rows:
        md.append(
            f"| {row['id']} | {row['first_authoritative_rank'] or '-'} | "
            f"{row['first_authoritative_stage'] or '-'} | "
            f"{row['ask_decision'] or '-'} | "
            f"{', '.join(row['citation_domains']) or '-'} |"
        )

    md += [
        "",
        "Full sentence scores, snippets, identifier checks, definition checks,",
        "and topical-authority scores are stored in the JSON report.",
        "",
        "Important: this script replays the local AnswerService heuristics against",
        "results returned by the target deployment. Run it from the same commit as",
        "the deployed service when using it for exact stage diagnosis.",
    ]
    Path(args.markdown).write_text("\n".join(md) + "\n", encoding="utf-8")

    print("")
    print("First authoritative candidate stage counts:")
    for stage, count in sorted(aggregate.items()):
        print(f"  {stage}: {count}")
    print(f"Reports: {args.output}, {args.markdown}")
    return 2 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
