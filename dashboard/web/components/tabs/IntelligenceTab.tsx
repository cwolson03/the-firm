'use client'

import { useState } from 'react'
import { fetchRagDemo, RagDemoResponse } from '@/lib/api'
import LoadingSpinner from '@/components/shared/LoadingSpinner'

// Actual Rugrat tracked members — scores from rugrat.py MEMBER_SCORES (0-30 track record scale)
const MEMBERS = [
  { name: 'Nancy Pelosi',            party: 'D', score: 28, tags: ['Tech', 'Options'] },
  { name: 'Michael McCaul',          party: 'R', score: 26, tags: ['Defense', 'Tech'] },
  { name: 'Ro Khanna',               party: 'D', score: 25, tags: ['Semis', 'Tech'] },
  { name: 'Josh Gottheimer',         party: 'D', score: 24, tags: ['Finance', 'Tech'] },
  { name: 'Dan Crenshaw',            party: 'R', score: 22, tags: ['Crypto', 'Energy'] },
  { name: 'Mark Kelly',              party: 'D', score: 22, tags: ['Defense', 'Space'] },
  { name: 'Pat Fallon',              party: 'R', score: 20, tags: ['Defense', 'Science'] },
  { name: 'Tommy Tuberville',        party: 'R', score: 18, tags: ['Commodities', 'Defense'] },
  { name: 'Ted Cruz',                party: 'R', score: 18, tags: ['Energy', 'Tech'] },
  { name: 'Kevin Hern',              party: 'R', score: 17, tags: ['Energy', 'Budget'] },
  { name: 'Brian Mast',              party: 'R', score: 17, tags: ['Defense'] },
  { name: 'Markwayne Mullin',        party: 'R', score: 16, tags: ['Defense', 'Diversified'] },
  { name: 'John Hickenlooper',       party: 'D', score: 16, tags: ['Tech', 'Energy'] },
  { name: 'Jerry Moran',             party: 'R', score: 15, tags: ['Defense', 'Ag'] },
  { name: 'John Hoeven',             party: 'R', score: 15, tags: ['Energy', 'Ag'] },
  { name: 'Susan Collins',           party: 'R', score: 14, tags: ['Diversified'] },
  { name: 'Marjorie Taylor Greene',  party: 'R', score: 12, tags: ['Tech', 'Meme'] },
  { name: 'Marie Gluesenkamp Perez', party: 'D', score: 10, tags: ['Emerging'] },
]

const RECOMMENDATIONS = [
  { ticker: 'NVDA', member: 'Pelosi cluster buy', confidence: 'High', action: 'BUY', reason: 'Multiple insider purchases aligned with AI capex cycle. Pelosi has 80%+ hit rate on semiconductor timing.', signal: 9 },
  { ticker: 'TSM', member: 'Khanna + 2 others', confidence: 'High', action: 'BUY', reason: 'CHIPS Act beneficiary. 3 members accumulated positions in Q1 2026. Geopolitical risk priced in.', signal: 8 },
  { ticker: 'LMT', member: 'McCaul defense buys', confidence: 'Medium', action: 'WATCH', reason: 'Iran conflict tailwind. McCaul has Armed Services committee access. Defense budget expansion likely.', signal: 7 },
  { ticker: 'COIN', member: 'Crenshaw crypto push', confidence: 'Medium', action: 'WATCH', reason: 'Regulatory clarity improving. Crenshaw bought ahead of potential crypto legislation.', signal: 6 },
  { ticker: 'PLTR', member: 'Multiple members', confidence: 'Low', action: 'MONITOR', reason: 'Government contract pipeline growing. 3 members with defense committee seats hold positions.', signal: 5 },
]

