import React, { useEffect, useMemo, useState } from 'react'
import { createRoot } from 'react-dom/client'
import {
  Activity, AlertTriangle, ArrowUpRight, BookOpen, Bot, Braces,
  CheckCircle2, Database, Gauge, Search, ShieldCheck, Sparkles, Timer,
  Waypoints, Zap
} from 'lucide-react'
import './styles.css'

const examples = [
  'How does Kafka handle retries and duplicate processing?',
  'Why combine BM25 with semantic search?',
  'How do distributed traces reduce incident diagnosis time?',
  'What is the transactional outbox pattern?'
]

function ms(v) {
  if (v == null) return '—'
  return `${Number(v).toFixed(v < 10 ? 2 : 1)} ms`
}

function pct(v) {
  if (v == null) return '—'
  return `${Math.round(Number(v) * 100)}%`
}

function App() {
  const [query, setQuery] = useState('')
  const [mode, setMode] = useState('auto')
  const [results, setResults] = useState([])
  const [answer, setAnswer] = useState(null)
  const [plan, setPlan] = useState(null)
  const [stats, setStats] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [lastSearchMs, setLastSearchMs] = useState(null)

  const citationMap = useMemo(() => {
    const map = new Map()
    answer?.citations?.forEach(c => map.set(c.index, c))
    return map
  }, [answer])

  async function loadStats() {
    try {
      const r = await fetch('/api/stats')
      if (r.ok) setStats(await r.json())
    } catch (_) {}
  }

  useEffect(() => { loadStats() }, [])

  async function runSearch(q = query) {
    const value = q.trim()
    if (!value) return
    setQuery(value)
    setLoading(true)
    setError('')
    setAnswer(null)
    setPlan(null)

    try {
      const [searchRes, askRes] = await Promise.all([
        fetch(`/api/search?q=${encodeURIComponent(value)}&mode=${mode}&top_k=8`),
        fetch('/api/ask', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ query: value, top_k: 5 })
        })
      ])

      if (!searchRes.ok || !askRes.ok) throw new Error('Search request failed')

      const searchData = await searchRes.json()
      const askData = await askRes.json()

      setResults(searchData.results || [])
      setLastSearchMs(searchData.took_ms)
      setAnswer(askData)
      setPlan(searchData.plan || askData.plan || null)
      loadStats()
    } catch (e) {
      setError(e.message || 'Something went wrong')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="app-shell">
      <div className="grid-glow" />
      <header className="nav">
        <div className="brand">
          <div className="brand-mark"><Waypoints size={19}/></div>
          <span>Nexus</span>
        </div>
        <div className="nav-meta">
          <span><span className="live-dot"/> live demo</span>
          <a href="/docs" target="_blank">API docs <ArrowUpRight size={14}/></a>
        </div>
      </header>

      <main>
        <section className="hero">
          <div className="eyebrow"><Sparkles size={14}/> adaptive distributed AI search</div>
          <h1>Search beyond keywords.<br/><span>Adapt under pressure.</span></h1>
          <p className="subhead">
            Nexus combines custom BM25, semantic retrieval, reciprocal-rank fusion and grounded answers —
            then changes its execution plan using live shard health, latency and request pressure.
          </p>

          <form className="search-box" onSubmit={(e) => { e.preventDefault(); runSearch() }}>
            <Search size={21} className="search-icon"/>
            <input
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder="Ask about distributed systems, search, RAG, reliability…"
            />
            <button disabled={loading}>{loading ? 'Searching…' : 'Search'}</button>
          </form>

          <div className="controls-row">
            <div className="segmented">
              {['auto','hybrid','lexical','semantic'].map(m => (
                <button
                  type="button"
                  key={m}
                  onClick={() => setMode(m)}
                  className={mode===m?'active':''}
                >
                  {m}
                </button>
              ))}
            </div>
            <div className="examples">
              {examples.slice(0,2).map((e,i) => (
                <button type="button" key={i} onClick={() => runSearch(e)}>{e}</button>
              ))}
            </div>
          </div>
        </section>

        <section className="metric-grid">
          <Metric icon={<Database size={17}/>} label="Indexed documents" value={stats?.documents ?? '—'} />
          <Metric icon={<Braces size={17}/>} label="Vocabulary" value={stats ? stats.vocabulary_terms.toLocaleString() : '—'} />
          <Metric icon={<Timer size={17}/>} label="p95 query latency" value={stats ? ms(stats.p95_search_ms) : '—'} />
          <Metric
            icon={<Activity size={17}/>}
            label={stats?.role === 'coordinator' ? 'Healthy shards' : 'Searches served'}
            value={stats?.role === 'coordinator' ? `${stats.healthy_shards}/${stats.shards}` : (stats?.searches ?? '—')}
          />
        </section>

        {error && <div className="error-card">{error}</div>}

        {plan && <AutopilotPanel plan={plan} />}

        {(loading || answer || results.length > 0) && (
          <section className="workspace">
            <div className="answer-panel">
              <div className="panel-title">
                <Bot size={18}/>
                <span>Grounded answer</span>
                <span className="pill"><ShieldCheck size={13}/> citation-aware</span>
              </div>
              {loading ? <Skeleton lines={5}/> : <>
                <p className="answer-text">{answer?.answer}</p>
                <div className="answer-footer">
                  <span><Zap size={14}/> {answer?.model}</span>
                  <span><Timer size={14}/> retrieve {ms(answer?.retrieval_ms)}</span>
                  {answer?.plan && (
                    <span><Gauge size={14}/> {answer.plan.tier} · {answer.plan.selected_mode}</span>
                  )}
                </div>
                <div className="citations">
                  {answer?.citations?.map(c => (
                    <a key={c.index} href={c.url || '#'} target="_blank" rel="noreferrer">
                      <span>[{c.index}]</span>{c.title}
                    </a>
                  ))}
                </div>
                {answer?.evidence && <EvidenceInspector evidence={answer.evidence} />}
              </>}
            </div>

            <div className="results-panel">
              <div className="panel-title">
                <Search size={18}/>
                <span>Ranked evidence</span>
                <span className="pill">{results.length} results · {ms(lastSearchMs)}</span>
              </div>
              {loading ? <Skeleton lines={7}/> : results.map((r, idx) => (
                <article className="result" key={r.id}>
                  <div className="result-rank">{String(idx+1).padStart(2,'0')}</div>
                  <div className="result-main">
                    <div className="result-source">
                      {r.source}{r.shard_id != null ? ` · shard ${r.shard_id}` : ''}
                    </div>
                    <h3>
                      {r.url
                        ? <a href={r.url} target="_blank" rel="noreferrer">{r.title} <ArrowUpRight size={14}/></a>
                        : r.title}
                    </h3>
                    <p>{r.snippet}</p>
                    <div className="score-row">
                      <span>BM25 {r.bm25_score.toFixed(2)}</span>
                      <span>semantic {r.semantic_score.toFixed(2)}</span>
                      <span>fused {r.score.toFixed(4)}</span>
                    </div>
                  </div>
                </article>
              ))}
            </div>
          </section>
        )}

        <section className="architecture">
          <div className="eyebrow"><Gauge size={14}/> adaptive architecture</div>
          <h2>One query. A live execution decision.</h2>
          <div className="flow">
            <FlowCard icon={<Search/>} title="Query" text="profile intent" />
            <div className="connector">→</div>
            <FlowCard icon={<Gauge/>} title="Autopilot" text="health · load · p95" />
            <div className="connector">→</div>
            <div className="parallel">
              <FlowCard icon={<BookOpen/>} title="BM25" text="lexical precision" />
              <FlowCard icon={<Sparkles/>} title="Semantic" text="meaning retrieval" />
            </div>
            <div className="connector">→</div>
            <FlowCard icon={<Waypoints/>} title="Global RRF" text="merge shard top-k" />
            <div className="connector">→</div>
            <FlowCard icon={<Bot/>} title="Answer" text="LLM or extractive" />
          </div>
        </section>
      </main>

      <footer>
        <span>Nexus Search Engine</span>
        <span>FastAPI · React · Kafka · PostgreSQL · OpenTelemetry · Kubernetes</span>
      </footer>
    </div>
  )
}

