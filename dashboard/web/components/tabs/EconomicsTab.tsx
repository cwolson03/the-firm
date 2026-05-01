'use client'

import { useEffect, useState, useCallback } from 'react'
import { fetchPositions, fetchEval, fetchKalshiBalance, fetchActivity, PositionItem, EvalRecord, ActivityEntry } from '@/lib/api'
import DataTable from '@/components/shared/DataTable'
import PnlChart from '@/components/shared/PnlChart'
import LoadingSpinner from '@/components/shared/LoadingSpinner'
import ErrorState from '@/components/shared/ErrorState'

const UPCOMING_EVENTS = [
  {
    date: 'Apr 30',
    title: 'Q1 GDP Advance + PCE March',
    details: 'GDPNow: 1.24% | 9 positions open — these resolve today\nAll GDP NO positions expected to resolve in our favor',
  },
  {
    date: 'May 2',
    title: 'NFP April (Jobs Report)',
    details: 'First post-tariff jobs report — consensus ~130K\nWatch: payrolls below 100K and 150K buckets (sub-threshold edge expected)',
  },
  {
    date: 'May 13',
    title: 'CPI April',
    details: 'First full month with tariff pass-through priced in\nWatch: above-consensus CPI buckets (tariff inflation story)',
  },
  {
    date: 'Jun 18',
    title: 'FOMC Rate Decision',
    details: 'Fed rate cut 25bps — open position held\nModel: no cut expected until H2 2026',
  },
]

const TIMELINE = [
  { date: 'Apr 30', event: 'Q1 GDP Advance + PCE', status: '9 open positions resolve', active: true },
  { date: 'May 2', event: 'NFP April', status: 'watching', active: false },
  { date: 'May 13', event: 'CPI April', status: 'watching', active: false },
  { date: 'Jun 17', event: 'Fed Dot Plot', status: 'position held (KXDOTPLOT)', active: true },
]

function parseTicker(ticker: string, title: string): string {
  if (title && title !== ticker && !title.startsWith('KX')) return title
  return ticker
    .replace(/^KX/, '')
    .replace(/-26APR30/, ' (Apr 30)')
    .replace(/-26JUN/, ' (Jun)')
    .replace(/-26MAY/, ' (May)')
    .replace(/-27/, ' (2027)')
    .replace(/GDP/, 'Q1 GDP')
    .replace(/T(\d+\.\d+)/, ' > $1%')
    .replace(/PCE/, 'Core PCE')
    .replace(/FEDDECISION/, 'Fed Rate Decision')
    .replace(/DOTPLOT/, 'Fed Dot Plot 3.4%')
    .replace(/HORMUZNORM/, 'Hormuz Normal')
    .replace(/-C25/, ' Cut 25bps')
    .replace(/-C50/, ' Cut 50bps')
    .trim()
}

function parseEvalMarket(market: string): string {
  if (!market) return '—'
  // Known mappings
  if (market.includes('SHELTERCPI') && market.includes('T424')) return 'CPI Shelter > 4.24% (Apr 10)'
  if (market.includes('SHELTERCPI')) {
    const match = market.match(/T(\d+)\.?(\d*)/)
    if (match) {
      const val = match[2] ? `${match[1]}.${match[2]}` : match[1]
      return `CPI Shelter > ${Number(val) / 100}% (Apr 10)`
    }
    return 'CPI Shelter'
  }
  if (market.includes('BTCD')) return 'BTC Daily Range (Apr)'
  if (market.includes('GDP') && market.includes('T2')) return 'Q1 GDP > 2.0% (Apr 30)'
  if (market.includes('GDP')) {
    const match = market.match(/T(\d+\.?\d*)/)
    return match ? `Q1 GDP > ${match[1]}% (Apr 30)` : 'Q1 GDP (Apr 30)'
  }
  if (market.includes('HORMUZNORM')) return 'Hormuz Normal by May 1'
  if (market.includes('USAIRANAGREEMENT')) return 'US-Iran Deal by May 1'
  if (market === 'TEST001') return 'Test Record'
  // Generic parse
  return parseTicker(market, '')
}

function parseEvalDate(tradeId: string): string {
  // Try ISO date parse
  if (tradeId.match(/^\d{4}-\d{2}-\d{2}/)) {
    try {
      const d = new Date(tradeId)
      if (!isNaN(d.getTime())) return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
    } catch { /* fallback */ }
  }
  // Try to extract date from trade_id patterns
  const dateMatch = tradeId.match(/(\d{4})(\d{2})(\d{2})/)
  if (dateMatch) {
    try {
      const d = new Date(`${dateMatch[1]}-${dateMatch[2]}-${dateMatch[3]}`)
      if (!isNaN(d.getTime())) return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
    } catch { /* fallback */ }
  }
  return tradeId.slice(0, 12)
}

