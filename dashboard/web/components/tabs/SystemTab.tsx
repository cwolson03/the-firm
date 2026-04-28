'use client'

import { useEffect, useState, useCallback } from 'react'
import { fetchStatus, fetchEval, fetchFiles, fetchFile, StatusResponse, EvalRecord, FileInfo } from '@/lib/api'
import LoadingSpinner from '@/components/shared/LoadingSpinner'
import ErrorState from '@/components/shared/ErrorState'

const FILE_STATUS: Record<string, { tag: string; color: string }> = {
  'economics.py': { tag: 'LIVE', color: '#00ff88' },
  'weather.py': { tag: 'LIVE', color: '#00ff88' },
  'sports.py': { tag: 'PAPER', color: '#f59e0b' },
  'congressional.py': { tag: 'LIVE', color: '#00ff88' },
  'options.py': { tag: 'LIVE', color: '#00ff88' },
  'supervisor.py': { tag: 'LIVE', color: '#00ff88' },
  'llm_client.py': { tag: 'LIVE', color: '#00ff88' },
  'rag_store.py': { tag: 'LIVE', color: '#00ff88' },
  'rag_ingest.py': { tag: 'LIVE', color: '#00ff88' },
  'eval_framework.py': { tag: 'LIVE', color: '#00ff88' },
  'firm.py': { tag: 'LIVE', color: '#00ff88' },
  'shared_context.py': { tag: 'LIVE', color: '#00ff88' },
  'weather_intel.py': { tag: 'IDLE', color: '#888' },
  'crypto.py': { tag: 'IDLE', color: '#888' },
}

const SERVICES = [
  { name: 'stratton-firm', key: 'firm', label: 'LIVE' },
  { name: 'stratton-api', key: 'api', label: 'LIVE' },
  { name: 'stratton-tunnel', key: 'tunnel', label: 'LIVE' },
  { name: 'Weather Bot', key: 'weather', label: 'LIVE', interval: '3m' },
  { name: 'Economics', key: 'economics', label: 'LIVE', interval: '120m' },
  { name: 'Intelligence', key: 'congressional', label: 'LIVE', interval: '240m' },
]

// Agent categorization for evals
const AGENT_CATEGORY: Record<string, string> = {
  economics: 'Economics', donnie: 'Economics', 'ECONOMICS': 'Economics',
  weather: 'Weather', 'weather-bot': 'Weather',
  sports: 'Sports', brad: 'Sports', BRAD: 'Sports',
  congressional: 'Intelligence', rugrat: 'Intelligence',
  options: 'Portfolio', jordan: 'Portfolio',
  supervisor: 'System',
  test: 'Economics',
}

function getAgentCategory(agent: string): string {
  return AGENT_CATEGORY[agent] || AGENT_CATEGORY[agent.toLowerCase()] || 'Economics'
}