function EvidenceInspector({ evidence }) {
  const decision = evidence.decision || 'abstain'
  const isAbstain = decision === 'abstain'
  const isCaveat = decision === 'answer_with_caveat'

  return (
    <div className="evidence-inspector">
      <div className="evidence-head">
        <div>
          <div className="evidence-kicker"><ShieldCheck size={13}/> Evidence Inspector</div>
          <strong>Why Nexus {isAbstain ? 'abstained' : 'answered'}</strong>
        </div>
        <div className={`evidence-decision decision-${decision}`}>
          {isAbstain ? <AlertTriangle size={13}/> : <CheckCircle2 size={13}/>}
          {decision.replaceAll('_', ' ')}
        </div>
      </div>

      <div className="evidence-grid">
        <EvidenceMetric label="Confidence" value={pct(evidence.confidence)} />
        <EvidenceMetric label="Coverage" value={pct(evidence.coverage)} />
        <EvidenceMetric label="Relevance" value={pct(evidence.relevance)} />
        <EvidenceMetric label="Authority" value={pct(evidence.authority)} />
        <EvidenceMetric label="Sources" value={evidence.independent_sources} />
        <EvidenceMetric label="Evidence kept" value={evidence.relevant_evidence_count} />
        <EvidenceMetric label="Discarded" value={evidence.discarded_results} />
        <EvidenceMetric
          label="Agreement"
          value={evidence.conflict_detected ? 'potential conflict' : evidence.agreement}
          warn={evidence.conflict_detected || isCaveat}
        />
      </div>

      <div className="evidence-reasons">
        <span>Evidence trace</span>
        <ul>
          {(evidence.reasons || []).map((reason, i) => <li key={i}>{reason}</li>)}
        </ul>
      </div>
    </div>
  )
}

