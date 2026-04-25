'use client'

import { useEffect, useState, useCallback } from 'react'
import { fetchPortfolio, fetchPositions, PortfolioItem, PositionItem } from '@/lib/api'
import LoadingSpinner from '@/components/shared/LoadingSpinner'
import ErrorState from '@/components/shared/ErrorState'

const QUANTITIES: Record<string, number> = {
  'BRK-B': 15, C: 80, DVN: 500, GOOG: 400, GOOGL: 400, LYFT: 100, MCD: 50, MU: 100,
  NVDA: 210, OKLO: 175, PLTR: 100, PYPL: 50, SMR: 500, SOBO: 50, SPY: 44, SWPPX: 164.55,
  TRP: 250, TSM: 15, VFIAX: 71.112, VTSAX: 187.649, WWD: 450, FRCB: 100,
}

const AVG_COSTS: Record<string, number> = {
  'BRK-B': 349.65, C: 41.38, DVN: 65.05, GOOG: 15.70, GOOGL: 15.80, LYFT: 57.99, MCD: 259.13,
  MU: 101.88, NVDA: 41.52, OKLO: 97.81, PLTR: 162.20, PYPL: 74.97, SMR: 19.85, SPY: 449.16,
  TRP: 34.40, TSM: 196.88, WWD: 24.12, SOBO: 16.91, SWPPX: 13.82, VFIAX: 439.09, VTSAX: 108.77, FRCB: 13.905,
}

const SECTORS: Record<string, string> = {
  'BRK-B': 'Finance', C: 'Finance', DVN: 'Energy', GOOG: 'Tech', GOOGL: 'Tech',
  LYFT: 'Tech', MCD: 'Consumer', MU: 'Tech', NVDA: 'Tech', OKLO: 'Energy',
  PLTR: 'Tech', PYPL: 'Tech', SMR: 'Energy', SOBO: 'Energy', SPY: 'Index',
  SWPPX: 'Index', TRP: 'Energy', TSM: 'Tech', VFIAX: 'Index', VTSAX: 'Index',
  WWD: 'Industrial', FRCB: 'Finance',
}

const GOLDEN = ['GOOG', 'GOOGL', 'WWD']
const DEAD = ['FRCB']

// Roth VTSAX is separate
const ROTH_VTSAX_SHARES = 71.112