function timeAgo(ts: number): string {
  const diff = (Date.now() / 1000) - ts
  if (diff < 60) return 'just now'
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

function parseEvalMarket(market: string): string {
  if (!market) return '—'
  if (market.includes('SHELTERCPI') && market.includes('T424')) return 'CPI Shelter > 4.24% (Apr 10)'
  if (market.includes('SHELTERCPI')) return 'CPI Shelter'
  if (market.includes('BTCD')) return 'BTC Daily Range (Apr)'
  if (market.includes('GDP') && market.includes('T2')) return 'Q1 GDP > 2.0% (Apr 30)'
  if (market.includes('GDP')) {
    const match = market.match(/T(\d+\.?\d*)/)
    return match ? `Q1 GDP > ${match[1]}% (Apr 30)` : 'Q1 GDP (Apr 30)'
  }
  if (market.includes('HORMUZNORM')) return 'Hormuz Normal by May 1'
  if (market.includes('USAIRANAGREEMENT')) return 'US-Iran Deal by May 1'
  if (market === 'TEST001') return 'Test Record'
  return market
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
  const [collapsedCategories, setCollapsedCategories] = useState<Record<string, boolean>>({})

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
    const ts = agent?.last_run_ts || (agent?.last_run ? new Date(agent.last_run).getTime() / 1000 : 0)
    const maxAge = svc.key === 'weather' ? 600 : svc.key === 'economics' ? 10800 : 18000
    return { ...svc, ok: ts > 0 && (now - ts < maxAge), lastCheck: ts ? timeAgo(ts) : 'no data' }
  })

  // Group evals by category
  const evalsByCategory: Record<string, EvalRecord[]> = { Economics: [], Weather: [], Sports: [] }
  evals.forEach(ev => {
    const cat = getAgentCategory(ev.agent)
    if (!evalsByCategory[cat]) evalsByCategory[cat] = []
    evalsByCategory[cat].push(ev)
  })

  const toggleCategory = (cat: string) => {
    setCollapsedCategories(prev => ({ ...prev, [cat]: !prev[cat] }))
  }

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
        {/* Performance Analytics - left 60% */}
        <div className="flex-[3] min-w-0">
          <div className="border border-[#222] rounded-lg bg-[#111] p-4">
            <h2 className="text-sm font-medium text-[#e5e5e5] mb-1">Trade Intelligence — Decision Quality</h2>
            <p className="text-[10px] text-[#666] mb-4">Process scores are independent of trade outcome — a 10/10 process can still lose. {evals.length} total records.</p>

            {/* Category sections */}
            {Object.entries(evalsByCategory).map(([category, catEvals]) => {
              const isCollapsed = collapsedCategories[category]
              const resolved = catEvals.filter(e => e.outcome === 'WIN' || e.outcome === 'LOSS')
              const wins = resolved.filter(e => e.outcome === 'WIN').length
              const winRate = resolved.length > 0 ? (wins / resolved.length * 100).toFixed(1) : '—'
              const avgProcess = catEvals.filter(e => e.llm_eval?.process_score != null)
              const avgScore = avgProcess.length > 0
                ? (avgProcess.reduce((s, e) => s + (e.llm_eval?.process_score || 0), 0) / avgProcess.length).toFixed(1)
                : '—'

              return (
                <div key={category} className="mb-4">
                  <div
                    className="flex items-center justify-between cursor-pointer hover:bg-[#1a1a1a] rounded px-2 py-1.5 -mx-2 transition-colors"
                    onClick={() => toggleCategory(category)}
                  >
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-[#e5e5e5] font-medium">{category}</span>
                      <span className="text-[10px] text-[#666]">{catEvals.length} records</span>
                      {resolved.length > 0 && (
                        <span className="text-[10px] text-[#888]">Win rate: {winRate}% | Avg process: {avgScore}/10</span>
                      )}
                    </div>
                    <span className="text-[#666] text-xs">{isCollapsed ? '▶' : '▼'}</span>
                  </div>

                  {!isCollapsed && (
                    <div className="space-y-2 mt-2">
                      {catEvals.length > 0 ? catEvals.map(ev => {
                        const score = ev.llm_eval?.process_score
                        const scoreColor = score != null ? (score >= 8 ? '#00ff88' : score >= 5 ? '#f59e0b' : '#ff4444') : '#666'
                        const isTest = ev.trade_id === 'TEST001'
                        const isPending = !ev.outcome || ev.outcome === 'PENDING'
                        const isExpanded = expanded === ev.trade_id

                        return (
                          <div key={ev.trade_id}>
                            <div
                              className="flex items-center gap-3 bg-[#0a0a0a] rounded p-3 cursor-pointer hover:bg-[#1a1a1a] transition-colors"
                              onClick={() => setExpanded(isExpanded ? null : ev.trade_id)}
                            >
                              <span className="text-xs text-[#888] flex-1 truncate">{parseEvalMarket(ev.market)}</span>
                              <span className="text-xs w-12 shrink-0">
                                {isPending ? (
                                  <span className="px-2 py-0.5 rounded text-[10px] bg-[#38bdf820] text-[#38bdf8]">⏳ Open</span>
                                ) : (
                                  <span className={ev.outcome === 'WIN' ? 'text-[#00ff88]' : 'text-[#ff4444]'}>{ev.outcome}</span>
                                )}
                              </span>
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
                      }) : (
                        <p className="text-[#666] text-xs py-3 text-center bg-[#0a0a0a] rounded">
                          {category} performance analytics will populate as trades resolve
                        </p>
                      )}
                    </div>
                  )}
                </div>
              )
            })}

            {!evals.length && (
              <p className="text-[#666] text-sm text-center py-6">Performance analytics active — scores populate as live trades resolve</p>
            )}
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
