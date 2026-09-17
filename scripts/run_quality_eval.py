#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, math, statistics, sys, time
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

def load_cases(path):
    out=[]
    for n,line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(),1):
        if line.strip():
            try: out.append(json.loads(line))
            except json.JSONDecodeError as e: raise SystemExit(f"{path}:{n}: {e}")
    return out

def domain_of(url):
    host=(urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host

def domain_gain(domain, case):
    if domain in case.get("preferred_domains",[]): return 2
    if domain in case.get("acceptable_domains",[]): return 1
    return 0

def reciprocal_rank(domains, case):
    if not (case.get("preferred_domains") or case.get("acceptable_domains")): return None
    for i,d in enumerate(domains,1):
        if domain_gain(d,case)>0: return 1.0/i
    return 0.0

def recall_at_k(domains, case, k=5):
    gold=set(case.get("preferred_domains",[]))|set(case.get("acceptable_domains",[]))
    if not gold: return None
    return len(gold & set(domains[:k]))/len(gold)

def dcg(gains):
    return sum(g/math.log2(i+2) for i,g in enumerate(gains))

def ndcg_at_k(domains, case, k=5):
    preferred=list(dict.fromkeys(case.get("preferred_domains",[])))
    acceptable=[
        d for d in dict.fromkeys(case.get("acceptable_domains",[]))
        if d not in set(preferred)
    ]
    ideal=[2]*len(preferred)+[1]*len(acceptable)
    if not ideal: return None

    # A judged domain can earn relevance credit only once. Multiple citations
    # from the same domain must not inflate DCG above the ideal ranking.
    seen=set()
    actual=[]
    for d in domains[:k]:
        if d in seen:
            actual.append(0)
            continue
        seen.add(d)
        actual.append(domain_gain(d,case))

    ideal=sorted(ideal,reverse=True)[:k]
    den=dcg(ideal)
    return dcg(actual)/den if den else None

def decision_ok(case,response):
    return response.get("evidence",{}).get("decision") in case.get("expected_decision",[])

def citation_rule_ok(case,response):
    rule=case.get("must_cite")
    if rule is None: return True
    has=bool(response.get("citations"))
    return has if rule else not has

def post_json(url,payload,timeout):
    req=Request(url,data=json.dumps(payload).encode(),headers={"Content-Type":"application/json"},method="POST")
    t=time.perf_counter()
    try:
        with urlopen(req,timeout=timeout) as resp: raw=resp.read().decode()
    except HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode(errors='replace')[:500]}") from e
    except URLError as e: raise RuntimeError(str(e)) from e
    return json.loads(raw),(time.perf_counter()-t)*1000

def mean(values):
    vals=[v for v in values if v is not None]
    return statistics.mean(vals) if vals else None