function EvidenceMetric({ label, value, warn = false }) {
  return (
    <div className={`evidence-metric ${warn ? 'warn' : ''}`}>
      <span>{label}</span>
      <strong>{value ?? '—'}</strong>
    </div>
  )
}

function AutopilotPanel({ plan }) {
  const tier = plan.tier || 'manual'
  const isAuto = plan.requested_mode === 'auto'

  return (
    <section className={`autopilot-panel tier-${tier}`}>
      <div className="autopilot-head">
        <div>
          <div className="autopilot-kicker"><Zap size={14}/> Adaptive Search Autopilot</div>
          <h2>{isAuto ? 'Nexus chose this execution plan' : 'Manual retrieval override active'}</h2>
        </div>
        <div className={`tier-badge tier-${tier}`}>{tier}</div>
      </div>

      <div className="autopilot-grid">
        <PlanMetric label="Selected mode" value={plan.selected_mode} />
        <PlanMetric label="Query profile" value={plan.query_profile?.replace('_', ' ')} />
        <PlanMetric label="Shard health" value={`${plan.healthy_shards}/${plan.total_shards}`} />
        <PlanMetric label="Process load" value={pct(plan.load_ratio)} />
        <PlanMetric label="Observed p95" value={ms(plan.observed_p95_ms)} />
        <PlanMetric label="Latency budget" value={ms(plan.latency_budget_ms)} />
        <PlanMetric label="Generation" value={plan.generation_allowed ? 'allowed' : 'extractive fallback'} />
        <PlanMetric label="Circuit pressure" value={`${plan.unavailable_circuits} unavailable`} />
      </div>

      <div className="autopilot-reasons">
        <span>Decision trace</span>
        <ul>
          {(plan.reasons || []).map((reason, i) => <li key={i}>{reason}</li>)}
        </ul>
      </div>
    </section>
  )
}

function PlanMetric({ label, value }) {
  return (
    <div className="plan-metric">
      <span>{label}</span>
      <strong>{value ?? '—'}</strong>
    </div>
  )
}

function Metric({icon,label,value}) {
  return <div className="metric"><div className="metric-icon">{icon}</div><div><span>{label}</span><strong>{value}</strong></div></div>
}

function FlowCard({icon,title,text}) {
  return <div className="flow-card"><div>{React.cloneElement(icon,{size:18})}</div><strong>{title}</strong><span>{text}</span></div>
}

function Skeleton({lines}) {
  return <div className="skeleton">{Array.from({length:lines}).map((_,i)=><div key={i} style={{width:`${92-(i%3)*12}%`}} />)}</div>
}

createRoot(document.getElementById('root')).render(<React.StrictMode><App/></React.StrictMode>)
