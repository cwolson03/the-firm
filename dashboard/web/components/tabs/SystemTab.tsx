'use client'

import { useEffect, useState, useCallback } from 'react'
import { fetchStatus, fetchEval, fetchFiles, fetchFile, StatusResponse, EvalRecord, FileInfo } from '@/lib/api'
import LoadingSpinner from '@/components/shared/LoadingSpinner'
import ErrorState from '@/components/shared/ErrorState'

const FILE_STATUS: Record<string, { tag: string; color: string }> = {
  'donnie_v2.py': { tag: 'LIVE', color: '#00ff88' },
  'weather.py': { tag: 'PAPER', color: '#f59e0b' },
  'brad.py': { tag: 'PAPER', color: '#f59e0b' },
  'rugrat.py': { tag: 'LIVE', color: '#00ff88' },
  'jordan.py': { tag: 'LIVE', color: '#00ff88' },
  'mark_hanna.py': { tag: 'IDLE', color: '#888' },
  'chester.py': { tag: 'IDLE', color: '#888' },
  'supervisor.py': { tag: 'LIVE', color: '#00ff88' },
  'llm_client.py': { tag: 'LIVE', color: '#00ff88' },
  'rag_store.py': { tag: 'LIVE', color: '#00ff88' },
  'rag_ingest.py': { tag: 'LIVE', color: '#00ff88' },
  'eval_framework.py': { tag: 'LIVE', color: '#00ff88' },
  'firm.py': { tag: 'LIVE', color: '#00ff88' },
  'shared_context.py': { tag: 'LIVE', color: '#00ff88' },
}