def pct(n,d): return round(100*n/d,2) if d else 0.0

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--base-url",default="http://localhost:8000")
    p.add_argument("--dataset",default="eval/technical_search_v1.jsonl")
    p.add_argument("--top-k",type=int,default=5)
    p.add_argument("--timeout",type=float,default=20)
    p.add_argument("--category",action="append")
    p.add_argument("--limit",type=int)
    p.add_argument("--output",default="eval/latest_report.json")
    p.add_argument("--markdown",default="eval/latest_report.md")
    a=p.parse_args()
    cases=load_cases(a.dataset)
    if a.category: cases=[c for c in cases if c["category"] in set(a.category)]
    if a.limit is not None: cases=cases[:a.limit]
    rows=[]; failures=0
    for i,c in enumerate(cases,1):
        try:
            r,wall=post_json(a.base_url.rstrip("/")+"/api/ask",{"query":c["query"],"top_k":a.top_k},a.timeout)
            domains=[domain_of(x.get("url","")) for x in r.get("citations",[])]
            row={"id":c["id"],"category":c["category"],"query":c["query"],
                 "decision":r.get("evidence",{}).get("decision"),
                 "decision_ok":decision_ok(c,r),"citation_rule_ok":citation_rule_ok(c,r),
                 "domains":domains,"rr":reciprocal_rank(domains,c),
                 "recall_at_k":recall_at_k(domains,c,a.top_k),"ndcg_at_k":ndcg_at_k(domains,c,a.top_k),
                 "retrieval_ms":r.get("retrieval_ms"),"generation_ms":r.get("generation_ms"),
                 "wall_ms":round(wall,3),"agreement":r.get("evidence",{}).get("agreement"),
                 "conflict_detected":r.get("evidence",{}).get("conflict_detected"),
                 "reasons":r.get("evidence",{}).get("reasons",[]),"answer":r.get("answer",""),"error":None}
        except Exception as e:
            failures+=1
            row={"id":c["id"],"category":c["category"],"query":c["query"],"decision":None,
                 "decision_ok":False,"citation_rule_ok":False,"domains":[],"rr":None,"recall_at_k":None,
                 "ndcg_at_k":None,"retrieval_ms":None,"generation_ms":None,"wall_ms":None,
                 "agreement":None,"conflict_detected":None,"reasons":[],"answer":"","error":str(e)}
        rows.append(row)
        ok=row["decision_ok"] and row["citation_rule_ok"]
        print(f"[{i:02d}/{len(cases):02d}] {'PASS' if ok else 'FAIL':4} {c['id']}: {row['decision']}")
    successful=[r for r in rows if not r["error"]]
    judged=[r for r in successful if r["rr"] is not None]
    cats={}
    for cat in sorted({r["category"] for r in rows}):
        g=[r for r in rows if r["category"]==cat and not r["error"]]
        cats[cat]={"cases":len([r for r in rows if r["category"]==cat]),
                   "behavior_pass_rate":pct(sum(1 for r in g if r["decision_ok"] and r["citation_rule_ok"]),len(g)),
                   "mrr":round(mean([r["rr"] for r in g]) or 0,4),
                   "recall_at_k":round(mean([r["recall_at_k"] for r in g]) or 0,4),
                   "ndcg_at_k":round(mean([r["ndcg_at_k"] for r in g]) or 0,4)}
    behavior=sum(1 for r in successful if r["decision_ok"] and r["citation_rule_ok"])
    report={"dataset":a.dataset,"base_url":a.base_url,"top_k":a.top_k,"cases":len(rows),
            "successful_requests":len(successful),"request_failures":failures,
            "behavior":{"combined_pass_rate":pct(behavior,len(successful))},
            "retrieval":{"judged_cases":len(judged),"mrr":round(mean([r["rr"] for r in judged]) or 0,4),
                         "recall_at_k":round(mean([r["recall_at_k"] for r in judged]) or 0,4),
                         "ndcg_at_k":round(mean([r["ndcg_at_k"] for r in judged]) or 0,4),
                         "note":"Domain-level proxy: preferred gain=2, acceptable gain=1."},
            "latency":{"retrieval_p50_ms":round(statistics.median([r["retrieval_ms"] for r in successful if r["retrieval_ms"] is not None]),3) if any(r["retrieval_ms"] is not None for r in successful) else None},
            "decisions":dict(Counter(r["decision"] or "error" for r in rows)),
            "by_category":cats,"results":rows}
    Path(a.output).parent.mkdir(parents=True,exist_ok=True)
    Path(a.output).write_text(json.dumps(report,indent=2),encoding="utf-8")
    md=["# Nexus Quality Evaluation","",f"- Cases: {report['cases']}",f"- Behavior pass: {report['behavior']['combined_pass_rate']}%",
        f"- MRR: {report['retrieval']['mrr']}",f"- Recall@{a.top_k}: {report['retrieval']['recall_at_k']}",
        f"- nDCG@{a.top_k}: {report['retrieval']['ndcg_at_k']}","",
        "| Category | Cases | Behavior pass | MRR | Recall@K | nDCG@K |","|---|---:|---:|---:|---:|---:|"]
    for cat,s in cats.items():
        md.append(f"| {cat} | {s['cases']} | {s['behavior_pass_rate']}% | {s['mrr']} | {s['recall_at_k']} | {s['ndcg_at_k']} |")
    md += ["","## Failures","","| ID | Category | Decision | Domains | Reason |","|---|---|---|---|---|"]
    bad=[r for r in rows if r["error"] or not(r["decision_ok"] and r["citation_rule_ok"])]
    if not bad: md.append("| - | - | - | - | None |")
    for r in bad:
        reason=(r["error"] or "; ".join(r["reasons"]) or "Expectation mismatch").replace("|","\\|")
        md.append(f"| {r['id']} | {r['category']} | {r['decision']} | {', '.join(r['domains']) or '-'} | {reason} |")
    Path(a.markdown).write_text("\n".join(md)+"\n",encoding="utf-8")
    print(f"\nBehavior pass: {report['behavior']['combined_pass_rate']}%")
    print(f"MRR: {report['retrieval']['mrr']}  Recall@{a.top_k}: {report['retrieval']['recall_at_k']}  nDCG@{a.top_k}: {report['retrieval']['ndcg_at_k']}")
    print(f"Reports: {a.output}, {a.markdown}")
    return 2 if failures else 0

if __name__=="__main__": sys.exit(main())
