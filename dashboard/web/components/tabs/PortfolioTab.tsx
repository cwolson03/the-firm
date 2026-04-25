'use client'

import { useEffect, useState, useCallback } from 'react'
import { fetchPortfolio, PortfolioItem } from '@/lib/api'
import LoadingSpinner from '@/components/shared/LoadingSpinner'
import ErrorState from '@/components/shared/ErrorState'

const GOLDEN = ['GOOG', 'GOOGL', 'WWD']
const DEAD = ['FRCB']

export default function PortfolioTab() {
  const [portfolio, setPortfolio] = useState<PortfolioItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [sortKey, setSortKey] = useState<'gain_pct' | 'change_pct' | 'ticker'>('gain_pct')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')

  const load = useCallback(async () => {
    try {
      setPortfolio(await fetchPortfolio())
      setError(false)
    } catch { setError(true) }
    setLoading(false)
  }, [])

  useEffect(() => { load() }, [load])

  if (loading) return <div className="flex justify-center py-20"><LoadingSpinner size="lg" /></div>
  if (error) return <ErrorState onRetry={load} />

  const sorted = [...portfolio].sort((a, b) => {
    const va = a[sortKey] ?? -Infinity
    const vb = b[sortKey] ?? -Infinity
    if (typeof va === 'string') return sortDir === 'asc' ? (va as string).localeCompare(vb as string) : (vb as string).localeCompare(va as string)
    return sortDir === 'asc' ? (va as number) - (vb as number) : (vb as number) - (va as number)
  })

  const inGreen = portfolio.filter(p => (p.gain_pct ?? 0) > 0).length
  const inRed = portfolio.filter(p => (p.gain_pct ?? 0) <= 0).length
  const biggest = [...portfolio].sort((a, b) => (b.gain_pct ?? 0) - (a.gain_pct ?? 0))[0]
  const worst = [...portfolio].sort((a, b) => (a.gain_pct ?? 0) - (b.gain_pct ?? 0))[0]
  const avgGain = portfolio.length ? portfolio.reduce((s, p) => s + (p.gain_pct ?? 0), 0) / portfolio.length : 0
  const dayChange = portfolio.length ? portfolio.reduce((s, p) => s + (p.change_pct ?? 0), 0) / portfolio.length : 0

  const handleSort = (key: typeof sortKey) => {
    if (sortKey === key) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setSortKey(key); setSortDir('desc') }
  }

  const pctCell = (v: number | null, bold?: boolean) => {
    if (v == null) return <span className="text-[#666]">—</span>
    const c = v > 0 ? '#00ff88' : v < 0 ? '#ff4444' : '#888'
    return <span style={{ color: c }} className={bold ? 'font-bold' : ''}>{v > 0 ? '+' : ''}{v.toFixed(1)}%</span>
  }

  return (
    <div className="space-y-6">
      {/* Header strip */}
      <div className="border border-[#222] rounded-lg bg-[#111] p-4 flex items-center justify-between">
        <div>
          <span className="text-[#888] text-xs">Portfolio Average Gain</span>
          <p className="text-2xl font-bold" style={{ color: avgGain > 0 ? '#00ff88' : '#ff4444' }}>
            {avgGain > 0 ? '+' : ''}{avgGain.toFixed(0)}%
          </p>
        </div>
        <div className="text-right">
          <span className="text-[#888] text-xs">Avg Day Change</span>
          <p className="text-lg font-medium" style={{ color: dayChange > 0 ? '#00ff88' : dayChange < 0 ? '#ff4444' : '#888' }}>
            {dayChange > 0 ? '+' : ''}{dayChange.toFixed(2)}%
          </p>
        </div>
      </div>

      <div className="flex gap-6">
        {/* Equities table - left 65% */}
        <div className="flex-[65] min-w-0">
          <div className="border border-[#222] rounded-lg bg-[#111] p-4">
            <h2 className="text-sm font-medium text-[#e5e5e5] mb-3">Equities</h2>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-[#222]">
                    <th className="text-left py-2 px-3 text-[#888] text-xs cursor-pointer" onClick={() => handleSort('ticker')}>Symbol</th>
                    <th className="text-right py-2 px-3 text-[#888] text-xs">Price</th>
                    <th className="text-right py-2 px-3 text-[#888] text-xs cursor-pointer" onClick={() => handleSort('change_pct')}>Day %</th>
                    <th className="text-right py-2 px-3 text-[#888] text-xs cursor-pointer" onClick={() => handleSort('gain_pct')}>Total %</th>
                    <th className="text-right py-2 px-3 text-[#888] text-xs">Status</th>
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
                        <td className="py-2 px-3">
                          <span className={`font-mono font-medium ${isGolden ? 'text-[#f59e0b]' : isDead ? 'text-[#ff4444]' : 'text-[#e5e5e5]'}`}>{p.ticker}</span>
                        </td>
                        <td className="py-2 px-3 text-right text-[#e5e5e5]">{p.price != null ? `$${p.price.toFixed(2)}` : '—'}</td>
                        <td className="py-2 px-3 text-right">{pctCell(p.change_pct)}</td>
                        <td className="py-2 px-3 text-right">{pctCell(p.gain_pct, true)}</td>
                        <td className="py-2 px-3 text-right">
                          {isDead ? (
                            <span className="text-[10px] text-[#ff4444]">DEAD</span>
                          ) : gain > 100 ? (
                            <span className="text-[10px] text-[#00ff88]">🚀</span>
                          ) : gain > 0 ? (
                            <span className="text-[10px] text-[#00ff88]">●</span>
                          ) : (
                            <span className="text-[10px] text-[#ff4444]">●</span>
                          )}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </div>

        {/* Quick stats - right 35% */}
        <div className="flex-[35] space-y-6 min-w-0">
          <div className="border border-[#222] rounded-lg bg-[#111] p-4 space-y-3">
            <h2 className="text-sm font-medium text-[#e5e5e5] mb-2">Quick Stats</h2>
            <div className="flex justify-between text-sm">
              <span className="text-[#888]">Biggest Winner</span>
              <span className="text-[#00ff88] font-mono">{biggest?.ticker} {biggest?.gain_pct != null ? `+${biggest.gain_pct.toFixed(0)}%` : ''}</span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-[#888]">Worst Position</span>
              <span className="text-[#ff4444] font-mono">{worst?.ticker} {worst?.gain_pct != null ? `${worst.gain_pct.toFixed(0)}%` : ''}</span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-[#888]">In Green</span>
              <span className="text-[#00ff88]">{inGreen}/{portfolio.length}</span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-[#888]">In Red</span>
              <span className="text-[#ff4444]">{inRed}/{portfolio.length}</span>
            </div>
            <p className="text-[10px] text-[#555] pt-2 border-t border-[#222]">22 positions across E*TRADE main + Roth IRA</p>
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
                <span className="text-[#888]">2026 Contributions</span>
                <span className="text-[#e5e5e5]">$0 / $7,000</span>
              </div>
              <div className="w-full bg-[#222] rounded-full h-2">
                <div className="bg-[#333] h-2 rounded-full" style={{ width: '0%' }} />
              </div>
              <p className="text-[#f59e0b] text-xs mt-2">⚠️ Roth IRA underutilized — consider contributing $7,000 for tax-free compounding</p>
            </div>
          </div>
        </div>
      </div>

      {/* SPY 0DTE */}
      <div className="border border-[#222] rounded-lg bg-[#111] p-4">
        <h2 className="text-sm font-medium text-[#e5e5e5] mb-1">SPY Options Desk — Jordan | 0DTE Monitoring</h2>
        <p className="text-[#666] text-sm py-6 text-center">No active positions — log next group alert to start monitoring</p>
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
