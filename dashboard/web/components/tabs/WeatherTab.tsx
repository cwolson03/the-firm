'use client'

import { useEffect, useState, useCallback } from 'react'
import { fetchWeather, fetchEval, WeatherResponse, EvalRecord } from '@/lib/api'
import LoadingSpinner from '@/components/shared/LoadingSpinner'
import ErrorState from '@/components/shared/ErrorState'

type LivePosition = {
  ticker: string
  city: string
  title: string
  side: string
  exposure: number
  current_no_price_cents: number
  max_payout: number
  potential_profit: number
  close_time: string
}

type WeatherData = WeatherResponse & {
  live_positions?: LivePosition[]
  baseline?: {
    total: number
    resolved: number
    wins: number
    win_rate: number
    period: string
  }
  open?: { ticker: string; city_name: string; direction: string; edge: number; forecast_high: number; kalshi_prob: number; model_prob: number; cost: number; timestamp: string }[]
}

function parseTempCondition(ticker: string): string {
  // KXHIGHNY-26APR28-T75 → "HIGH < 75°F"
  // KXHIGHNY-26APR28-B77.5 → "HIGH 77-78°F"
  const match = ticker.match(/-([TB])(\d+\.?\d*)$/)
  if (!match) return ticker
  const type = match[1]
  const val = parseFloat(match[2])
  if (type === 'T') return `HIGH < ${val}°F`
  if (type === 'B') return `HIGH ${val}–${val + 1}°F`
  return ticker
}

function formatCloseTime(iso: string): string {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
  } catch { return '—' }
}

function WinRateBar({ rate, total }: { rate: number; total: number }) {
  const color = rate >= 60 ? '#00ff88' : rate >= 45 ? '#f59e0b' : '#ff4444'
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 bg-[#1a1a1a] rounded-full h-1.5 max-w-[80px]">
        <div className="h-1.5 rounded-full transition-all" style={{ width: `${Math.min(rate, 100)}%`, background: color }} />
      </div>
      <span className="text-xs font-mono" style={{ color }}>{rate.toFixed(0)}%</span>
      <span className="text-[10px] text-[#555]">({total})</span>
    </div>
  )
}