export default function EconomicsTab() {
  const [positions, setPositions] = useState<PositionItem[]>([])
  const [evals, setEvals] = useState<EvalRecord[]>([])
  const [balance, setBalance] = useState<number>(0)
  const [donnieSignals, setDonnieSignals] = useState<ActivityEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [evalFilter, setEvalFilter] = useState<'ALL' | 'WIN' | 'LOSS' | 'PENDING'>('ALL')
  const [evalPage, setEvalPage] = useState(0)

  const load = useCallback(async () => {
    try {
      const [p, e, b, signals] = await Promise.all([
        fetchPositions(), fetchEval(), fetchKalshiBalance().catch(() => ({ balance: 0 })),
        fetchActivity(100, 'economics').catch(() => [] as ActivityEntry[])
      ])
      // filter out weather positions (KXHIGH*) — those belong in the Weather tab
      setPositions(Array.isArray(p) ? (p as PositionItem[]).filter(pos => !pos.ticker.startsWith("KXHIGH")) : [])
      setEvals(Array.isArray(e) ? e : [])
      setBalance(b.balance)
      // Only surface meaningful signals — exclude internal EXEC/Scoring/debug noise
      const filtered = (Array.isArray(signals) ? signals : [])
        .filter(s => {
          const msg = s.message
          // Skip internal debug messages
          if (msg.startsWith('[EXEC]') || msg.startsWith('[Scoring]') || 
              msg.startsWith('[SCAN]') || msg.startsWith('[OB]') ||
              msg.includes('skipping') || msg.includes('Skipping') ||
              msg.includes('excluded') || msg.includes('pagination') ||
              msg.includes('RSA') || msg.includes('Portfolio balance') ||
              msg.includes('Total exposure') || msg.includes('Open positions+orders') ||
              msg.includes('Events pagination')) return false
          // Keep meaningful activity
          return /Watchlist updated|HIGH confidence|Scored.*HIGH|LLM gate|APPROVED|VETOED|Gate.*PASS|discovered.*trade|new candidate/i.test(msg)
        })
        .slice(0, 5)
      setDonnieSignals(filtered)
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
  if (error) return <ErrorState onRetry={load} />

  const totalDeployed = positions.reduce((s, p) => s + p.exposure, 0)
  const totalRealized = positions.reduce((s, p) => s + p.realized_pnl, 0)

  const econEvals = evals.filter(e => !e.agent || e.agent === 'donnie' || e.agent === 'economics')
  const filteredEvals = econEvals.filter(e => {
    if (evalFilter === 'ALL') return true
    if (evalFilter === 'PENDING') return !e.outcome || e.outcome === 'PENDING'
    return e.outcome === evalFilter
  })
  const evalPages = Math.ceil(filteredEvals.length / 10)
  const pageEvals = filteredEvals.slice(evalPage * 10, (evalPage + 1) * 10)

  const pnlData = econEvals.map((e, i) => ({
    date: e.trade_id,
    value: evals.slice(0, i + 1).reduce((s, r) => s + (r.pnl_pct || 0), 0),
  }))

  return (
    <div className="flex gap-6">
      {/* Left 60% */}
      <div className="flex-[3] space-y-6 min-w-0">
        {/* Open positions */}
        <div className="border border-[#222] rounded-lg bg-[#111] p-4">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-medium text-[#e5e5e5]">Open Positions</h2>
              <span className="px-2 py-0.5 rounded-full bg-[#00ff8820] text-[#00ff88] text-[10px] font-medium">{positions.length}</span>
            </div>
            <div className="flex items-center gap-3 text-xs">
              <span className="text-[#888]">Balance: <span className="text-[#00ff88] font-mono">${balance.toFixed(2)}</span></span>
            </div>
          </div>
          {positions.length ? (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-[#222]">
                    <th className="text-left py-2 px-2 text-[#888] text-xs">Market</th>
                    <th className="text-center py-2 px-2 text-[#888] text-xs">Resolves</th>
                    <th className="text-center py-2 px-2 text-[#888] text-xs">Side</th>
                    <th className="text-right py-2 px-2 text-[#888] text-xs">Deployed $</th>
                    <th className="text-right py-2 px-2 text-[#888] text-xs">Max Payout</th>
                    <th className="text-right py-2 px-2 text-[#888] text-xs">Realized P&L</th>
                  </tr>
                </thead>
                <tbody>
                  {positions.map(p => {
                    // Max payout: exposure / (avg_price_cents/100) = exposure * 100 / avg_price_cents
                    // payout comes from API (calculated using live Kalshi market price)
                    const maxPayout = (p as unknown as Record<string, number>).max_payout || 0
                    const potentialProfit = (p as unknown as Record<string, number>).potential_profit || 0
                    return (
                      <tr key={p.ticker} className="border-b border-[#1a1a1a] hover:bg-[#1a1a1a]">
                        <td className="py-2 px-2">
                          <div className="text-[#e5e5e5] text-xs font-medium">{parseTicker(p.ticker, p.title)}</div>
                          <div className="text-[#555] text-[10px] font-mono">{p.ticker}</div>
                        </td>
                        <td className="py-2 px-2 text-center text-[#888] text-xs">
                          {(() => {
                            const m = p.ticker.match(/-(\d{2})([A-Z]{3})(\d{2})-/)
                            if (!m) return '—'
                            return `${m[2].charAt(0)+m[2].slice(1).toLowerCase()} ${parseInt(m[1])}`
                          })()}
                        </td>
                        <td className="py-2 px-2 text-center">
                          <span className={`px-2 py-0.5 rounded text-[10px] font-medium ${
                            p.side === 'YES' ? 'bg-[#00ff8820] text-[#00ff88]' : 'bg-[#ff444420] text-[#ff4444]'
                          }`}>{p.side}</span>
                        </td>
                        <td className="py-2 px-2 text-right text-[#e5e5e5] font-mono text-xs">${p.exposure.toFixed(2)}</td>
                        <td className="py-2 px-2 text-right font-mono text-xs">
                          {maxPayout > 0 ? (
                            <div>
                              <span className="text-[#e5e5e5]">${maxPayout.toFixed(2)}</span>
                              <span className="text-[#00ff88] text-[10px] ml-1">+${potentialProfit.toFixed(2)}</span>
                            </div>
                          ) : (
                            <span className="text-[#666]">—</span>
                          )}
                        </td>
                        <td className="py-2 px-2 text-right font-mono text-xs" style={{ color: p.realized_pnl >= 0 ? '#00ff88' : '#ff4444' }}>
                          {p.realized_pnl >= 0 ? '+' : ''}{p.realized_pnl.toFixed(2)}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
                <tfoot>
                  <tr className="border-t border-[#333]">
                    <td colSpan={3} className="py-2 px-2 text-xs text-[#888] font-medium">Total Deployed</td>
                    <td className="py-2 px-2 text-right text-[#00ff88] font-mono text-xs font-bold">${totalDeployed.toFixed(2)}</td>
                    <td className="py-2 px-2 text-right text-[#888] font-mono text-xs">—</td>
                    <td className="py-2 px-2 text-right font-mono text-xs font-bold" style={{ color: totalRealized >= 0 ? '#00ff88' : '#ff4444' }}>
                      {totalRealized >= 0 ? '+' : ''}{totalRealized.toFixed(2)}
                    </td>
                  </tr>
                </tfoot>
              </table>
            </div>
          ) : (
            <p className="text-[#666] text-sm py-8 text-center">No open positions — scanner is watching 5,200+ markets</p>
          )}
        </div>

        {/* Trade history */}
        <div className="border border-[#222] rounded-lg bg-[#111] p-4">
          {/* Resolved P&L summary */}
          {(() => {
            const resolved = econEvals.filter(e => e.outcome === 'WIN' || e.outcome === 'LOSS')
            const wins = resolved.filter(e => e.outcome === 'WIN')
            // Use real pnl_dollars where available, fall back to estimated
            const totalGain = resolved.reduce((s, e) => {
              const pnlD = (e as unknown as Record<string, number>).pnl_dollars
              if (typeof pnlD === 'number' && pnlD !== 0) return s + pnlD
              // Fallback estimate: avg $18 per trade
              return s + (e.pnl_pct / 100) * 18
            }, 0)
            const winRate = resolved.length > 0 ? wins.length / resolved.length * 100 : 0
            if (resolved.length === 0) return null
            return (
              <div className="grid grid-cols-4 gap-3 mb-4 pb-4 border-b border-[#1a1a1a]">
                <div className="text-center">
                  <p className="text-[#888] text-[10px] mb-1">Resolved</p>
                  <p className="text-xl font-bold text-[#e5e5e5]">{resolved.length}</p>
                </div>
                <div className="text-center">
                  <p className="text-[#888] text-[10px] mb-1">Win Rate</p>
                  <p className="text-xl font-bold" style={{ color: winRate >= 50 ? '#00ff88' : '#ff4444' }}>
                    {winRate.toFixed(0)}%
                  </p>
                </div>
                <div className="text-center">
                  <p className="text-[#888] text-[10px] mb-1">Est. Total Gain</p>
                  <p className="text-xl font-bold" style={{ color: totalGain >= 0 ? '#00ff88' : '#ff4444' }}>
                    {totalGain >= 0 ? '+' : ''}${totalGain.toFixed(0)}
                  </p>
                </div>
                <div className="text-center">
                  <p className="text-[#888] text-[10px] mb-1">Avg P&L</p>
                  <p className="text-xl font-bold" style={{ color: totalGain >= 0 ? '#00ff88' : '#ff4444' }}>
                    {resolved.length > 0 ? `${(resolved.reduce((s,e)=>s+e.pnl_pct,0)/resolved.length) >= 0 ? '+' : ''}${(resolved.reduce((s,e)=>s+e.pnl_pct,0)/resolved.length).toFixed(0)}%` : '—'}
                  </p>
                </div>
              </div>
            )
          })()}
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-medium text-[#e5e5e5]">Trade History</h2>
              <span className="text-[10px] text-[#666]">{econEvals.length} records</span>
            </div>
            <div className="flex gap-1">
              {(['ALL', 'WIN', 'LOSS', 'PENDING'] as const).map(f => (
                <button
                  key={f}
                  onClick={() => { setEvalFilter(f); setEvalPage(0) }}
                  className={`px-2 py-0.5 rounded text-[10px] font-medium transition-colors ${
                    evalFilter === f
                      ? f === 'WIN' ? 'bg-[#00ff8830] text-[#00ff88]'
                        : f === 'LOSS' ? 'bg-[#ff444430] text-[#ff4444]'
                        : 'bg-[#38bdf830] text-[#38bdf8]'
                      : 'bg-[#1a1a1a] text-[#666] hover:text-[#888]'
                  }`}
                >{f}</button>
              ))}
            </div>
          </div>
          <DataTable
            columns={[
              { key: 'trade_id', label: 'Date', render: (v: string) => <span className="text-[#888]">{parseEvalDate(v)}</span> },
              { key: 'market', label: 'Market', render: (v: string) => <span className="text-[#e5e5e5]">{parseEvalMarket(v)}</span> },
              { key: 'direction', label: 'Side' },
              { key: 'pnl_pct', label: 'Edge', render: (v: number) => v != null ? `${v > 0 ? '+' : ''}${v.toFixed(1)}%` : '—' },
              { key: 'outcome', label: 'Outcome', render: (v: string) => (
                <span className={`px-2 py-0.5 rounded text-[10px] font-medium ${
                  v === 'WIN' ? 'bg-[#00ff8820] text-[#00ff88]' : v === 'LOSS' ? 'bg-[#ff444420] text-[#ff4444]' : 'bg-[#1a1a1a] text-[#888]'
                }`}>{v || 'PENDING'}</span>
              )},
              { key: 'process_score', label: 'Process', render: (_: unknown, row: EvalRecord) => {
                const s = row.llm_eval?.process_score
                if (s == null) return <span className="text-[#666]">—</span>
                const c = s >= 8 ? '#00ff88' : s >= 5 ? '#f59e0b' : '#ff4444'
                return (
                  <div className="flex items-center gap-1">
                    <div className="w-12 bg-[#222] rounded-full h-1.5">
                      <div className="h-1.5 rounded-full" style={{ width: `${s * 10}%`, background: c }} />
                    </div>
                    <span className="text-[10px] font-mono" style={{ color: c }}>{s}</span>
                  </div>
                )
              }},
            ]}
            data={pageEvals.filter(e => !['weather', 'sports'].includes(e.agent || ''))}
            rowClassName={(row: EvalRecord) => row.outcome === 'WIN' ? 'border-l-2 border-l-[#00ff88]' : row.outcome === 'LOSS' ? 'border-l-2 border-l-[#ff4444]' : ''}
            emptyMessage="Trade history grows as Donnie executes and positions resolve"
          />
          {evalPages > 1 && (
            <div className="flex items-center justify-between mt-3 pt-3 border-t border-[#222]">
              <button onClick={() => setEvalPage(p => Math.max(0, p - 1))} disabled={evalPage === 0}
                className="text-xs text-[#888] hover:text-[#e5e5e5] disabled:opacity-30">← Prev</button>
              <span className="text-[10px] text-[#666]">Page {evalPage + 1} of {evalPages}</span>
              <button onClick={() => setEvalPage(p => Math.min(evalPages - 1, p + 1))} disabled={evalPage >= evalPages - 1}
                className="text-xs text-[#888] hover:text-[#e5e5e5] disabled:opacity-30">Next →</button>
            </div>
          )}
          {pnlData.length > 1 && (
            <div className="mt-4">
              <PnlChart data={pnlData} />
            </div>
          )}
        </div>
      </div>

      {/* Right 40% */}
      <div className="flex-[2] space-y-6 min-w-0">
        {/* Macro Context */}
        <div className="border border-[#222] rounded-lg bg-[#111] p-4">
          <h2 className="text-sm font-medium text-[#e5e5e5] mb-3">MACRO SNAPSHOT — Apr 2026</h2>
          <div className="space-y-3 text-xs">
            <div className="flex gap-2">
              <span>📉</span>
              <div>
                <span className="text-[#e5e5e5] font-medium">GDP:</span>
                <span className="text-[#888] ml-1">GDPNow at 1.24% — Q1 prints Apr 30. Tariff drag + inventory destocking.</span>
              </div>
            </div>
            <div className="flex gap-2">
              <span>📈</span>
              <div>
                <span className="text-[#e5e5e5] font-medium">Inflation:</span>
                <span className="text-[#888] ml-1">Core PCE 3.0% YoY (Feb). March prints Apr 30. Tariff pass-through accelerating.</span>
              </div>
            </div>
            <div className="flex gap-2">
              <span>🏦</span>
              <div>
                <span className="text-[#e5e5e5] font-medium">Fed:</span>
                <span className="text-[#888] ml-1">On hold. March FOMC projected 2026 core PCE at 2.7%. No cuts expected until H2 2026.</span>
              </div>
            </div>
            <div className="flex gap-2">
              <span>⚡</span>
              <div>
                <span className="text-[#e5e5e5] font-medium">Wild card:</span>
                <span className="text-[#888] ml-1">Iran war — Hormuz ~closed, energy prices elevated. First full month in March PCE data.</span>
              </div>
            </div>
          </div>
        </div>

        {/* Upcoming Events Timeline */}
        <div className="border border-[#222] rounded-lg bg-[#111] p-4">
          <h2 className="text-sm font-medium text-[#e5e5e5] mb-3">Upcoming Events</h2>
          <div className="relative">
            <div className="absolute left-[7px] top-2 bottom-2 w-px bg-[#333]" />
            <div className="space-y-4">
              {TIMELINE.map(t => (
                <div key={t.date} className="flex items-start gap-3 relative">
                  <div className={`w-[15px] h-[15px] rounded-full border-2 shrink-0 mt-0.5 ${
                    t.active ? 'border-[#00ff88] bg-[#00ff8830]' : 'border-[#333] bg-[#111]'
                  }`} />
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-[#e5e5e5] font-medium">{t.date}</span>
                      <span className="text-[10px] text-[#888]">{t.event}</span>
                    </div>
                    <span className={`text-[10px] ${t.active ? 'text-[#00ff88]' : 'text-[#666]'}`}>{t.status}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Donnie's Active Signals / Watchlist */}
        <div className="border border-[#222] rounded-lg bg-[#111] p-4">
          <h2 className="text-sm font-medium text-[#e5e5e5] mb-3">📅 MARKET RADAR — Upcoming Catalysts</h2>

          {donnieSignals.length > 0 && (
            <div className="mb-4 space-y-2">
              <p className="text-[10px] text-[#00ff88] uppercase tracking-wider">Live Signals</p>
              {donnieSignals.map((s, i) => (
                <div key={i} className="bg-[#0a0a0a] rounded p-2 border border-[#1a1a1a]">
                  <p className="text-xs text-[#e5e5e5]">{s.message}</p>
                  <p className="text-[10px] text-[#444] mt-0.5">{s.timestamp.slice(11, 19)} UTC</p>
                </div>
              ))}
            </div>
          )}

          <div className="space-y-4">
            {UPCOMING_EVENTS.map(evt => (
              <div key={evt.date} className="bg-[#0a0a0a] rounded p-3">
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-[#00ff88] font-mono text-xs font-medium">{evt.date}</span>
                  <span className="text-xs text-[#e5e5e5] font-medium">— {evt.title}</span>
                </div>
                {evt.details.split('\n').map((line, i) => (
                  <p key={i} className="text-[10px] text-[#888] mt-0.5">{line}</p>
                ))}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
