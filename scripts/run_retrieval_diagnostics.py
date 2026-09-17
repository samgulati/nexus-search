#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


def load_cases(path: str) -> list[dict]:
    cases = []
    for line_no, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            cases.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{path}:{line_no}: invalid JSON: {exc}") from exc
    return cases


def domain_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def judged_domains(case: dict) -> list[str]:
    return list(dict.fromkeys(case.get("preferred_domains", []) + case.get("acceptable_domains", [])))


def get_json(url: str, timeout: float) -> tuple[dict, float]:
    req = Request(url, method="GET")
    started = time.perf_counter()
    try:
        with urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GET HTTP {exc.code}: {raw[:500]}") from exc
    except URLError as exc:
        raise RuntimeError(str(exc)) from exc
    return json.loads(raw), (time.perf_counter() - started) * 1000


def post_json(url: str, payload: dict, timeout: float) -> tuple[dict, float]:
    req = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"POST HTTP {exc.code}: {raw[:500]}") from exc
    except URLError as exc:
        raise RuntimeError(str(exc)) from exc
    return json.loads(raw), (time.perf_counter() - started) * 1000


def first_judged_rank(search_results: list[dict], case: dict) -> int | None:
    gold = set(judged_domains(case))
    if not gold:
        return None
    for rank, result in enumerate(search_results, start=1):
        if domain_of(result.get("url", "")) in gold:
            return rank
    return None


def diagnose(case: dict, search_response: dict, ask_response: dict, search_k: int) -> str:
    gold = set(judged_domains(case))
    search_domains = [domain_of(r.get("url", "")) for r in search_response.get("results", [])]
    citation_domains = [domain_of(c.get("url", "")) for c in ask_response.get("citations", [])]
    decision = ask_response.get("evidence", {}).get("decision")

    if not gold:
        return "behavior_only_abstain" if decision == "abstain" else "behavior_only_answered"

    found_in_search = bool(gold & set(search_domains))
    found_in_citations = bool(gold & set(citation_domains))

    if not found_in_search:
        return f"candidate_coverage_miss_top_{search_k}"
    if not found_in_citations:
        if decision == "abstain":
            return "retrieved_then_filtered_or_answerability_abstain"
        return "retrieved_but_not_selected_for_citation"
    if decision == "abstain":
        return "citation_present_but_abstained"
    return "authoritative_evidence_reached_answer"


