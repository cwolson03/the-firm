'use client'

import { useEffect, useState, useCallback } from 'react'
import { fetchPortfolio, fetchPositions, PortfolioItem, PositionItem } from '@/lib/api'
import LoadingSpinner from '@/components/shared/LoadingSpinner'
import ErrorState from '@/components/shared/ErrorState'

// demo mode: qty=1 hides absolute $ values while preserving % gains
const DEMO_MODE = process.env.NEXT_PUBLIC_PORTFOLIO_DEMO === 'true'

const PORTFOLIO = [
  { ticker: 'BRK.B', name: 'Berkshire Hathaway', qty: DEMO_MODE ? 1 : 15, avgCost: 349.65, sector: 'Finance' },
  { ticker: 'C', name: 'Citigroup', qty: DEMO_MODE ? 1 : 80, avgCost: 41.38, sector: 'Finance' },
  { ticker: 'DVN', name: 'Devon Energy', qty: DEMO_MODE ? 1 : 500, avgCost: 65.05, sector: 'Energy' },
  { ticker: 'FRCB', name: 'First Republic Bank', qty: DEMO_MODE ? 1 : 100, avgCost: 13.905, sector: 'Finance' },
  { ticker: 'GOOG', name: 'Alphabet (GOOG)', qty: DEMO_MODE ? 1 : 400, avgCost: 15.7036, sector: 'Tech' },
  { ticker: 'GOOGL', name: 'Alphabet (GOOGL)', qty: DEMO_MODE ? 1 : 400, avgCost: 15.7953, sector: 'Tech' },
  { ticker: 'LYFT', name: 'Lyft', qty: DEMO_MODE ? 1 : 100, avgCost: 57.99, sector: 'Tech' },
  { ticker: 'MCD', name: "McDonald's", qty: DEMO_MODE ? 1 : 50, avgCost: 259.125, sector: 'Consumer' },
  { ticker: 'MU', name: 'Micron Technology', qty: DEMO_MODE ? 1 : 100, avgCost: 101.8758, sector: 'Semis' },
  { ticker: 'NVDA', name: 'NVIDIA', qty: DEMO_MODE ? 1 : 210, avgCost: 41.517, sector: 'Semis' },
  { ticker: 'OKLO', name: 'Oklo Inc', qty: DEMO_MODE ? 1 : 175, avgCost: 97.8137, sector: 'Nuclear' },
  { ticker: 'PLTR', name: 'Palantir', qty: DEMO_MODE ? 1 : 100, avgCost: 162.20, sector: 'Tech' },
  { ticker: 'PYPL', name: 'PayPal', qty: DEMO_MODE ? 1 : 50, avgCost: 74.965, sector: 'Finance' },
  { ticker: 'SMR', name: 'NuScale Power', qty: DEMO_MODE ? 1 : 500, avgCost: 19.852, sector: 'Nuclear' },
  { ticker: 'SOBO', name: 'South Bow Corp', qty: DEMO_MODE ? 1 : 50, avgCost: 16.9128, sector: 'Energy' },
  { ticker: 'SPY', name: 'S&P 500 ETF', qty: DEMO_MODE ? 1 : 44, avgCost: 449.16, sector: 'Index' },
  { ticker: 'SWPPX', name: 'Schwab S&P 500 Index', qty: DEMO_MODE ? 1 : 164.55, avgCost: 13.8159, sector: 'Index' },
  { ticker: 'TRP', name: 'TC Energy', qty: DEMO_MODE ? 1 : 250, avgCost: 34.3974, sector: 'Energy' },
  { ticker: 'TSM', name: 'Taiwan Semiconductor', qty: DEMO_MODE ? 1 : 15, avgCost: 196.8807, sector: 'Semis' },
  { ticker: 'VFIAX', name: 'Vanguard 500 Index Admiral', qty: DEMO_MODE ? 1 : 71.112, avgCost: 439.0897, sector: 'Index' },
  { ticker: 'VTSAX', name: 'Vanguard Total Market', qty: DEMO_MODE ? 1 : 187.649, avgCost: 108.7681, sector: 'Index' },
  { ticker: 'WWD', name: 'Woodward Inc', qty: DEMO_MODE ? 1 : 450, avgCost: 24.116, sector: 'Industrial' },
]

