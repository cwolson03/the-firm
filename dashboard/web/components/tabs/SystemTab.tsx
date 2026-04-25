'use client'

import { useEffect, useState, useCallback } from 'react'
import { fetchStatus, fetchEval, StatusResponse, EvalRecord } from '@/lib/api'
import LoadingSpinner from '@/components/shared/LoadingSpinner'
import ErrorState from '@/components/shared/ErrorState'

const FILES = [
  { name: 'kalshi.py', desc: 'Kalshi Execution Engine | Gate 6 LLM', tag: 'LIVE', color: '#00ff88' },
  { name: 'weather.py', desc: 'Temperature Market Scanner | 19 cities', tag: 'LIVE', color: '#00ff88' },
  { name: 'sports.py', desc: 'Sports Stink-Bid Strategy | S1/S2/S3', tag: 'PAPER', color: '#f59e0b' },
  { name: 'tracker.py', desc: 'Congressional Intelligence | RAG Pipeline', tag: 'LIVE', color: '#00ff88' },
  { name: 'options.py', desc: 'SPY 0DTE Desk | Target Monitor', tag: 'IDLE', color: '#888' },
  { name: 'analyst.py', desc: 'Macro Research | Weekly Brief', tag: 'LIVE', color: '#00ff88' },
  { name: 'supervisor.py', desc: 'System Health Monitor | 6 checks', tag: 'LIVE', color: '#00ff88' },
  { name: 'llm_client.py', desc: 'Multi-Model LLM | Grok + Claude + GPT-4o', tag: 'LIVE', color: '#00ff88' },
  { name: 'rag_store.py', desc: 'Vector Store | ChromaDB | 206 disclosures', tag: 'LIVE', color: '#00ff88' },
  { name: 'eval_framework.py', desc: 'Trade Evaluation | LLM Critique', tag: 'LIVE', color: '#00ff88' },
  { name: 'firm.py', desc: 'Master Orchestrator | 8 agents | systemd', tag: 'LIVE', color: '#00ff88' },
]