const SERVICES = [
  { name: 'stratton-firm', key: 'firm', label: 'LIVE' },
  { name: 'stratton-api', key: 'api', label: 'LIVE' },
  { name: 'stratton-tunnel', key: 'tunnel', label: 'LIVE' },
  { name: 'Weather Bot', key: 'weather', label: 'LIVE (paper)', interval: '3m' },
  { name: 'Donnie', key: 'donnie', label: 'LIVE', interval: '120m' },
  { name: 'Rugrat', key: 'rugrat', label: 'LIVE', interval: '240m' },
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
  const [files, setFiles] = useState<FileInfo[]>([])
  const [expanded, setExpanded] = useState<string | null>(null)
  const [viewingFile, setViewingFile] = useState<string | null>(null)
  const [fileContent, setFileContent] = useState<string>('')
  const [fileLoading, setFileLoading] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)

  const load = useCallback(async () => {
    try {
      const [s, e, f] = await Promise.all([fetchStatus(), fetchEval(), fetchFiles().catch(() => [])])
      setStatus(s)
      setEvals(Array.isArray(e) ? e : [])
      setFiles(f)
      setError(false)
    } catch { setError(true) }
    setLoading(false)
  }, [])

  useEffect(() => { load() }, [load])
  useEffect(() => {
    const iv = setInterval(load, 30000)
    return () => clearInterval(iv)
  }, [load])

  const openFile = async (name: string) => {
    if (viewingFile === name) { setViewingFile(null); return }
    setViewingFile(name)
    setFileLoading(true)
    try {
      const data = await fetchFile(name)
      setFileContent(data.content || '')
    } catch {
      setFileContent('// Failed to load file')
    }
    setFileLoading(false)
  }

  if (loading) return <div className="flex justify-center py-20"><LoadingSpinner size="lg" /></div>
  if (error || !status) return <ErrorState onRetry={load} />

  const agents = status.agents || {}
  const now = Date.now() / 1000

  const serviceStatus = SERVICES.map(svc => {
    if (svc.key === 'firm') return { ...svc, ok: status.service_running, lastCheck: 'now' }
    if (svc.key === 'api') return { ...svc, ok: true, lastCheck: 'now' }
    if (svc.key === 'tunnel') return { ...svc, ok: status.service_running, lastCheck: status.service_running ? 'active' : 'down' }
    const agent = agents[svc.key]
    // Support both numeric last_run_ts and ISO string last_run (log-sourced agents)
    const ts = agent?.last_run_ts || (agent?.last_run ? new Date(agent.last_run).getTime() / 1000 : 0)
    const maxAge = svc.key === 'weather' ? 600 : svc.key === 'donnie' ? 10800 : 18000
    return { ...svc, ok: ts > 0 && (now - ts < maxAge), lastCheck: ts ? timeAgo(ts) : 'no data' }
  })

  return (
    <div className="space-y-6">
      {/* Service Status Grid */}
      <div>
        <h2 className="text-sm font-medium text-[#888] mb-3">SERVICE HEALTH</h2>
        <div className="grid grid-cols-3 gap-3">
          {serviceStatus.map(svc => (
            <div key={svc.name} className="border border-[#222] rounded-lg bg-[#111] p-4">
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs text-[#e5e5e5] font-medium">{svc.name}</span>
                <span className={`w-2 h-2 rounded-full ${svc.ok ? 'bg-[#00ff88]' : 'bg-[#ff4444]'}`} />
              </div>
              <div className="flex items-center justify-between">
                <span className={`text-[10px] font-medium ${svc.ok ? 'text-[#00ff88]' : 'text-[#ff4444]'}`}>
                  {svc.ok ? svc.label : 'DOWN'}
                </span>
                <span className="text-[10px] text-[#666]">{svc.lastCheck}</span>
              </div>
              {svc.interval && <span className="text-[9px] text-[#555]">interval: {svc.interval}</span>}
            </div>
          ))}
        </div>
      </div>

      <div className="flex gap-6">
        {/* Eval framework - left 60% */}
        <div className="flex-[3] min-w-0">
          <div className="border border-[#222] rounded-lg bg-[#111] p-4">
            <h2 className="text-sm font-medium text-[#e5e5e5] mb-1">Trade Evaluation — LLM Process Scoring</h2>
            <p className="text-[10px] text-[#666] mb-4">Process scores are independent of trade outcome — a 10/10 process can still lose.</p>

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
                        {isTest && <p className="text-[#f59e0b] text-[10px] mb-2">Test Record — live trades will appear here as they resolve</p>}
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

        {/* File Registry - right 40% */}
        <div className="flex-[2] min-w-0">
          <div className="border border-[#222] rounded-lg bg-[#111] p-4">
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-medium text-[#e5e5e5]">The Firm — File Registry</h2>
              <span className="text-[10px] text-[#555]">Loaded from Atlas in real time</span>
            </div>
            <div className="space-y-1">
              {files.map(f => {
                const meta = FILE_STATUS[f.name] || { tag: 'UNKNOWN', color: '#666' }
                const isViewing = viewingFile === f.name
                return (
                  <div key={f.name}>
                    <div
                      className={`flex items-center gap-2 rounded p-2 cursor-pointer hover:bg-[#1a1a1a] transition-colors ${isViewing ? 'bg-[#1a1a1a]' : 'bg-[#0a0a0a]'}`}
                      onClick={() => openFile(f.name)}
                    >
                      <span className="text-xs text-[#00ff88] font-mono w-36 shrink-0 truncate">{f.name}</span>
                      <span className="text-[10px] text-[#666] flex-1">{f.lines} lines · {f.size_kb}KB</span>
                      <span className="px-1.5 py-0.5 rounded text-[9px] font-medium shrink-0" style={{ background: `${meta.color}20`, color: meta.color }}>{meta.tag}</span>
                    </div>
                    {isViewing && (
                      <div className="mx-1 mt-1 mb-2 rounded overflow-hidden">
                        {fileLoading ? (
                          <div className="flex justify-center py-4 bg-[#0a0a0a]"><LoadingSpinner size="sm" /></div>
                        ) : (
                          <div className="relative">
                            <div className="absolute top-2 right-2 text-[9px] text-[#555]">{f.lines} lines</div>
                            <pre className="text-[10px] text-[#aaa] bg-[#050505] p-3 overflow-x-auto max-h-[400px] overflow-y-auto leading-relaxed font-mono whitespace-pre">
                              {fileContent}
                            </pre>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )
              })}
              {files.length === 0 && (
                <p className="text-[#666] text-xs text-center py-4">No files loaded — check Atlas connection</p>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