def compact_result(result: dict, rank: int) -> dict:
    return {
        "rank": rank,
        "title": result.get("title"),
        "url": result.get("url"),
        "domain": domain_of(result.get("url", "")),
        "score": result.get("score"),
        "bm25_score": result.get("bm25_score"),
        "semantic_score": result.get("semantic_score"),
        "source": result.get("source"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose Nexus retrieval coverage and post-retrieval filtering.")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--dataset", default="eval/technical_search_v1.jsonl")
    parser.add_argument("--search-k", type=int, default=20)
    parser.add_argument("--ask-k", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--category", action="append")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--output", default="eval/latest_retrieval_diagnostics.json")
    parser.add_argument("--markdown", default="eval/latest_retrieval_diagnostics.md")
    args = parser.parse_args()

    cases = load_cases(args.dataset)
    if args.category:
        wanted = set(args.category)
        cases = [case for case in cases if case.get("category") in wanted]
    if args.limit is not None:
        cases = cases[: args.limit]

    base = args.base_url.rstrip("/")
    rows = []
    failures = 0

    for index, case in enumerate(cases, start=1):
        try:
            search_url = base + "/api/search?" + urlencode({"q": case["query"], "mode": "auto", "top_k": args.search_k})
            search_response, search_wall_ms = get_json(search_url, args.timeout)
            ask_response, ask_wall_ms = post_json(
                base + "/api/ask",
                {"query": case["query"], "top_k": args.ask_k},
                args.timeout,
            )

            raw_results = search_response.get("results", [])
            raw_domains = [domain_of(r.get("url", "")) for r in raw_results]
            citations = ask_response.get("citations", [])
            citation_domains = [domain_of(c.get("url", "")) for c in citations]
            gold = judged_domains(case)

            row = {
                "id": case["id"],
                "category": case["category"],
                "query": case["query"],
                "judged_domains": gold,
                "expected_domain_found_in_top_k": bool(set(gold) & set(raw_domains)) if gold else None,
                "expected_domain_first_rank": first_judged_rank(raw_results, case),
                "raw_search": [compact_result(r, i) for i, r in enumerate(raw_results, start=1)],
                "answer_decision": ask_response.get("evidence", {}).get("decision"),
                "answer_agreement": ask_response.get("evidence", {}).get("agreement"),
                "answer_reasons": ask_response.get("evidence", {}).get("reasons", []),
                "citation_domains": citation_domains,
                "citations": citations,
                "diagnosis": diagnose(case, search_response, ask_response, args.search_k),
                "search_wall_ms": round(search_wall_ms, 3),
                "ask_wall_ms": round(ask_wall_ms, 3),
                "error": None,
            }
        except Exception as exc:
            failures += 1
            row = {
                "id": case["id"],
                "category": case["category"],
                "query": case["query"],
                "judged_domains": judged_domains(case),
                "expected_domain_found_in_top_k": None,
                "expected_domain_first_rank": None,
                "raw_search": [],
                "answer_decision": None,
                "answer_agreement": None,
                "answer_reasons": [],
                "citation_domains": [],
                "citations": [],
                "diagnosis": "request_error",
                "search_wall_ms": None,
                "ask_wall_ms": None,
                "error": str(exc),
            }

        rows.append(row)
        rank_text = f"rank={row['expected_domain_first_rank']}" if row["expected_domain_first_rank"] else "rank=-"
        print(f"[{index:02d}/{len(cases):02d}] {case['id']}: {row['diagnosis']} {rank_text}")

    diagnosis_counts = Counter(row["diagnosis"] for row in rows)
    judged_rows = [r for r in rows if r["judged_domains"] and not r["error"]]
    coverage_hits = sum(1 for r in judged_rows if r["expected_domain_found_in_top_k"])
    ranks = [r["expected_domain_first_rank"] for r in judged_rows if r["expected_domain_first_rank"] is not None]

    report = {
        "dataset": args.dataset,
        "base_url": args.base_url,
        "search_k": args.search_k,
        "ask_k": args.ask_k,
        "cases": len(rows),
        "request_failures": failures,
        "judged_cases": len(judged_rows),
        "authoritative_domain_coverage_at_k": round(coverage_hits / len(judged_rows), 4) if judged_rows else None,
        "mean_first_authoritative_rank_when_found": round(sum(ranks) / len(ranks), 3) if ranks else None,
        "diagnosis_counts": dict(diagnosis_counts),
        "results": rows,
    }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")

    markdown = [
        "# Nexus Retrieval Diagnostics",
        "",
        f"- Cases: {report['cases']}",
        f"- Judged cases: {report['judged_cases']}",
        f"- Authoritative-domain coverage@{args.search_k}: {report['authoritative_domain_coverage_at_k']}",
        f"- Mean first authoritative rank when found: {report['mean_first_authoritative_rank_when_found']}",
        "",
        "## Diagnosis counts",
        "",
        "| Diagnosis | Count |",
        "|---|---:|",
    ]
    for name, count in sorted(diagnosis_counts.items()):
        markdown.append(f"| {name} | {count} |")

    markdown += [
        "",
        "## Judged-query diagnostics",
        "",
        "| ID | Category | Expected domain rank | Ask decision | Diagnosis | Citation domains |",
        "|---|---|---:|---|---|---|",
    ]
    for row in rows:
        if not row["judged_domains"]:
            continue
        rank_display = row["expected_domain_first_rank"] or "-"
        citations_display = ", ".join(row["citation_domains"]) or "-"
        markdown.append(
            f"| {row['id']} | {row['category']} | {rank_display} | "
            f"{row['answer_decision']} | {row['diagnosis']} | {citations_display} |"
        )

    markdown += [
        "",
        "## Interpretation",
        "",
        f"- `candidate_coverage_miss_top_{args.search_k}` means the expected authoritative domain did not appear in the raw top-{args.search_k}. It does not prove corpus absence; the source may be missing or ranked below K.",
        "- `retrieved_then_filtered_or_answerability_abstain` means authoritative evidence reached raw retrieval but did not survive to a final cited answer.",
        "- `retrieved_but_not_selected_for_citation` means authoritative evidence was retrieved, but another source was selected.",
        "- `authoritative_evidence_reached_answer` means the expected authoritative domain survived retrieval and citation selection.",
        "",
        "These are diagnostic labels, not passage-level relevance judgments.",
    ]

    md_path = Path(args.markdown)
    md_path.write_text("\n".join(markdown) + "\n", encoding="utf-8")

    print("")
    print(f"Authoritative-domain coverage@{args.search_k}: {report['authoritative_domain_coverage_at_k']}")
    print(f"Mean first authoritative rank: {report['mean_first_authoritative_rank_when_found']}")
    print("Diagnosis counts:")
    for name, count in sorted(diagnosis_counts.items()):
        print(f"  {name}: {count}")
    print(f"Reports: {output}, {md_path}")

    return 2 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
