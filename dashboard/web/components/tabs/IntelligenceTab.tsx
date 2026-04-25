'use client'

import { useState } from 'react'
import { fetchRagDemo, RagDemoResponse } from '@/lib/api'
import LoadingSpinner from '@/components/shared/LoadingSpinner'

const MEMBERS = [
  { name: 'Nancy Pelosi', party: 'D', score: 30, tags: ['Tech', 'Semiconductors'] },
  { name: 'Dan Crenshaw', party: 'R', score: 28, tags: ['Defense', 'Crypto'] },
  { name: 'Ro Khanna', party: 'D', score: 28, tags: ['Tech', 'AI'] },
  { name: 'Michael McCaul', party: 'R', score: 27, tags: ['Defense', 'China'] },
  { name: 'Tommy Tuberville', party: 'R', score: 27, tags: ['Finance', 'Defense'] },
  { name: 'Marjorie Taylor Greene', party: 'R', score: 26, tags: ['Energy', 'Tech'] },
  { name: 'Josh Gottheimer', party: 'D', score: 26, tags: ['Finance', 'Tech'] },
  { name: 'Mark Green', party: 'R', score: 25, tags: ['Defense', 'Cyber'] },
  { name: 'French Hill', party: 'R', score: 25, tags: ['Banking', 'Crypto'] },
  { name: 'Pat Fallon', party: 'R', score: 24, tags: ['Defense', 'Real Estate'] },
  { name: 'Pete Sessions', party: 'R', score: 24, tags: ['Tech', 'Telecom'] },
  { name: 'John Curtis', party: 'R', score: 23, tags: ['Energy', 'Tech'] },
  { name: 'Kevin Hern', party: 'R', score: 23, tags: ['Energy', 'QSR'] },
  { name: 'Marie Gluesenkamp Perez', party: 'D', score: 22, tags: ['Small Biz'] },
  { name: 'Daniel Goldman', party: 'D', score: 22, tags: ['Finance', 'Real Estate'] },
  { name: 'Debbie Wasserman Schultz', party: 'D', score: 21, tags: ['Healthcare'] },
  { name: 'Virginia Foxx', party: 'R', score: 20, tags: ['Education', 'Finance'] },
  { name: 'Zach Nunn', party: 'R', score: 19, tags: ['Cyber', 'Defense'] },
]

const PRESETS = [
  { label: 'Pelosi/NVDA', member: 'Nancy Pelosi', ticker: 'NVDA', type: 'purchase' },
  { label: 'Khanna/TSM', member: 'Ro Khanna', ticker: 'TSM', type: 'purchase' },
  { label: 'Crenshaw/COIN', member: 'Dan Crenshaw', ticker: 'COIN', type: 'purchase' },
  { label: 'McCaul/defense', member: 'Michael McCaul', ticker: 'LMT', type: 'purchase' },
]

const RECOMMENDATIONS = [
  { ticker: 'NVDA', member: 'Pelosi cluster buy', confidence: 'High', action: 'BUY' },
  { ticker: 'TSM', member: 'Khanna + 2 others', confidence: 'High', action: 'BUY' },
  { ticker: 'LMT', member: 'McCaul defense buys', confidence: 'Medium', action: 'WATCH' },
  { ticker: 'COIN', member: 'Crenshaw crypto push', confidence: 'Medium', action: 'WATCH' },
  { ticker: 'PLTR', member: 'Multiple members', confidence: 'Low', action: 'MONITOR' },
]