const ROTH_IRA = {
  account: '-8418',
  ticker: 'VTSAX',
  name: 'Vanguard Total Market Admiral',
  shares: DEMO_MODE ? 1 : 72.5,
  avgCost: 108.7681,
  netValue: DEMO_MODE ? 0 : 12424.97,  // calculated from shares × price in demo
  contributions2026: 0,
  maxContribution: 7000,
  deadline: 'April 15, 2027',
}

const GOLDEN = ['GOOG', 'GOOGL', 'WWD']
const DEAD = ['FRCB']

const WATCHLIST_WHY: Record<string, { name: string; why: string }> = {
  AAPL: { name: 'Apple Inc', why: 'AI hardware cycle, iPhone refresh' },
  META: { name: 'Meta Platforms', why: 'AI infrastructure, ad revenue recovery' },
  AMZN: { name: 'Amazon.com', why: 'AWS cloud + AI, tariff-resistant revenue' },
  AMD: { name: 'Advanced Micro Devices', why: 'Data center GPUs, NVDA competitor' },
  MSFT: { name: 'Microsoft', why: 'Azure AI growth, Copilot monetization' },
}

export default function PortfolioTab() {
  const [portfolio, setPortfolio] = useState<PortfolioItem[]>([])
  const [spyPositions, setSpyPositions] = useState<PositionItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [sortKey, setSortKey] = useState<'gain_pct' | 'change_pct' | 'ticker' | 'value'>('gain_pct')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')
  const [editingWatchlist, setEditingWatchlist] = useState(false)
  const [watchlistText, setWatchlistText] = useState('')
  const [watchlistTickers, setWatchlistTickers] = useState<string[]>(['AAPL', 'META', 'AMZN', 'AMD', 'MSFT'])

  // Load watchlist from localStorage
  useEffect(() => {
    try {
      const saved = localStorage.getItem('portfolio-watchlist')
      if (saved) {
        const parsed = JSON.parse(saved)
        if (Array.isArray(parsed) && parsed.length > 0) {
          setWatchlistTickers(parsed)
        }
      }
    } catch { /* ignore */ }
  }, [])

  const load = useCallback(async () => {
    try {
      const [p, pos] = await Promise.all([
        fetchPortfolio(),
        fetchPositions().catch(() => [])
      ])
      setPortfolio(p)
      setSpyPositions(Array.isArray(pos) ? pos.filter((x: PositionItem) => (x.ticker || '').includes('SPY')) : [])
      setError(false)
    } catch { setError(true) }
    setLoading(false)
  }, [])

  useEffect(() => { load() }, [load])

  if (loading) return <div className="flex justify-center py-20"><LoadingSpinner size="lg" /></div>
  if (error) return <ErrorState onRetry={load} />

  // Build lookup from API data
  const priceMap: Record<string, PortfolioItem> = {}
  portfolio.forEach(p => {
    priceMap[p.ticker] = p
    // normalize: BRK-B ↔ BRK.B
    priceMap[p.ticker.replace('-', '.')] = p
    priceMap[p.ticker.replace('.', '-')] = p
  })

  // Enrich portfolio with live prices
  const enriched = PORTFOLIO.map(pos => {
    const live = priceMap[pos.ticker]
    const price = live?.price || 0
    const changePct = live?.change_pct || 0
    const gainPct = live?.gain_pct || (price > 0 && pos.avgCost > 0 ? ((price - pos.avgCost) / pos.avgCost) * 100 : 0)
    const value = price * pos.qty
    const costBasis = pos.avgCost * pos.qty
    const dollarGain = value - costBasis
    const prevPrice = changePct !== 0 ? price / (1 + changePct / 100) : price
    const dayDollarChange = (price - prevPrice) * pos.qty
    return {
      ...pos,
      price,
      changePct,
      gainPct,
      value,
      costBasis,
      dollarGain,
      dayDollarChange,
    }
  })

  const sorted = [...enriched].sort((a, b) => {
    switch (sortKey) {
      case 'value': return sortDir === 'asc' ? a.value - b.value : b.value - a.value
      case 'change_pct': return sortDir === 'asc' ? a.changePct - b.changePct : b.changePct - a.changePct
      case 'gain_pct': return sortDir === 'asc' ? a.gainPct - b.gainPct : b.gainPct - a.gainPct
      case 'ticker': return sortDir === 'asc' ? a.ticker.localeCompare(b.ticker) : b.ticker.localeCompare(a.ticker)
      default: return 0
    }
  })

  const totalValue = enriched.reduce((s, p) => s + p.value, 0)
  const totalCost = enriched.reduce((s, p) => s + p.costBasis, 0)
  const totalGain = totalValue - totalCost
  const totalGainPct = totalCost > 0 ? (totalGain / totalCost) * 100 : 0
  const totalDayGain = enriched.reduce((s, p) => s + p.dayDollarChange, 0)
  const totalDayGainPct = totalValue > 0 ? (totalDayGain / (totalValue - totalDayGain)) * 100 : 0
  const inGreen = enriched.filter(p => p.gainPct > 0).length
  const inRed = enriched.filter(p => p.gainPct <= 0).length

  // Largest day winner/loser
  const sortedByDay = [...enriched].sort((a, b) => b.dayDollarChange - a.dayDollarChange)
  const dayWinner = sortedByDay[0]
  const dayLoser = sortedByDay[sortedByDay.length - 1]

  // Sector breakdown
  const sectorTotals: Record<string, number> = {}
  enriched.forEach(p => { sectorTotals[p.sector] = (sectorTotals[p.sector] || 0) + p.value })

  // Roth IRA
  const vtsaxPrice = priceMap['VTSAX']?.price || (ROTH_IRA.netValue / ROTH_IRA.shares)
  const rothValue = ROTH_IRA.shares * vtsaxPrice
  const rothGainPct = ROTH_IRA.avgCost > 0 ? ((vtsaxPrice - ROTH_IRA.avgCost) / ROTH_IRA.avgCost) * 100 : 0

  const handleSort = (key: typeof sortKey) => {
    if (sortKey === key) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setSortKey(key); setSortDir('desc') }
  }

  const pctCell = (v: number, bold?: boolean) => {
    const c = v > 0 ? '#00ff88' : v < 0 ? '#ff4444' : '#888'
    return <span style={{ color: c }} className={bold ? 'font-bold' : ''}>{v > 0 ? '+' : ''}{v.toFixed(1)}%</span>
  }

  const dollarCell = (v: number) => {
    const c = v > 0 ? '#00ff88' : v < 0 ? '#ff4444' : '#888'
    return <span style={{ color: c }}>{v >= 0 ? '+' : ''}{Math.abs(v) >= 1000 ? `$${(v/1000).toFixed(1)}K` : `$${v.toFixed(0)}`}</span>
  }

  const saveWatchlist = () => {
    const tickers = watchlistText.split(/[,\s\n]+/).map(t => t.trim().toUpperCase()).filter(Boolean)
    if (tickers.length > 0) {
      setWatchlistTickers(tickers)
      localStorage.setItem('portfolio-watchlist', JSON.stringify(tickers))
    }
    setEditingWatchlist(false)
  }

  return (
    <div className="space-y-6">
      {/* Header strip */}
      <div className="border border-[#222] rounded-lg bg-[#111] p-4">
        <div className="flex items-center justify-between">
          <div>
            <span className="text-[#888] text-xs">Total Portfolio Value</span>
            <p className="text-3xl font-bold text-[#e5e5e5]">${totalValue.toLocaleString('en-US', { maximumFractionDigits: 0 })}</p>
          </div>
          <div className="text-center">
            <span className="text-[#888] text-xs">Unrealized Gain</span>
            <p className="text-xl font-bold" style={{ color: totalGain > 0 ? '#00ff88' : '#ff4444' }}>
              {totalGain >= 0 ? '+' : ''}${(totalGain / 1000).toFixed(1)}K ({totalGainPct > 0 ? '+' : ''}{totalGainPct.toFixed(1)}%)
            </p>
          </div>
          <div className="text-right">
            <span className="text-[#888] text-xs">Today</span>
            <p className="text-xl font-bold" style={{ color: totalDayGain >= 0 ? '#00ff88' : '#ff4444' }}>
              {totalDayGain >= 0 ? '+' : ''}${Math.abs(totalDayGain).toLocaleString('en-US', { maximumFractionDigits: 0 })} ({totalDayGainPct >= 0 ? '+' : ''}{totalDayGainPct.toFixed(2)}%)
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
                    <th className="text-left py-2 px-2 text-[#888] text-xs">Company</th>
                    <th className="text-right py-2 px-2 text-[#888] text-xs">Qty</th>
                    <th className="text-right py-2 px-2 text-[#888] text-xs">Price</th>
                    <th className="text-right py-2 px-2 text-[#888] text-xs cursor-pointer" onClick={() => handleSort('change_pct')}>Day %</th>
                    <th className="text-right py-2 px-2 text-[#888] text-xs cursor-pointer" onClick={() => handleSort('gain_pct')}>Total %</th>
                    <th className="text-right py-2 px-2 text-[#888] text-xs">Day $</th>
                    <th className="text-right py-2 px-2 text-[#888] text-xs">Total $</th>
                    <th className="text-right py-2 px-2 text-[#888] text-xs cursor-pointer" onClick={() => handleSort('value')}>Value $</th>
                  </tr>
                </thead>
                <tbody>
                  {sorted.map(p => {
                    const isGolden = GOLDEN.includes(p.ticker)
                    const isDead = DEAD.includes(p.ticker)
                    return (
                      <tr
                        key={p.ticker}
                        className={`border-b border-[#1a1a1a] hover:bg-[#1a1a1a] transition-colors ${
                          isGolden ? 'bg-[#f59e0b08]' : isDead ? 'bg-[#ff444408]' : ''
                        }`}
                        style={{ borderLeftWidth: 3, borderLeftColor: p.gainPct > 0 ? '#00ff8844' : p.gainPct < 0 ? '#ff444444' : '#33333344' }}
                      >
                        <td className="py-1.5 px-2">
                          <span className={`font-mono font-medium text-xs ${isGolden ? 'text-[#f59e0b]' : isDead ? 'text-[#ff4444]' : 'text-[#e5e5e5]'}`}>{p.ticker}</span>
                        </td>
                        <td className="py-1.5 px-2 text-[#888] text-xs truncate max-w-[120px]">{p.name}</td>
                        <td className="py-1.5 px-2 text-right text-[#888] text-xs">{Number.isInteger(p.qty) ? p.qty : p.qty.toFixed(2)}</td>
                        <td className="py-1.5 px-2 text-right text-[#e5e5e5] text-xs">{p.price > 0 ? `$${p.price.toFixed(2)}` : '—'}</td>
                        <td className="py-1.5 px-2 text-right text-xs">{pctCell(p.changePct)}</td>
                        <td className="py-1.5 px-2 text-right text-xs">{pctCell(p.gainPct, true)}</td>
                        <td className="py-1.5 px-2 text-right text-xs">{dollarCell(p.dayDollarChange)}</td>
                        <td className="py-1.5 px-2 text-right text-xs">{dollarCell(p.dollarGain)}</td>
                        <td className="py-1.5 px-2 text-right text-[#e5e5e5] text-xs font-medium">
                          {p.value >= 1000 ? `$${(p.value/1000).toFixed(1)}K` : `$${p.value.toFixed(0)}`}
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
        <div className="flex-[35] space-y-4 min-w-0">
          {/* Quick Stats */}
          <div className="border border-[#222] rounded-lg bg-[#111] p-4 space-y-3">
            <h2 className="text-sm font-medium text-[#e5e5e5] mb-2">Quick Stats</h2>
            <div className="flex justify-between text-sm">
              <span className="text-[#888]">Total Value</span>
              <span className="text-[#e5e5e5] font-mono font-bold">${totalValue.toLocaleString('en-US', { maximumFractionDigits: 0 })}</span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-[#888]">Today&apos;s Gain</span>
              <span className="font-mono" style={{ color: totalDayGain >= 0 ? '#00ff88' : '#ff4444' }}>
                {totalDayGain >= 0 ? '+' : ''}${Math.abs(totalDayGain).toLocaleString('en-US', { maximumFractionDigits: 0 })} ({totalDayGainPct >= 0 ? '+' : ''}{totalDayGainPct.toFixed(2)}%)
              </span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-[#888]">Unrealized Gain</span>
              <span className="font-mono" style={{ color: totalGain > 0 ? '#00ff88' : '#ff4444' }}>
                {totalGain >= 0 ? '+' : ''}${(totalGain / 1000).toFixed(1)}K
              </span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-[#888]">Day Winner</span>
              <span className="text-[#00ff88] font-mono text-xs">
                {dayWinner ? `${dayWinner.ticker} ${dayWinner.dayDollarChange >= 0 ? '+' : ''}$${Math.abs(dayWinner.dayDollarChange).toFixed(0)}` : '—'}
              </span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-[#888]">Day Loser</span>
              <span className="text-[#ff4444] font-mono text-xs">
                {dayLoser ? `${dayLoser.ticker} ${dayLoser.dayDollarChange >= 0 ? '+' : ''}$${dayLoser.dayDollarChange.toFixed(0)}` : '—'}
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
            <div className="flex items-center gap-2 mb-2">
              <h2 className="text-sm font-medium text-[#e5e5e5]">Roth IRA</h2>
              <span className="text-[10px] text-[#666]">Acct {ROTH_IRA.account}</span>
            </div>
            <p className="text-[10px] text-[#f59e0b] mb-2">⚠️ Separate account from main brokerage</p>
            <div className="space-y-2">
              <div className="flex justify-between text-sm">
                <span className="text-[#888]">Position</span>
                <span className="text-[#e5e5e5] font-mono">{ROTH_IRA.ticker}</span>
              </div>
              <div className="flex justify-between text-sm">
                <span className="text-[#888]">Shares</span>
                <span className="text-[#e5e5e5]">{ROTH_IRA.shares}</span>
              </div>
              <div className="flex justify-between text-sm">
                <span className="text-[#888]">Price</span>
                <span className="text-[#e5e5e5] font-mono">${vtsaxPrice.toFixed(2)}/share</span>
              </div>
              <div className="flex justify-between text-sm">
                <span className="text-[#888]">Value</span>
                <span className="text-[#00ff88] font-mono">${rothValue.toLocaleString('en-US', { maximumFractionDigits: 2 })}</span>
              </div>
              <div className="flex justify-between text-sm">
                <span className="text-[#888]">Return</span>
                <span className="font-mono" style={{ color: rothGainPct >= 0 ? '#00ff88' : '#ff4444' }}>
                  {rothGainPct >= 0 ? '+' : ''}{rothGainPct.toFixed(1)}%
                </span>
              </div>
              <div className="flex justify-between text-sm">
                <span className="text-[#888]">2026 Contributions</span>
                <span className="text-[#e5e5e5]">${ROTH_IRA.contributions2026} / ${ROTH_IRA.maxContribution.toLocaleString()}</span>
              </div>
              <div className="w-full bg-[#222] rounded-full h-2">
                <div className="bg-[#333] h-2 rounded-full" style={{ width: `${(ROTH_IRA.contributions2026 / ROTH_IRA.maxContribution) * 100}%` }} />
              </div>
              <p className="text-[#f59e0b] text-xs mt-2">⚠️ $7,000 contribution deadline: {ROTH_IRA.deadline}</p>
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
                {spyPositions.map((p: PositionItem) => (
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
          <button
            onClick={() => {
              if (editingWatchlist) {
                saveWatchlist()
              } else {
                setWatchlistText(watchlistTickers.join(', '))
                setEditingWatchlist(true)
              }
            }}
            className="px-3 py-1 rounded bg-[#1a1a1a] border border-[#333] text-[#888] text-xs hover:border-[#00ff88] hover:text-[#00ff88] transition-colors"
          >
            {editingWatchlist ? 'Save' : 'Edit watchlist'}
          </button>
        </div>

        {editingWatchlist && (
          <div className="mb-3">
            <textarea
              value={watchlistText}
              onChange={e => setWatchlistText(e.target.value)}
              placeholder="Enter tickers separated by commas (e.g. AAPL, META, AMZN)"
              className="w-full bg-[#0a0a0a] border border-[#333] rounded px-3 py-2 text-sm text-[#e5e5e5] placeholder-[#555] h-16 resize-none"
            />
            <button
              onClick={() => setEditingWatchlist(false)}
              className="text-[10px] text-[#666] mt-1 hover:text-[#888]"
            >Cancel</button>
          </div>
        )}

        <div className="space-y-2">
          {watchlistTickers.map(t => {
            const info = WATCHLIST_WHY[t]
            const live = priceMap[t]
            return (
              <div key={t} className="bg-[#0a0a0a] rounded p-3 flex items-center gap-4">
                <div className="flex-shrink-0 w-16">
                  <span className="text-[#00ff88] font-mono font-medium text-sm">{t}</span>
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-xs text-[#e5e5e5]">{info?.name || t}</p>
                  <p className="text-[10px] text-[#666] mt-0.5">{info?.why || 'Watching'}</p>
                </div>
                <div className="text-right flex-shrink-0">
                  {live?.price ? (
                    <>
                      <p className="text-xs text-[#e5e5e5] font-mono">${live.price.toFixed(2)}</p>
                      <p className="text-[10px] font-mono" style={{ color: (live.change_pct || 0) >= 0 ? '#00ff88' : '#ff4444' }}>
                        {(live.change_pct || 0) >= 0 ? '+' : ''}{(live.change_pct || 0).toFixed(1)}%
                      </p>
                    </>
                  ) : (
                    <p className="text-[10px] text-[#666]">No data</p>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