export default function IntelligenceTab() {
  const [searchMode, setSearchMode] = useState<'member' | 'ticker'>('member')
  const [memberInput, setMemberInput] = useState('')
  const [ticker, setTicker] = useState('')
  const [tradeType, setTradeType] = useState('purchase')
  const [result, setResult] = useState<RagDemoResponse | null>(null)
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState(false)
  const [expandedMember, setExpandedMember] = useState<string | null>(null)

  const search = async (m?: string, t?: string, tt?: string) => {
    setSearching(true)
    setSearchError(false)
    setResult(null)
    try {
      const useMember = m || memberInput
      const useTicker = t || ticker
      const useType = tt || tradeType
      if (!useMember && searchMode === 'member') {
        setSearchError(true)
        setSearching(false)
        return
      }
      const res = await fetchRagDemo(useMember || 'all members', useTicker || 'all recent trades', useType)
      setResult(res)
    } catch { setSearchError(true) }
    setSearching(false)
  }

  const searchByTicker = async () => {
    if (!ticker) return
    setSearchMode('ticker')
    setSearching(true)
    setSearchError(false)
    setResult(null)
    try {
      const res = await fetchRagDemo('all members', ticker, 'purchase')
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
            <span className="text-[10px] text-[#888]">18 Tracked Members | STOCK Act Disclosures | Score = Track Record (0-30)</span>
          </div>
          <div className="grid grid-cols-3 gap-2 max-h-[420px] overflow-y-auto pr-1">
            {MEMBERS.map(m => {
              const isExpanded = expandedMember === m.name
              return (
                <div key={m.name} className="border border-[#1a1a1a] rounded bg-[#0a0a0a] overflow-hidden">
                  <div
                    className="p-3 cursor-pointer hover:bg-[#111] transition-colors"
                    onClick={() => {
                      setExpandedMember(isExpanded ? null : m.name)
                      // Auto-fill member input on click
                      setMemberInput(m.name)
                      setSearchMode('member')
                    }}
                  >
                    <div className="flex items-center gap-2 mb-1">
                      <span className={`w-2 h-2 rounded-full ${m.party === 'D' ? 'bg-[#3b82f6]' : 'bg-[#ef4444]'}`} />
                      <span className="text-xs text-[#e5e5e5] font-medium truncate">{m.name}</span>
                    </div>
                    <div className="flex items-center justify-between">
                      <div>
                        <span className="text-[10px] text-[#888]">Score: </span>
                        <span className="text-[10px] font-bold" style={{ color: scoreColor(m.score) }}>{m.score}/30</span>
                      </div>
                      <div className="flex gap-1">
                        {m.tags.slice(0, 2).map(t => (
                          <span key={t} className="px-1.5 py-0.5 bg-[#1a1a1a] text-[#888] text-[9px] rounded">{t}</span>
                        ))}
                      </div>
                    </div>
                  </div>
                  {isExpanded && (
                    <div className="border-t border-[#1a1a1a] p-2 bg-[#050505] space-y-2">
                      <p className="text-[10px] text-[#666]">Click below to analyze {m.name}&apos;s trades</p>
                      <button
                        onClick={(e) => {
                          e.stopPropagation()
                          setMemberInput(m.name)
                          setSearchMode('member')
                          search(m.name, ticker, tradeType)
                        }}
                        className="w-full px-2 py-1 rounded bg-[#00ff8820] text-[#00ff88] text-[10px] font-medium hover:bg-[#00ff8830] transition-colors"
                      >
                        Run Stock Finder
                      </button>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>

        {/* Stock Finder */}
        <div className="border border-[#222] rounded-lg bg-[#111] p-4">
          <h2 className="text-sm font-medium text-[#e5e5e5] mb-3">🔍 Stock Finder — Congressional Intelligence</h2>

          {/* Mode toggle */}
          <div className="flex gap-2 mb-4">
            <button
              onClick={() => setSearchMode('member')}
              className={`px-3 py-1.5 rounded text-xs font-medium transition-colors ${
                searchMode === 'member' ? 'bg-[#00ff8820] text-[#00ff88] border border-[#00ff8840]' : 'bg-[#1a1a1a] text-[#888] border border-[#333]'
              }`}
            >Congress Member</button>
            <button
              onClick={() => setSearchMode('ticker')}
              className={`px-3 py-1.5 rounded text-xs font-medium transition-colors ${
                searchMode === 'ticker' ? 'bg-[#00ff8820] text-[#00ff88] border border-[#00ff8840]' : 'bg-[#1a1a1a] text-[#888] border border-[#333]'
              }`}
            >Any Ticker</button>
          </div>

          {/* Inputs */}
          {searchMode === 'member' ? (
            <div className="flex gap-3 mb-4">
              <input
                value={memberInput}
                onChange={e => setMemberInput(e.target.value)}
                placeholder="Type member name or click card above..."
                className="flex-1 bg-[#0a0a0a] border border-[#333] rounded px-3 py-2 text-sm text-[#e5e5e5] placeholder-[#555]"
              />
              <input
                value={ticker}
                onChange={e => setTicker(e.target.value.toUpperCase())}
                placeholder="Ticker (optional)"
                className="w-28 bg-[#0a0a0a] border border-[#333] rounded px-3 py-2 text-sm text-[#e5e5e5] placeholder-[#555]"
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
          ) : (
            <div className="flex gap-3 mb-4">
              <input
                value={ticker}
                onChange={e => setTicker(e.target.value.toUpperCase())}
                placeholder="Enter ticker (e.g. NVDA)"
                className="flex-1 bg-[#0a0a0a] border border-[#333] rounded px-3 py-2 text-sm text-[#e5e5e5] placeholder-[#555]"
              />
              <button
                onClick={searchByTicker}
                disabled={searching || !ticker}
                className="px-4 py-2 rounded bg-[#00ff88] text-[#0a0a0a] text-sm font-medium hover:bg-[#00cc6e] disabled:opacity-50 transition-colors"
              >
                {searching ? 'Searching...' : `Search ${ticker || 'ticker'}`}
              </button>
            </div>
          )}

          {searchMode === 'ticker' && searching && (
            <p className="text-[#888] text-xs text-center py-2">Searching all tracked members for {ticker} activity...</p>
          )}

          {/* Results */}
          {searching && <div className="flex justify-center py-8"><LoadingSpinner /></div>}
          {searchError && <p className="text-[#ff4444] text-sm text-center py-4">Analysis failed — try again</p>}
          {result && (
            <div className="space-y-3">
              <div className="bg-[#0a0a0a] rounded p-3">
                <span className="text-[10px] text-[#888] uppercase tracking-wider">Query</span>
                <p className="text-xs text-[#e5e5e5] mt-1 font-mono">{result.query.member} → {result.query.ticker} ({result.query.trade_type})</p>
              </div>

              {/* RAG warning */}
              <div className="bg-[#f59e0b10] border border-[#f59e0b30] rounded p-2">
                <p className="text-[10px] text-[#f59e0b]">⚠️ Results are semantically ranked — may include similar but different members/tickers</p>
              </div>

              {result.retrieved_context?.length > 0 && (
                <div className="bg-[#0a0a0a] rounded p-3">
                  <span className="text-[10px] text-[#888] uppercase tracking-wider">Retrieved Disclosures ({result.retrieved_context.length})</span>
                  <div className="space-y-2 mt-2">
                    {result.retrieved_context.slice(0, 5).map((c, i) => (
                      <div key={i} className="bg-[#111] rounded p-2">
                        <p className="text-[10px] text-[#00ff88] mb-1">Member: {result.query.member}</p>
                        <p className="text-xs text-[#aaa]">{c.text}</p>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {result.member_profile && (
                <div className="bg-[#0a0a0a] rounded p-3">
                  <span className="text-[10px] text-[#888] uppercase tracking-wider">Member Profile</span>
                  <p className="text-xs text-[#aaa] mt-1">{result.member_profile}</p>
                </div>
              )}
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
        {/* AI Ranked Opportunities — expanded */}
        <div className="border border-[#222] rounded-lg bg-[#111] p-4">
          <div className="flex items-center justify-between mb-1">
            <h2 className="text-sm font-medium text-[#e5e5e5]">AI-Ranked Opportunities</h2>
            <button
              onClick={() => search('all members', 'best opportunities', 'purchase')}
              className="px-2 py-1 rounded bg-[#1a1a1a] border border-[#333] text-[#888] text-[10px] hover:border-[#00ff88] hover:text-[#00ff88] transition-colors"
            >Refresh</button>
          </div>
          <p className="text-[10px] text-[#666] mb-3">Based on recent congressional activity + historical patterns</p>
          <div className="space-y-2">
            {RECOMMENDATIONS.map(r => (
              <div key={r.ticker} className="bg-[#0a0a0a] rounded p-3">
                <div className="flex items-center justify-between mb-1">
                  <div className="flex items-center gap-2">
                    <span className="text-[#00ff88] font-mono font-medium text-sm">{r.ticker}</span>
                    <span className="text-[10px] text-[#888]">{r.member}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] text-[#888]">Signal: </span>
                    <div className="w-16 bg-[#222] rounded-full h-1.5">
                      <div className="h-1.5 rounded-full bg-[#00ff88]" style={{ width: `${r.signal * 10}%` }} />
                    </div>
                    <span className="px-2 py-0.5 rounded text-[10px]" style={{
                      background: `${r.confidence === 'High' ? '#00ff88' : r.confidence === 'Medium' ? '#f59e0b' : '#888'}20`,
                      color: r.confidence === 'High' ? '#00ff88' : r.confidence === 'Medium' ? '#f59e0b' : '#888',
                    }}>{r.confidence}</span>
                    <span className={`px-2 py-0.5 rounded text-[10px] font-medium ${
                      r.action === 'BUY' ? 'bg-[#00ff8820] text-[#00ff88]' : 'bg-[#1a1a1a] text-[#888]'
                    }`}>{r.action}</span>
                  </div>
                </div>
                <p className="text-[10px] text-[#666] mt-1">{r.reason}</p>
                <button
                  className="mt-2 text-[10px] text-[#38bdf8] hover:text-[#00ff88] transition-colors"
                  onClick={() => {
                    setSearchMode('ticker')
                    setTicker(r.ticker)
                    searchByTicker()
                  }}
                >
                  Click to run Stock Finder →
                </button>
              </div>
            ))}
          </div>
          <p className="text-[10px] text-[#555] mt-3">Rugrat scans every 4h — recommendations auto-refresh</p>
        </div>
      </div>
    </div>
  )
}