export default function PortfolioTab() {
  const [portfolio, setPortfolio] = useState<PortfolioItem[]>([])
  const [spyPositions, setSpyPositions] = useState<PositionItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [sortKey, setSortKey] = useState<'gain_pct' | 'change_pct' | 'ticker' | 'value'>('gain_pct')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')

  const load = useCallback(async () => {
    try {
      const [p, pos] = await Promise.all([
        fetchPortfolio(),
        fetchPositions().catch(() => [])
      ])
      setPortfolio(p)
      setSpyPositions(Array.isArray(pos) ? pos.filter((x: any) => (x.ticker || '').includes('SPY')) : [])
      setError(false)
    } catch { setError(true) }
    setLoading(false)
  }, [])

  useEffect(() => { load() }, [load])

  if (loading) return <div className="flex justify-center py-20"><LoadingSpinner size="lg" /></div>
  if (error) return <ErrorState onRetry={load} />

  // Enrich with quantities and dollar values
  const enriched = portfolio.map(p => {
    const qty = QUANTITIES[p.ticker] || 0
    const avgCost = AVG_COSTS[p.ticker] || 0
    const price = p.price || 0
    const value = price * qty
    const costBasis = avgCost * qty
    const dollarGain = value - costBasis
    const dayDollarChange = (p.change_pct || 0) / 100 * value
    return { ...p, qty, value, costBasis, dollarGain, dayDollarChange, sector: SECTORS[p.ticker] || 'Other' }
  })

  const sorted = [...enriched].sort((a, b) => {
    if (sortKey === 'value') return sortDir === 'asc' ? a.value - b.value : b.value - a.value
    const va = a[sortKey] ?? -Infinity
    const vb = b[sortKey] ?? -Infinity
    if (typeof va === 'string') return sortDir === 'asc' ? (va as string).localeCompare(vb as string) : (vb as string).localeCompare(va as string)
    return sortDir === 'asc' ? (va as number) - (vb as number) : (vb as number) - (va as number)
  })

  const totalValue = enriched.reduce((s, p) => s + p.value, 0)
  const totalCost = enriched.reduce((s, p) => s + p.costBasis, 0)
  const totalGain = totalValue - totalCost
  const totalGainPct = totalCost > 0 ? (totalGain / totalCost) * 100 : 0
  const inGreen = enriched.filter(p => (p.gain_pct ?? 0) > 0).length
  const inRed = enriched.filter(p => (p.gain_pct ?? 0) <= 0).length

  const biggestDayMover = [...enriched].sort((a, b) => Math.abs(b.change_pct || 0) - Math.abs(a.change_pct || 0))[0]

  // Sector breakdown
  const sectorTotals: Record<string, number> = {}
  enriched.forEach(p => { sectorTotals[p.sector] = (sectorTotals[p.sector] || 0) + p.value })

  // Roth VTSAX value
  const vtsaxPrice = portfolio.find(p => p.ticker === 'VTSAX')?.price || 0
  const rothValue = ROTH_VTSAX_SHARES * vtsaxPrice

  const handleSort = (key: typeof sortKey) => {
    if (sortKey === key) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setSortKey(key); setSortDir('desc') }
  }

  const pctCell = (v: number | null, bold?: boolean) => {
    if (v == null) return <span className="text-[#666]">—</span>
    const c = v > 0 ? '#00ff88' : v < 0 ? '#ff4444' : '#888'
    return <span style={{ color: c }} className={bold ? 'font-bold' : ''}>{v > 0 ? '+' : ''}{v.toFixed(1)}%</span>
  }

  const dollarCell = (v: number) => {
    const c = v > 0 ? '#00ff88' : v < 0 ? '#ff4444' : '#888'
    return <span style={{ color: c }}>{v >= 0 ? '+' : ''}{v >= 1000 || v <= -1000 ? `$${(v/1000).toFixed(1)}K` : `$${v.toFixed(0)}`}</span>
  }

  return (
    <div className="space-y-6">
      {/* Header strip */}
      <div className="border border-[#222] rounded-lg bg-[#111] p-4">
        <div className="flex items-center justify-between">
          <div>
            <span className="text-[#888] text-xs">Total Portfolio Value</span>
            <p className="text-3xl font-bold text-[#e5e5e5]">${(totalValue / 1000).toFixed(1)}K</p>
          </div>
          <div className="text-center">
            <span className="text-[#888] text-xs">Unrealized Gain</span>
            <p className="text-xl font-bold" style={{ color: totalGain > 0 ? '#00ff88' : '#ff4444' }}>
              {totalGain >= 0 ? '+' : ''}${(totalGain / 1000).toFixed(1)}K
            </p>
          </div>
          <div className="text-right">
            <span className="text-[#888] text-xs">Total Return</span>
            <p className="text-xl font-bold" style={{ color: totalGainPct > 0 ? '#00ff88' : '#ff4444' }}>
              {totalGainPct > 0 ? '+' : ''}{totalGainPct.toFixed(0)}%
            </p>
          </div>
        </div>
      </div>

      <div className="flex gap-6">
        {/* Equities table - left 65% */}
        <div className="flex-[65] min-w-0">
          <div className="border border-[#222] rounded-lg bg-[#111] p-4">
            <h2 className="text-sm font-medium text-[#e5e5e5] mb-3">Equities — {enriched.length} Positions</h2>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-[#222]">
                    <th className="text-left py-2 px-2 text-[#888] text-xs cursor-pointer" onClick={() => handleSort('ticker')}>Symbol</th>
                    <th className="text-right py-2 px-2 text-[#888] text-xs">Qty</th>
                    <th className="text-right py-2 px-2 text-[#888] text-xs">Price</th>
                    <th className="text-right py-2 px-2 text-[#888] text-xs cursor-pointer" onClick={() => handleSort('value')}>Value</th>
                    <th className="text-right py-2 px-2 text-[#888] text-xs cursor-pointer" onClick={() => handleSort('change_pct')}>Day %</th>
                    <th className="text-right py-2 px-2 text-[#888] text-xs cursor-pointer" onClick={() => handleSort('gain_pct')}>Total %</th>
                    <th className="text-right py-2 px-2 text-[#888] text-xs">$ Gain</th>
                  </tr>
                </thead>
                <tbody>
                  {sorted.map(p => {
                    const isGolden = GOLDEN.includes(p.ticker)
                    const isDead = DEAD.includes(p.ticker)
                    const gain = p.gain_pct ?? 0
                    return (
                      <tr
                        key={p.ticker}
                        className={`border-b border-[#1a1a1a] hover:bg-[#1a1a1a] transition-colors ${
                          isGolden ? 'bg-[#f59e0b08]' : isDead ? 'bg-[#ff444408]' : ''
                        }`}
                        style={{ borderLeftWidth: 3, borderLeftColor: gain > 0 ? '#00ff8844' : gain < 0 ? '#ff444444' : '#33333344' }}
                      >
                        <td className="py-1.5 px-2">
                          <span className={`font-mono font-medium text-xs ${isGolden ? 'text-[#f59e0b]' : isDead ? 'text-[#ff4444]' : 'text-[#e5e5e5]'}`}>{p.ticker}</span>
                          <span className="text-[9px] text-[#555] ml-1">{p.sector}</span>
                        </td>
                        <td className="py-1.5 px-2 text-right text-[#888] text-xs">{Number.isInteger(p.qty) ? p.qty : p.qty.toFixed(2)}</td>
                        <td className="py-1.5 px-2 text-right text-[#e5e5e5] text-xs">{p.price != null ? `$${p.price.toFixed(2)}` : '—'}</td>
                        <td className="py-1.5 px-2 text-right text-[#e5e5e5] text-xs font-medium">
                          {p.value >= 1000 ? `$${(p.value/1000).toFixed(1)}K` : `$${p.value.toFixed(0)}`}
                        </td>
                        <td className="py-1.5 px-2 text-right text-xs">{pctCell(p.change_pct)}</td>
                        <td className="py-1.5 px-2 text-right text-xs">{pctCell(p.gain_pct, true)}</td>
                        <td className="py-1.5 px-2 text-right text-xs">{dollarCell(p.dollarGain)}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </div>

        {/* Quick stats - right 35% */}
        <div className="flex-[35] space-y-4 min-w-0">
          {/* Quick Stats */}
          <div className="border border-[#222] rounded-lg bg-[#111] p-4 space-y-3">
            <h2 className="text-sm font-medium text-[#e5e5e5] mb-2">Quick Stats</h2>
            <div className="flex justify-between text-sm">
              <span className="text-[#888]">Total Value</span>
              <span className="text-[#e5e5e5] font-mono font-bold">${(totalValue / 1000).toFixed(1)}K</span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-[#888]">Unrealized Gain</span>
              <span className="font-mono" style={{ color: totalGain > 0 ? '#00ff88' : '#ff4444' }}>
                {totalGain >= 0 ? '+' : ''}${(totalGain / 1000).toFixed(1)}K
              </span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-[#888]">Day Mover</span>
              <span className="text-[#e5e5e5] font-mono">
                {biggestDayMover?.ticker} {biggestDayMover?.change_pct != null ? `${biggestDayMover.change_pct > 0 ? '+' : ''}${biggestDayMover.change_pct.toFixed(1)}%` : ''}
              </span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-[#888]">Green / Red</span>
              <span><span className="text-[#00ff88]">{inGreen}</span> <span className="text-[#666]">/</span> <span className="text-[#ff4444]">{inRed}</span></span>
            </div>
            <div className="pt-2 border-t border-[#222]">
              <p className="text-[10px] text-[#888] mb-2">Sectors</p>
              <div className="space-y-1">
                {Object.entries(sectorTotals).sort((a, b) => b[1] - a[1]).map(([sector, val]) => (
                  <div key={sector} className="flex items-center gap-2">
                    <span className="text-[10px] text-[#888] w-16">{sector}</span>
                    <div className="flex-1 bg-[#222] rounded-full h-1.5">
                      <div className="bg-[#00ff88] h-1.5 rounded-full" style={{ width: `${(val / totalValue) * 100}%` }} />
                    </div>
                    <span className="text-[10px] text-[#666] w-10 text-right">{((val / totalValue) * 100).toFixed(0)}%</span>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* Roth IRA */}
          <div className="border border-[#222] rounded-lg bg-[#111] p-4">
            <h2 className="text-sm font-medium text-[#e5e5e5] mb-2">Roth IRA</h2>
            <div className="space-y-2">
              <div className="flex justify-between text-sm">
                <span className="text-[#888]">Position</span>
                <span className="text-[#e5e5e5] font-mono">VTSAX</span>
              </div>
              <div className="flex justify-between text-sm">
                <span className="text-[#888]">Shares</span>
                <span className="text-[#e5e5e5]">{ROTH_VTSAX_SHARES} (Roth only)</span>
              </div>
              <div className="flex justify-between text-sm">
                <span className="text-[#888]">Value</span>
                <span className="text-[#00ff88] font-mono">${rothValue > 0 ? rothValue.toLocaleString('en-US', { maximumFractionDigits: 0 }) : '—'}</span>
              </div>
              <div className="flex justify-between text-sm">
                <span className="text-[#888]">2026 Contributions</span>
                <span className="text-[#e5e5e5]">$0 / $7,000</span>
              </div>
              <div className="w-full bg-[#222] rounded-full h-2">
                <div className="bg-[#333] h-2 rounded-full" style={{ width: '0%' }} />
              </div>
              <p className="text-[#f59e0b] text-xs mt-2">⚠️ $7,000 contribution deadline: April 15, 2027</p>
              <p className="text-[#38bdf8] text-[10px] mt-1">💡 Strategy: Use Roth for SPY 0DTE options — tax-free gains</p>
            </div>
          </div>
        </div>
      </div>

      {/* SPY 0DTE */}
      <div className="border border-[#222] rounded-lg bg-[#111] p-4">
        <h2 className="text-sm font-medium text-[#e5e5e5] mb-1">SPY Options Desk — Jordan | 0DTE Monitoring</h2>
        {spyPositions.length > 0 ? (
          <div className="overflow-x-auto mt-3">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-[#222]">
                  <th className="text-left py-2 px-2 text-[#888]">Ticker</th>
                  <th className="text-center py-2 px-2 text-[#888]">Side</th>
                  <th className="text-right py-2 px-2 text-[#888]">Contracts</th>
                  <th className="text-right py-2 px-2 text-[#888]">Exposure</th>
                </tr>
              </thead>
              <tbody>
                {spyPositions.map((p: any) => (
                  <tr key={p.ticker} className="border-b border-[#1a1a1a]">
                    <td className="py-1.5 px-2 text-[#e5e5e5] font-mono">{p.ticker}</td>
                    <td className="py-1.5 px-2 text-center">
                      <span className={`px-2 py-0.5 rounded text-[10px] ${p.side === 'YES' ? 'bg-[#00ff8820] text-[#00ff88]' : 'bg-[#ff444420] text-[#ff4444]'}`}>{p.side}</span>
                    </td>
                    <td className="py-1.5 px-2 text-right text-[#e5e5e5]">{p.contracts}</td>
                    <td className="py-1.5 px-2 text-right text-[#e5e5e5] font-mono">${p.exposure?.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-[#666] text-sm py-4 text-center">No active positions — log next group alert to start monitoring</p>
        )}
      </div>

      {/* Portfolio watchlist */}
      <div className="border border-[#222] rounded-lg bg-[#111] p-4">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-medium text-[#e5e5e5]">Watchlist</h2>
          <button className="px-3 py-1 rounded bg-[#1a1a1a] border border-[#333] text-[#888] text-xs cursor-default">Edit watchlist</button>
        </div>
        <div className="grid grid-cols-5 gap-3">
          {['AAPL', 'META', 'AMZN', 'AMD', 'MSFT'].map(t => (
            <div key={t} className="bg-[#0a0a0a] rounded p-3 text-center">
              <span className="text-[#00ff88] font-mono font-medium text-sm">{t}</span>
              <p className="text-[10px] text-[#666] mt-1">Watching</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