export default function WeatherTab() {
  const [data, setData] = useState<WeatherData | null>(null)
  const [liveEvals, setLiveEvals] = useState<EvalRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)

  const load = useCallback(async () => {
    try {
      const [w, evals] = await Promise.all([
        fetchWeather() as Promise<WeatherData>,
        fetchEval().catch(() => [] as EvalRecord[]),
      ])
      setData(w)
      // filter to live weather trades only (from Apr 27 onward, agent=weather)
      const liveWeatherEvals = (evals as EvalRecord[]).filter(e => e.agent === 'weather')
      setLiveEvals(liveWeatherEvals)
      setError(false)
    } catch { setError(true) }
    setLoading(false)
  }, [])

  useEffect(() => { load() }, [load])
  useEffect(() => {
    const iv = setInterval(load, 60000)
    return () => clearInterval(iv)
  }, [load])

  if (loading) return <div className="flex justify-center py-20"><LoadingSpinner size="lg" /></div>
  if (error || !data) return <ErrorState onRetry={load} />

  const livePos = data.live_positions || []
  const resolvedEvals = liveEvals.filter((e: import('@/lib/api').EvalRecord) => e.outcome === 'WIN' || e.outcome === 'LOSS')
  const baseline = data.baseline
  const paperOpen = (data.open || []).slice(0, 10)

  // city performance — weighted score (penalizes low volume)
  const cityEntries = Object.entries(data.by_city || {})
    .map(([city, s]) => ({
      city,
      total: s.total,
      wins: s.wins,
      win_rate: s.win_rate,
      score: s.win_rate * Math.min(s.total / 20, 1),
    }))
    .sort((a, b) => b.score - a.score)
    .slice(0, 10)

  // recent resolved trades
  const recentResolved = (data.recent || []).slice(0, 15)

  return (
    <div className="space-y-6">

      {/* SECTION 1: Live Kalshi Positions */}
      <div>
        <div className="flex items-center gap-3 mb-3">
          <h2 className="text-sm font-semibold text-[#e5e5e5]">Live Positions</h2>
          <span className="flex items-center gap-1 px-2 py-0.5 rounded-full bg-[#00ff8815] text-[#00ff88] text-[10px]">
            <span className="w-1.5 h-1.5 rounded-full bg-[#00ff88] animate-pulse" />
            Real money
          </span>
          <span className="text-[#555] text-xs">{livePos.length} open on Kalshi</span>
        </div>

        {/* Live stats summary */}
        {(() => {
          const totalDeployed = livePos.reduce((s, p) => s + p.exposure, 0)
          const totalMaxPayout = livePos.reduce((s, p) => s + p.max_payout, 0)
          const totalPotentialProfit = livePos.reduce((s, p) => s + p.potential_profit, 0)
          const resolvedEvals = liveEvals.filter(e => e.outcome === 'WIN' || e.outcome === 'LOSS')
          const winEvals = liveEvals.filter(e => e.outcome === 'WIN')
          const winRate = resolvedEvals.length > 0 ? (winEvals.length / resolvedEvals.length * 100) : 0
          // Total gain from resolved trades: use pnl_pct directly
          // pnl_pct > 0 means profit, pnl_pct = -100 means full loss
          // Estimate exposure from payout math: Austin was ~$4.71, Phoenix ~$4.80
          // Average ~$4.75 per trade as proxy when exact exposure not stored
          const AVG_EXPOSURE = 4.75
          const totalGainDollars = resolvedEvals.reduce((s, e) => {
            if (e.pnl_pct >= 0) {
              // WIN: gain = payout - cost. pnl_pct = (payout-cost)/cost*100
              // So gain_dollars = (pnl_pct/100) * estimated_cost
              return s + (e.pnl_pct / 100) * AVG_EXPOSURE
            } else {
              // LOSS: lose the full exposure (~$4.75)
              return s - AVG_EXPOSURE
            }
          }, 0)
          const totalGainPct = resolvedEvals.length > 0 
            ? (totalGainDollars / (resolvedEvals.length * AVG_EXPOSURE) * 100) 
            : 0
          return (
            <div className="grid grid-cols-3 gap-3 mb-4">
              <div className="border border-[#1e1e1e] rounded-lg bg-[#0d0d0d] p-3">
                <p className="text-[#666] text-[10px] mb-1">Live Win Rate</p>
                <p className="text-2xl font-bold" style={{ color: resolvedEvals.length === 0 ? '#444' : winRate >= 50 ? '#00ff88' : '#f59e0b' }}>
                  {resolvedEvals.length === 0 ? '—' : `${winRate.toFixed(0)}%`}
                </p>
                <p className="text-[10px] text-[#444] mt-0.5">{resolvedEvals.length}/{liveEvals.length} resolved</p>
              </div>
              <div className="border border-[#1e1e1e] rounded-lg bg-[#0d0d0d] p-3">
                <p className="text-[#666] text-[10px] mb-1">Live Total Gain</p>
                <p className="text-2xl font-bold" style={{ color: resolvedEvals.length === 0 ? '#444' : totalGainDollars >= 0 ? '#00ff88' : '#ff4444' }}>
                  {resolvedEvals.length === 0 ? '—' : `${totalGainDollars >= 0 ? '+' : ''}$${totalGainDollars.toFixed(2)}`}
                </p>
                <p className="text-[10px] text-[#444] mt-0.5">{resolvedEvals.length === 0 ? 'pending' : `${totalGainPct >= 0 ? '+' : ''}${totalGainPct.toFixed(1)}%`}</p>
              </div>
              <div className="border border-[#1e1e1e] rounded-lg bg-[#0d0d0d] p-3">
                <p className="text-[#666] text-[10px] mb-1">Open Potential</p>
                <p className="text-2xl font-bold text-[#00ff88]">+${totalPotentialProfit.toFixed(2)}</p>
                <p className="text-[10px] text-[#444] mt-0.5">${totalDeployed.toFixed(2)} deployed → ${totalMaxPayout.toFixed(2)} max</p>
              </div>
            </div>
          )
        })()}

        {livePos.length > 0 ? (
          <div className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))" }}>
            {livePos.map(p => {
              const isWinning = p.current_no_price_cents >= 50  // market agrees with our NO
              const isLosing = p.current_no_price_cents < 20     // market disagrees
              const borderColor = isWinning ? '#00ff88' : isLosing ? '#ff4444' : '#f59e0b'
              return (
                <div key={p.ticker} className="rounded-lg bg-[#111] p-4 border"
                  style={{ borderColor: borderColor + '40' }}>
                  <div className="flex items-start justify-between mb-2">
                    <div>
                      <p className="text-[#e5e5e5] font-medium text-sm">{p.city}</p>
                      <p className="text-[#666] text-[10px] mt-0.5">{parseTempCondition(p.ticker)}</p>
                    </div>
                    <span className="px-2 py-0.5 rounded text-[10px] font-medium bg-[#ff444420] text-[#ff4444]">NO</span>
                  </div>
                  <div className="grid grid-cols-2 gap-2 text-[11px] mt-3">
                    <div>
                      <p className="text-[#666]">Deployed</p>
                      <p className="text-[#e5e5e5] font-mono">${p.exposure.toFixed(2)}</p>
                    </div>
                    <div>
                      <p className="text-[#666]">Max Payout</p>
                      <p className="font-mono" style={{ color: isWinning ? '#00ff88' : '#f59e0b' }}>
                        {p.current_no_price_cents > 5 && p.max_payout > 0
                          ? `$${p.max_payout.toFixed(2)}`
                          : <span className="text-[#444]">market favors YES</span>}
                      </p>
                    </div>
                    <div>
                      <p className="text-[#666]">Market NO price</p>
                      <p className="font-mono text-[#e5e5e5]">{p.current_no_price_cents > 0 ? `${p.current_no_price_cents.toFixed(0)}¢` : '—'}</p>
                    </div>
                    <div>
                      <p className="text-[#666]">Closes</p>
                      <p className="text-[#e5e5e5]">{formatCloseTime(p.close_time)}</p>
                    </div>
                  </div>
                  <div className="mt-2 pt-2 border-t border-[#1a1a1a]">
                    {p.current_no_price_cents > 10 && p.max_payout > 0 ? (
                      <span className="text-[10px] font-medium text-[#00ff88]">
                        +${p.potential_profit.toFixed(2)} profit if NO wins
                      </span>
                    ) : (
                      <span className="text-[10px] font-medium text-[#ff4444]">
                        ${p.exposure.toFixed(2)} at risk — market favors YES
                      </span>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        ) : (
          <div className="border border-[#1e1e1e] rounded-lg bg-[#111] p-6 text-center">
            <p className="text-[#666] text-sm">No live positions — scanner running every 3 minutes</p>
          </div>
        )}
      </div>

      {/* SECTION 2: Live Trade Results */}
      {resolvedEvals.length > 0 && (
        <div className="border border-[#222] rounded-lg bg-[#111] p-4">
          <div className="flex items-center gap-2 mb-3">
            <h2 className="text-sm font-medium text-[#e5e5e5]">Live Trade Results</h2>
            <span className="text-[10px] text-[#00ff88] bg-[#00ff8815] px-2 py-0.5 rounded-full">
              {resolvedEvals.length} resolved
            </span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-[#222]">
                  <th className="text-left py-2 px-2 text-[#888]">City & Condition</th>
                  <th className="text-center py-2 px-2 text-[#888]">Side</th>
                  <th className="text-right py-2 px-2 text-[#888]">Cost</th>
                  <th className="text-right py-2 px-2 text-[#888]">Gain</th>
                  <th className="text-center py-2 px-2 text-[#888]">Result</th>
                </tr>
              </thead>
              <tbody>
                {resolvedEvals.map((e, i) => {
                  const isWin = e.outcome === 'WIN'
                  const AVG_COST = 4.75
                  const gainDollars = isWin ? (e.pnl_pct / 100) * AVG_COST : -AVG_COST
                  // Parse city name from ticker prefix
                  const CITY_MAP: Record<string, string> = {
                    'KXHIGHNY': 'New York', 'KXHIGHTNY': 'New York',
                    'KXHIGHLAX': 'Los Angeles', 'KXHIGHTLAX': 'Los Angeles',
                    'KXHIGHMIA': 'Miami', 'KXHIGHTMIA': 'Miami',
                    'KXHIGHAUS': 'Austin', 'KXHIGHTAUS': 'Austin',
                    'KXHIGHTPHX': 'Phoenix', 'KXHIGHPHX': 'Phoenix',
                    'KXHIGHTCHI': 'Chicago', 'KXHIGHCHI': 'Chicago',
                    'KXHIGHTHOU': 'Houston', 'KXHIGHHOU': 'Houston',
                    'KXHIGHTMIN': 'Minneapolis', 'KXHIGHMIN': 'Minneapolis',
                    'KXHIGHTATL': 'Atlanta', 'KXHIGHATL': 'Atlanta',
                    'KXHIGHTBOS': 'Boston', 'KXHIGHBOS': 'Boston',
                    'KXHIGHTDC': 'Washington DC', 'KXHIGHDC': 'Washington DC',
                    'KXHIGHTNOLA': 'New Orleans', 'KXHIGHNOLA': 'New Orleans',
                    'KXHIGHTOKC': 'Oklahoma City', 'KXHIGHOKC': 'Oklahoma City',
                    'KXHIGHTSATX': 'San Antonio', 'KXHIGHSATX': 'San Antonio',
                    'KXHIGHTSFO': 'San Francisco', 'KXHIGHSFO': 'San Francisco',
                    'KXHIGHTSEA': 'Seattle', 'KXHIGHSEA': 'Seattle',
                    'KXHIGHPHIL': 'Philadelphia', 'KXHIGHTPHIL': 'Philadelphia',
                  }
                  const seriesKey = e.trade_id.match(/^(KXHIGH[A-Z]+)-/)?.[1] || ''
                  const cityName = CITY_MAP[seriesKey] || seriesKey.replace('KXHIGHT', '').replace('KXHIGH', '')
                  const condition = parseTempCondition(e.trade_id)
                  return (
                    <tr key={i} className={`border-b border-[#1a1a1a] ${isWin ? 'border-l-2 border-l-[#00ff88]' : 'border-l-2 border-l-[#ff4444]'}`}>
                      <td className="py-2 px-2">
                        <div className="text-[#e5e5e5] text-xs font-medium">{cityName}</div>
                        <div className="text-[#555] text-[10px]">{condition}</div>
                        <div className="text-[#333] text-[9px] font-mono">{e.trade_id}</div>
                      </td>
                      <td className="py-2 px-2 text-center">
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-[#ff444420] text-[#ff4444]">NO</span>
                      </td>
                      <td className="py-2 px-2 text-right text-[#888] font-mono text-xs">~$4.75</td>
                      <td className="py-2 px-2 text-right font-mono font-bold text-xs" style={{ color: isWin ? '#00ff88' : '#ff4444' }}>
                        {gainDollars >= 0 ? '+' : ''}${gainDollars.toFixed(2)}
                        <div className="text-[10px] font-normal" style={{ color: isWin ? '#00ff8880' : '#ff444480' }}>
                          {isWin ? `+${e.pnl_pct.toFixed(1)}%` : `${e.pnl_pct.toFixed(1)}%`}
                        </div>
                      </td>
                      <td className="py-2 px-2 text-center">
                        <span className={`text-[10px] px-2 py-0.5 rounded font-medium ${isWin ? 'bg-[#00ff8820] text-[#00ff88]' : 'bg-[#ff444420] text-[#ff4444]'}`}>
                          {e.outcome}
                        </span>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* SECTION 3: Calibration Baseline + City Performance */}
      <div className="grid grid-cols-2 gap-4">
        {/* Win Rate Gauge */}
        <div className="border border-[#222] rounded-lg bg-[#111] p-4">
          <h2 className="text-sm font-medium text-[#e5e5e5] mb-1">Calibration Baseline</h2>
          <p className="text-[#555] text-[10px] mb-4">{baseline?.period || 'Apr 19 – Apr 26'} · post-parameter-tuning</p>
          <div className="flex items-end gap-3">
            <div>
              <span className="text-5xl font-bold" style={{ color: (baseline?.win_rate || 0) >= 50 ? '#00ff88' : '#f59e0b' }}>
                {baseline?.win_rate?.toFixed(1) || data.win_rate.toFixed(1)}%
              </span>
              <p className="text-[#555] text-xs mt-1">win rate</p>
            </div>
            <div className="text-xs text-[#666] space-y-1 mb-1">
              <p>{baseline?.resolved || data.resolved} resolved</p>
              <p>{baseline?.wins || data.wins} wins</p>
              <p>{(baseline?.total || data.total)} total trades</p>
            </div>
          </div>
          <p className="text-[10px] text-[#444] mt-3">
            Pre-tuning data (Apr 16-18) excluded — parameter changes made the earlier data unrepresentative
          </p>
        </div>

        {/* City Performance */}
        <div className="border border-[#222] rounded-lg bg-[#111] p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-medium text-[#e5e5e5]">City Performance</h2>
            <span className="text-[10px] text-[#555]">volume-weighted</span>
          </div>
          <div className="space-y-2">
            {cityEntries.slice(0, 8).map(c => (
              <div key={c.city} className="flex items-center justify-between gap-2">
                <span className="text-xs text-[#888] w-28 shrink-0 truncate">{c.city}</span>
                <WinRateBar rate={c.win_rate} total={c.total} />
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* SECTION 3: Paper Scan Open Signals */}
      {paperOpen.length > 0 && (
        <div className="border border-[#1e1e1e] rounded-lg bg-[#0d0d0d] p-4">
          <div className="flex items-center gap-2 mb-3">
            <h2 className="text-sm font-medium text-[#666]">Paper Scan — Open Signals</h2>
            <span className="px-2 py-0.5 rounded bg-[#1a1a1a] text-[#555] text-[10px]">parallel logging</span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-[#1a1a1a]">
                  <th className="text-left py-1.5 px-2 text-[#555]">City</th>
                  <th className="text-left py-1.5 px-2 text-[#555]">Condition</th>
                  <th className="text-center py-1.5 px-2 text-[#555]">Dir</th>
                  <th className="text-right py-1.5 px-2 text-[#555]">Edge</th>
                  <th className="text-right py-1.5 px-2 text-[#555]">Forecast</th>
                  <th className="text-right py-1.5 px-2 text-[#555]">Cost</th>
                </tr>
              </thead>
              <tbody>
                {paperOpen.map((t, i) => (
                  <tr key={i} className="border-b border-[#111] opacity-60">
                    <td className="py-1.5 px-2 text-[#888]">{t.city_name}</td>
                    <td className="py-1.5 px-2 text-[#666] font-mono text-[10px]">{parseTempCondition(t.ticker)}</td>
                    <td className="py-1.5 px-2 text-center">
                      <span className={`text-[10px] ${t.direction === 'YES' ? 'text-[#00ff8880]' : 'text-[#ff444480]'}`}>{t.direction}</span>
                    </td>
                    <td className="py-1.5 px-2 text-right text-[#666] font-mono">{(t.edge * 100).toFixed(0)}%</td>
                    <td className="py-1.5 px-2 text-right text-[#666]">{t.forecast_high?.toFixed(1)}°F</td>
                    <td className="py-1.5 px-2 text-right text-[#666] font-mono">${t.cost?.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-[10px] text-[#333] mt-2">Paper scanner runs in parallel — not all signals execute as live trades</p>
        </div>
      )}

      {/* SECTION 4: Recent Resolved (paper baseline) */}
      <div className="border border-[#1e1e1e] rounded-lg bg-[#0d0d0d] p-4">
        <div className="flex items-center gap-2 mb-3">
          <h2 className="text-sm font-medium text-[#666]">Recent Resolved Trades</h2>
          <span className="px-2 py-0.5 rounded bg-[#1a1a1a] text-[#555] text-[10px]">calibration data · Apr 19-26</span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-[#1a1a1a]">
                <th className="text-left py-1.5 px-2 text-[#555]">Date</th>
                <th className="text-left py-1.5 px-2 text-[#555]">City</th>
                <th className="text-left py-1.5 px-2 text-[#555]">Condition</th>
                <th className="text-center py-1.5 px-2 text-[#555]">Result</th>
                <th className="text-right py-1.5 px-2 text-[#555]">Edge</th>
                <th className="text-right py-1.5 px-2 text-[#555]">Source</th>
              </tr>
            </thead>
            <tbody>
              {recentResolved.map((t, i) => {
                const src = (t as Record<string,string>).forecast_source || 'legacy'
                const srcLabel = src.includes('tomorrow') ? 'T.io' : src === 'open_meteo' ? 'OM' : 'legacy'
                return (
                  <tr key={i} className={`border-b border-[#111] opacity-70 ${
                    (t as Record<string,string>).status === 'WIN' ? 'border-l-2 border-l-[#00ff8840]' : 'border-l-2 border-l-[#ff444440]'
                  }`}>
                    <td className="py-1.5 px-2 text-[#555]">{String((t as Record<string,string>).date || '').slice(5)}</td>
                    <td className="py-1.5 px-2 text-[#888]">{(t as Record<string,string>).city_name}</td>
                    <td className="py-1.5 px-2 text-[#666] font-mono text-[10px]">{parseTempCondition((t as Record<string,string>).ticker)}</td>
                    <td className="py-1.5 px-2 text-center">
                      <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
                        (t as Record<string,string>).status === 'WIN' ? 'bg-[#00ff8815] text-[#00ff8880]' : 'bg-[#ff444415] text-[#ff444480]'
                      }`}>{(t as Record<string,string>).status}</span>
                    </td>
                    <td className="py-1.5 px-2 text-right text-[#555] font-mono">{(Number((t as Record<string,unknown>).edge || 0) * 100).toFixed(0)}%</td>
                    <td className="py-1.5 px-2 text-right text-[#444]">{srcLabel}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