function timeAgo(ts: number): string {
  const diff = (Date.now() / 1000) - ts
  if (diff < 60) return 'just now'
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

export default function SystemTab() {
  const [status, setStatus] = useState<StatusResponse | null>(null)
  const [evals, setEvals] = useState<EvalRecord[]>([])
  const [expanded, setExpanded] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)

  const load = useCallback(async () => {
    try {
      const [s, e] = await Promise.all([fetchStatus(), fetchEval()])
      setStatus(s)
      setEvals(Array.isArray(e) ? e : [])
      setError(false)
    } catch { setError(true) }
    setLoading(false)
  }, [])

  useEffect(() => { load() }, [load])
  useEffect(() => {
    const iv = setInterval(load, 30000)
    return () => clearInterval(iv)
  }, [load])

  if (loading) return <div className="flex justify-center py-20"><LoadingSpinner size="lg" /></div>
  if (error || !status) return <ErrorState onRetry={load} />

  const agents = status.agents || {}
  const now = Date.now() / 1000

  const checks = [
    { label: 'stratton-firm.service', ok: status.service_running },
    { label: 'stratton-api.service', ok: true }, // if we got status, API is up
    { label: 'Donnie', ok: agents.donnie?.last_run_ts ? (now - agents.donnie.last_run_ts < 10800) : false, warn: true },
    { label: 'Weather', ok: agents.weather?.last_run_ts ? (now - agents.weather.last_run_ts < 600) : false, warn: true },
    { label: 'RAG Store', ok: (status.rag_stats?.disclosures ?? 0) > 0 },
    { label: 'Eval Framework', ok: status.eval_trades > 0, warn: true },
  ]

  return (
    <div className="space-y-6">
      {/* Health monitor */}
      <div className="grid grid-cols-6 gap-3">
        {checks.map(c => (
          <div key={c.label} className="border border-[#222] rounded-lg bg-[#111] p-3 text-center">
            <div className="text-2xl mb-1">{c.ok ? '✅' : c.warn ? '⚠️' : '❌'}</div>
            <p className="text-[10px] text-[#888] leading-tight">{c.label}</p>
          </div>
        ))}
      </div>

      <div className="flex gap-6">
        {/* Eval framework - left 60% */}
        <div className="flex-[3] min-w-0">
          <div className="border border-[#222] rounded-lg bg-[#111] p-4">
            <h2 className="text-sm font-medium text-[#e5e5e5] mb-1">Trade Evaluation — LLM Process Scoring</h2>
            <p className="text-[10px] text-[#666] mb-4">Every resolved trade gets an independent LLM critique. Win rate isn&apos;t enough — process quality matters.</p>
            
            <div className="space-y-2">
              {evals.map(ev => {
                const score = ev.llm_eval?.process_score
                const scoreColor = score != null ? (score >= 8 ? '#00ff88' : score >= 5 ? '#f59e0b' : '#ff4444') : '#666'
                const isTest = ev.trade_id === 'TEST001'
                const isExpanded = expanded === ev.trade_id

                return (
                  <div key={ev.trade_id}>
                    <div
                      className="flex items-center gap-3 bg-[#0a0a0a] rounded p-3 cursor-pointer hover:bg-[#1a1a1a] transition-colors"
                      onClick={() => setExpanded(isExpanded ? null : ev.trade_id)}
                    >
                      <span className="text-xs text-[#666] w-20 shrink-0">{ev.trade_id}</span>
                      <span className="text-xs text-[#888] w-16 shrink-0">{ev.agent}</span>
                      <span className="text-xs text-[#e5e5e5] flex-1 truncate">{ev.market}</span>
                      <span className={`text-xs w-12 ${ev.outcome === 'WIN' ? 'text-[#00ff88]' : ev.outcome === 'LOSS' ? 'text-[#ff4444]' : 'text-[#888]'}`}>{ev.outcome}</span>
                      {/* Score bar */}
                      <div className="w-24 shrink-0">
                        {score != null ? (
                          <div className="flex items-center gap-2">
                            <div className="flex-1 bg-[#222] rounded-full h-2">
                              <div className="h-2 rounded-full" style={{ width: `${score * 10}%`, background: scoreColor }} />
                            </div>
                            <span className="text-[10px] font-mono" style={{ color: scoreColor }}>{score}</span>
                          </div>
                        ) : <span className="text-[10px] text-[#666]">—</span>}
                      </div>
                      <span className="text-xs text-[#888] w-40 truncate">{ev.llm_eval?.lesson || '—'}</span>
                      {isTest && <span className="px-1.5 py-0.5 bg-[#f59e0b20] text-[#f59e0b] text-[9px] rounded">TEST</span>}
                    </div>
                    {isExpanded && ev.llm_eval && (
                      <div className="bg-[#0a0a0a] border border-[#1a1a1a] rounded mx-2 mt-1 p-3 space-y-2 text-xs">
                        <div><span className="text-[#888]">What worked:</span> <span className="text-[#e5e5e5]">{ev.llm_eval.what_worked}</span></div>
                        <div><span className="text-[#888]">Improve:</span> <span className="text-[#e5e5e5]">{ev.llm_eval.what_to_improve}</span></div>
                        <div><span className="text-[#888]">Avoid:</span> <span className="text-[#ff4444]">{ev.llm_eval.avoid_next_time}</span></div>
                        <div><span className="text-[#888]">Edge quality:</span> <span className="text-[#e5e5e5]">{ev.llm_eval.edge_quality}</span></div>
                      </div>
                    )}
                  </div>
                )
              })}
              {!evals.length && (
                <p className="text-[#666] text-sm text-center py-6">Eval framework active — scores populate as live trades resolve</p>
              )}
            </div>
          </div>
        </div>

        {/* File directory - right 40% */}
        <div className="flex-[2] min-w-0">
          <div className="border border-[#222] rounded-lg bg-[#111] p-4">
            <h2 className="text-sm font-medium text-[#e5e5e5] mb-3">The Firm — File Registry</h2>
            <div className="space-y-2">
              {FILES.map(f => (
                <div key={f.name} className="flex items-center gap-3 bg-[#0a0a0a] rounded p-2.5">
                  <span className="text-xs text-[#00ff88] font-mono w-32 shrink-0">{f.name}</span>
                  <span className="text-[10px] text-[#888] flex-1 truncate">{f.desc}</span>
                  <span className="px-1.5 py-0.5 rounded text-[9px] font-medium shrink-0" style={{ background: `${f.color}20`, color: f.color }}>{f.tag}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
