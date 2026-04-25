'use client'

import { useEffect, useState, useCallback } from 'react'
import { fetchBrad, BradResponse } from '@/lib/api'
import StatCard from '@/components/shared/StatCard'
import DataTable from '@/components/shared/DataTable'
import LoadingSpinner from '@/components/shared/LoadingSpinner'
import ErrorState from '@/components/shared/ErrorState'

const STRATEGY_META: Record<string, { name: string; desc: string }> = {
  S1: { name: 'Live Game Winners', desc: 'Real-time game state analysis' },
  S2: { name: 'Spread/Props', desc: 'Line value + prop bets' },
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

  if (loading) return <div className="flex justify-center py-20"><LoadingSpinner size="lg" /></div>
  if (error || !data) return <ErrorState onRetry={load} />

  const bestStrat = Object.entries(data.by_strategy || {}).sort((a, b) => b[1].win_rate - a[1].win_rate)[0]

  return (
    <div className="space-y-6">
      {/* Performance overview */}
      <div className="grid grid-cols-3 gap-4">
        <StatCard label="Total Paper Trades" value={data.total} color="#f59e0b" />
        <StatCard label="Win Rate" value={`${(data.win_rate * 100).toFixed(1)}%`} color={data.win_rate >= 0.5 ? '#00ff88' : '#f59e0b'} />
        <StatCard label="Best Strategy" value={bestStrat ? bestStrat[0] : '—'} sub={bestStrat ? `${(bestStrat[1].win_rate * 100).toFixed(1)}% win rate` : ''} color="#00ff88" />
      </div>

      {/* Strategy breakdown */}
      <div className="grid grid-cols-3 gap-4">
        {Object.entries(data.by_strategy || {}).map(([key, s]) => {
          const meta = STRATEGY_META[key] || { name: key, desc: '' }
          return (
            <div key={key} className="border border-[#222] rounded-lg bg-[#111] p-4">
              <h3 className="text-sm font-medium text-[#e5e5e5] mb-1">{key} — {meta.name}</h3>
              <p className="text-[10px] text-[#666] mb-3">{meta.desc}</p>
              <div className="flex justify-between text-sm mb-2">
                <span className="text-[#888]">{s.total} trades</span>
                <span className="text-[#e5e5e5]">{(s.win_rate * 100).toFixed(1)}%</span>
              </div>
              <div className="w-full bg-[#222] rounded-full h-2">
                <div className="bg-[#00ff88] h-2 rounded-full transition-all" style={{ width: `${s.win_rate * 100}%` }} />
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

      {/* Personal sportsbook placeholder */}
      <div className="border border-dashed border-[#333] rounded-lg bg-[#111] p-6 opacity-60">
        <h2 className="text-sm font-medium text-[#e5e5e5] mb-2">Personal Accounts — FanDuel, DraftKings</h2>
        <p className="text-[#666] text-sm">Integration coming soon — manual tracking enabled</p>
        <p className="text-[10px] text-[#555] mt-2">Promo tracking and personal bet history — future feature</p>
      </div>

      {/* Recent trades */}
      <div className="border border-[#222] rounded-lg bg-[#111] p-4">
        <h2 className="text-sm font-medium text-[#e5e5e5] mb-3">Recent Trades</h2>
        <DataTable
          columns={[
            { key: 'date', label: 'Date', render: (_: any, row: any) => row.date || row.timestamp || '—' },
            { key: 'market', label: 'Market' },
            { key: 'strategy', label: 'Strategy' },
            { key: 'side', label: 'Side' },
            { key: 'result', label: 'Result', render: (v: string) => (
              <span className={v === 'WIN' ? 'text-[#00ff88]' : v === 'LOSS' ? 'text-[#ff4444]' : 'text-[#888]'}>{v || 'OPEN'}</span>
            )},
          ]}
          data={(data.recent || []).slice(0, 10)}
        />
      </div>
    </div>
  )
}
