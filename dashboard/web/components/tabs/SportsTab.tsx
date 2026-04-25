'use client'

import { useEffect, useState, useCallback } from 'react'
import { fetchBrad, BradResponse } from '@/lib/api'
import StatCard from '@/components/shared/StatCard'
import DataTable from '@/components/shared/DataTable'
import LoadingSpinner from '@/components/shared/LoadingSpinner'
import ErrorState from '@/components/shared/ErrorState'

const STRATEGY_META: Record<string, { name: string; desc: string }> = {
  S1: { name: 'Live Game Winners', desc: 'Real-time game state analysis' },
  S2: { name: 'Spread & Props', desc: 'Line value + prop bets' },
  S3: { name: 'Tournament Outrights', desc: 'Futures and tournament picks' },
}

export default function SportsTab() {
  const [data, setData] = useState<BradResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)

  const load = useCallback(async () => {
    try {
      setData(await fetchBrad())
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
  if (error || !data) return <ErrorState onRetry={load} />

  const bestStrat = Object.entries(data.by_strategy || {}).sort((a, b) => b[1].win_rate - a[1].win_rate)[0]
  const openTrades = data.open || []
  const recentResolved = data.recent || []

  return (
    <div className="space-y-6">
      {/* Performance overview */}
      <div className="grid grid-cols-3 gap-4">
        <StatCard label="Total Paper Trades" value={data.total} color="#f59e0b" />
        <StatCard label="Win Rate" value={`${data.win_rate.toFixed(1)}%`} color={data.win_rate >= 50 ? '#00ff88' : '#f59e0b'} />
        <StatCard label="Best Strategy" value={bestStrat ? (STRATEGY_META[bestStrat[0]]?.name || bestStrat[0]) : '—'} sub={bestStrat ? `${bestStrat[1].win_rate.toFixed(1)}% win rate` : ''} color="#00ff88" />
      </div>

      {/* Strategy breakdown - compact side by side */}
      <div className="grid grid-cols-3 gap-3">
        {Object.entries(data.by_strategy || {}).map(([key, s]) => {
          const meta = STRATEGY_META[key] || { name: key, desc: '' }
          return (
            <div key={key} className="border border-[#222] rounded-lg bg-[#111] p-3">
              <div className="flex items-center gap-2 mb-1">
                <span className="text-[10px] text-[#666] font-mono">{key}</span>
                <span className="text-xs font-medium text-[#e5e5e5]">{meta.name}</span>
              </div>
              <p className="text-[10px] text-[#555] mb-2">{meta.desc}</p>
              <div className="flex justify-between text-xs mb-1.5">
                <span className="text-[#888]">{s.total} trades</span>
                <span className="text-[#e5e5e5] font-medium">{s.win_rate.toFixed(1)}%</span>
              </div>
              <div className="w-full bg-[#222] rounded-full h-1.5">
                <div className="bg-[#00ff88] h-1.5 rounded-full transition-all" style={{ width: `${s.win_rate}%` }} />
              </div>
            </div>
          )
        })}
        {!Object.keys(data.by_strategy || {}).length && (
          <div className="col-span-3 border border-[#222] rounded-lg bg-[#111] p-6 text-center text-[#666] text-sm">
            Strategy data will populate as Brad places trades
          </div>
        )}
      </div>

      {/* Open Positions */}
      <div className="border border-[#222] rounded-lg bg-[#111] p-4">
        <div className="flex items-center gap-2 mb-3">
          <h2 className="text-sm font-medium text-[#e5e5e5]">Open Positions</h2>
          <span className="px-2 py-0.5 rounded-full bg-[#f59e0b20] text-[#f59e0b] text-[10px] font-medium">{openTrades.length}</span>
        </div>
        {openTrades.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-[#222]">
                  <th className="text-left py-2 px-2 text-[#888]">Title</th>
                  <th className="text-center py-2 px-2 text-[#888]">Strategy</th>
                  <th className="text-right py-2 px-2 text-[#888]">Bid Price</th>
                  <th className="text-center py-2 px-2 text-[#888]">Status</th>
                </tr>
              </thead>
              <tbody>
                {openTrades.map((t: any, i: number) => {
                  const title = (t.title || t.market || t.ticker || '—').slice(0, 50)
                  const status = (t.status || 'open').toLowerCase()
                  return (
                    <tr key={i} className="border-b border-[#1a1a1a]">
                      <td className="py-1.5 px-2 text-[#e5e5e5]">{title}</td>
                      <td className="py-1.5 px-2 text-center">
                        <span className="text-[10px] text-[#888] font-mono">{t.strategy || '—'}</span>
                      </td>
                      <td className="py-1.5 px-2 text-right text-[#e5e5e5] font-mono">
                        {t.bid_price != null ? `${t.bid_price}¢` : t.price != null ? `${t.price}¢` : '—'}
                      </td>
                      <td className="py-1.5 px-2 text-center">
                        <span className={`px-2 py-0.5 rounded text-[10px] font-medium ${
                          status === 'filled' ? 'bg-[#00ff8820] text-[#00ff88]' : 'bg-[#f59e0b20] text-[#f59e0b]'
                        }`}>{status.toUpperCase()}</span>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-[#666] text-sm py-4 text-center">No active stink bids</p>
        )}
      </div>

      {/* Personal sportsbook placeholder */}
      <div className="border border-dashed border-[#333] rounded-lg bg-[#111] p-4 opacity-60">
        <h2 className="text-sm font-medium text-[#e5e5e5] mb-1">Personal Accounts — FanDuel, DraftKings</h2>
        <p className="text-[#666] text-xs">Integration coming soon — manual tracking enabled</p>
      </div>

      {/* Recent resolved trades */}
      <div className="border border-[#222] rounded-lg bg-[#111] p-4">
        <h2 className="text-sm font-medium text-[#e5e5e5] mb-3">Recent Resolved Trades</h2>
        <DataTable
          columns={[
            { key: 'date', label: 'Date', render: (_: any, row: any) => (row.date || row.timestamp || '—').slice(0, 10) },
            { key: 'market', label: 'Market', render: (_: any, row: any) => (
              <span className="truncate block max-w-[200px]">{row.title || row.market || '—'}</span>
            )},
            { key: 'strategy', label: 'Strategy', render: (_: any, row: any) => {
              const s = row.strategy || '—'
              return <span className="font-mono">{STRATEGY_META[s]?.name || s}</span>
            }},
            { key: 'bid', label: 'Bid', render: (_: any, row: any) => row.bid_price != null ? `${row.bid_price}¢` : '—' },
            { key: 'result', label: 'Result', render: (_: any, row: any) => {
              const status = row.status || ''
              const isWin = status.includes('win')
              return (
                <span className={`px-2 py-0.5 rounded text-[10px] font-medium ${
                  isWin ? 'bg-[#00ff8820] text-[#00ff88]' : 'bg-[#ff444420] text-[#ff4444]'
                }`}>{isWin ? 'WIN' : 'LOSS'}</span>
              )
            }},
          ]}
          data={recentResolved.slice(0, 20)}
        />
        {recentResolved.length === 0 && (
          <p className="text-[#666] text-sm text-center py-4">No resolved trades yet — Brad is placing stink bids</p>
        )}
      </div>
    </div>
  )
}