export default function IntelligenceTab() {
  const [member, setMember] = useState('Nancy Pelosi')
  const [ticker, setTicker] = useState('NVDA')
  const [tradeType, setTradeType] = useState('purchase')
  const [result, setResult] = useState<RagDemoResponse | null>(null)
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState(false)

  const search = async (m?: string, t?: string, tt?: string) => {
    setSearching(true)
    setSearchError(false)
    setResult(null)
    try {
      const res = await fetchRagDemo(m || member, t || ticker, tt || tradeType)
      setResult(res)
    } catch { setSearchError(true) }
    setSearching(false)
  }

  const scoreColor = (s: number) => s >= 25 ? '#00ff88' : s >= 20 ? '#f59e0b' : '#666'
  const confColor = (c: string) => c === 'high' ? '#00ff88' : c === 'medium' ? '#f59e0b' : '#ff4444'

  return (
    <div className="flex gap-6">
      {/* Left 55% */}
      <div className="flex-[55] space-y-6 min-w-0">
        {/* Congressional watchlist */}
        <div className="border border-[#222] rounded-lg bg-[#111] p-4">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-sm font-medium text-[#e5e5e5]">Congressional Watchlist</h2>
            <span className="text-[10px] text-[#888]">18 Tracked Members | STOCK Act Disclosures</span>
          </div>
          <div className="grid grid-cols-3 gap-2 max-h-[350px] overflow-y-auto pr-1">
            {MEMBERS.map(m => (
              <div key={m.name} className="border border-[#1a1a1a] rounded bg-[#0a0a0a] p-3">
                <div className="flex items-center gap-2 mb-1">
                  <span className={`w-2 h-2 rounded-full ${m.party === 'D' ? 'bg-[#3b82f6]' : 'bg-[#ef4444]'}`} />
                  <span className="text-xs text-[#e5e5e5] font-medium truncate">{m.name}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-[10px] font-mono font-bold" style={{ color: scoreColor(m.score) }}>{m.score}/30</span>
                  <div className="flex gap-1">
                    {m.tags.slice(0, 2).map(t => (
                      <span key={t} className="px-1.5 py-0.5 bg-[#1a1a1a] text-[#888] text-[9px] rounded">{t}</span>
                    ))}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Stock Finder */}
        <div className="border border-[#222] rounded-lg bg-[#111] p-4">
          <h2 className="text-sm font-medium text-[#e5e5e5] mb-3">🔍 Stock Finder — Congressional Intelligence</h2>
          
          {/* Presets */}
          <div className="flex gap-2 mb-4">
            {PRESETS.map(p => (
              <button
                key={p.label}
                onClick={() => { setMember(p.member); setTicker(p.ticker); setTradeType(p.type); search(p.member, p.ticker, p.type) }}
                className="px-3 py-1 rounded bg-[#1a1a1a] border border-[#333] text-[#888] text-xs hover:border-[#00ff88] hover:text-[#00ff88] transition-colors"
              >
                {p.label}
              </button>
            ))}
          </div>

          {/* Inputs */}
          <div className="flex gap-3 mb-4">
            <select
              value={member}
              onChange={e => setMember(e.target.value)}
              className="flex-1 bg-[#0a0a0a] border border-[#333] rounded px-3 py-2 text-sm text-[#e5e5e5]"
            >
              {MEMBERS.map(m => <option key={m.name} value={m.name}>{m.name}</option>)}
            </select>
            <input
              value={ticker}
              onChange={e => setTicker(e.target.value.toUpperCase())}
              placeholder="Ticker"
              className="w-24 bg-[#0a0a0a] border border-[#333] rounded px-3 py-2 text-sm text-[#e5e5e5]"
            />
            <select
              value={tradeType}
              onChange={e => setTradeType(e.target.value)}
              className="w-32 bg-[#0a0a0a] border border-[#333] rounded px-3 py-2 text-sm text-[#e5e5e5]"
            >
              <option value="purchase">Purchase</option>
              <option value="sale">Sale</option>
              <option value="exchange">Exchange</option>
            </select>
            <button
              onClick={() => search()}
              disabled={searching}
              className="px-4 py-2 rounded bg-[#00ff88] text-[#0a0a0a] text-sm font-medium hover:bg-[#00cc6e] disabled:opacity-50 transition-colors"
            >
              {searching ? 'Analyzing...' : 'Analyze'}
            </button>
          </div>

          {/* Results */}
          {searching && <div className="flex justify-center py-8"><LoadingSpinner /></div>}
          {searchError && <p className="text-[#ff4444] text-sm text-center py-4">Analysis failed — try again</p>}
          {result && (
            <div className="space-y-3">
              {/* Query */}
              <div className="bg-[#0a0a0a] rounded p-3">
                <span className="text-[10px] text-[#888] uppercase tracking-wider">Query</span>
                <p className="text-xs text-[#e5e5e5] mt-1 font-mono">{result.query.member} → {result.query.ticker} ({result.query.trade_type})</p>
              </div>
              {/* Retrieved */}
              {result.retrieved_context?.length > 0 && (
                <div className="bg-[#0a0a0a] rounded p-3">
                  <span className="text-[10px] text-[#888] uppercase tracking-wider">Retrieved Disclosures ({result.retrieved_context.length})</span>
                  <div className="space-y-2 mt-2">
                    {result.retrieved_context.slice(0, 3).map((c, i) => (
                      <p key={i} className="text-xs text-[#aaa] bg-[#111] rounded p-2">{c.text}</p>
                    ))}
                  </div>
                </div>
              )}
              {/* Profile */}
              {result.member_profile && (
                <div className="bg-[#0a0a0a] rounded p-3">
                  <span className="text-[10px] text-[#888] uppercase tracking-wider">Member Profile</span>
                  <p className="text-xs text-[#aaa] mt-1">{result.member_profile}</p>
                </div>
              )}
              {/* LLM Analysis */}
              <div className="bg-[#0a0a0a] rounded p-3">
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-[10px] text-[#888] uppercase tracking-wider">LLM Analysis</span>
                  <span className="px-2 py-0.5 rounded text-[10px] bg-[#1a1a1a] text-[#888]">{result.llm_model}</span>
                  <span className="px-2 py-0.5 rounded text-[10px]" style={{ background: `${confColor(result.llm_confidence)}20`, color: confColor(result.llm_confidence) }}>
                    {result.llm_confidence}
                  </span>
                  <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${result.go ? 'bg-[#00ff8820] text-[#00ff88]' : 'bg-[#ff444420] text-[#ff4444]'}`}>
                    {result.go ? 'GO' : 'NO-GO'}
                  </span>
                </div>
                <pre className="text-xs text-[#aaa] whitespace-pre-wrap">{result.llm_reasoning}</pre>
                {result.risks?.length > 0 && (
                  <div className="mt-2 pt-2 border-t border-[#222]">
                    <span className="text-[10px] text-[#ff4444]">RISKS:</span>
                    <ul className="list-disc list-inside text-xs text-[#888] mt-1">
                      {result.risks.map((r, i) => <li key={i}>{r}</li>)}
                    </ul>
                  </div>
                )}
                <div className="mt-2 text-[10px] text-[#555]">Completed in {(result.latency_ms / 1000).toFixed(1)}s</div>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Right 45% */}
      <div className="flex-[45] space-y-6 min-w-0">
        {/* LLM Recommendations */}
        <div className="border border-[#222] rounded-lg bg-[#111] p-4">
          <h2 className="text-sm font-medium text-[#e5e5e5] mb-1">AI-Ranked Opportunities</h2>
          <p className="text-[10px] text-[#666] mb-3">Based on recent congressional activity + historical patterns</p>
          <div className="space-y-2">
            {RECOMMENDATIONS.map(r => (
              <div key={r.ticker} className="flex items-center justify-between bg-[#0a0a0a] rounded p-3">
                <div>
                  <span className="text-[#00ff88] font-mono font-medium text-sm">{r.ticker}</span>
                  <p className="text-[10px] text-[#888] mt-0.5">{r.member}</p>
                </div>
                <div className="flex items-center gap-2">
                  <span className="px-2 py-0.5 rounded text-[10px]" style={{
                    background: `${r.confidence === 'High' ? '#00ff88' : r.confidence === 'Medium' ? '#f59e0b' : '#888'}20`,
                    color: r.confidence === 'High' ? '#00ff88' : r.confidence === 'Medium' ? '#f59e0b' : '#888',
                  }}>{r.confidence}</span>
                  <span className={`px-2 py-0.5 rounded text-[10px] font-medium ${
                    r.action === 'BUY' ? 'bg-[#00ff8820] text-[#00ff88]' : 'bg-[#1a1a1a] text-[#888]'
                  }`}>{r.action}</span>
                </div>
              </div>
            ))}
          </div>
          <p className="text-[10px] text-[#555] mt-3">Refreshed by Rugrat every 4hrs</p>
        </div>

        {/* Market Research */}
        <div className="border border-[#222] rounded-lg bg-[#111] p-4">
          <h2 className="text-sm font-medium text-[#e5e5e5] mb-2">Mark Hanna — Macro Intelligence</h2>
          <div className="bg-[#0a0a0a] rounded p-3 mb-3">
            <p className="text-xs text-[#888]">This week:</p>
            <p className="text-sm text-[#e5e5e5] mt-1">Royalty Streaming Companies (WPM, RGLD)</p>
            <p className="text-[10px] text-[#555] mt-2">Full brief in #the-crucible</p>
          </div>
          <button className="px-3 py-1.5 rounded bg-[#1a1a1a] border border-[#333] text-[#888] text-xs hover:border-[#00ff88] transition-colors cursor-default">
            Request Analysis
          </button>
        </div>
      </div>
    </div>
  )
}
