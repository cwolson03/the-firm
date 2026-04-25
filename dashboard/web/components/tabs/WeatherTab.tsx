'use client'

import { useEffect, useState, useCallback } from 'react'
import { fetchWeather, WeatherResponse } from '@/lib/api'
import WinRateGauge from '@/components/shared/WinRateGauge'
import DataTable from '@/components/shared/DataTable'
import LoadingSpinner from '@/components/shared/LoadingSpinner'
import ErrorState from '@/components/shared/ErrorState'

export default function WeatherTab() {
  const [data, setData] = useState<WeatherResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)

  const load = useCallback(async () => {
    try {
      setData(await fetchWeather())
      setError(false)
    } catch { setError(true) }
    setLoading(false)
  }, [])

  useEffect(() => { load() }, [load])

  if (loading) return <div className="flex justify-center py-20"><LoadingSpinner size="lg" /></div>
  if (error || !data) return <ErrorState onRetry={load} />

  const cities = Object.entries(data.by_city || {})
    .map(([city, s]) => ({ city, ...s }))
    .sort((a, b) => b.total - a.total)
    .slice(0, 10)

  const sources = Object.entries(data.by_source || {})

  return (
    <div className="space-y-6">
      {/* Win Rate Hero */}
      <div className="border border-[#222] rounded-lg bg-[#111] p-6 flex items-center gap-8">
        <WinRateGauge rate={data.win_rate * 100} size={140} />
        <div>
          <h2 className="text-2xl font-bold text-[#e5e5e5]">Weather Performance</h2>
          <p className="text-[#888] text-sm mt-1">{data.total.toLocaleString()} paper trades since April 2026</p>
          <div className="flex gap-4 mt-3">
            <div className="flex items-center gap-2">
              <div className="w-3 h-3 rounded bg-[#00ff88]" />
              <span className="text-sm text-[#e5e5e5]">{data.wins} wins</span>
            </div>
            <div className="flex items-center gap-2">
              <div className="w-3 h-3 rounded bg-[#ff4444]" />
              <span className="text-sm text-[#e5e5e5]">{data.resolved - data.wins} losses</span>
            </div>
            <div className="flex items-center gap-2">
              <div className="w-3 h-3 rounded bg-[#888]" />
              <span className="text-sm text-[#e5e5e5]">{data.total - data.resolved} open</span>
            </div>
          </div>
          {/* Win/loss bar */}
          <div className="w-80 h-3 rounded-full bg-[#222] mt-3 overflow-hidden flex">
            <div className="bg-[#00ff88] h-full" style={{ width: `${data.resolved ? (data.wins / data.resolved) * 100 : 0}%` }} />
            <div className="bg-[#ff4444] h-full flex-1" />
          </div>
        </div>
      </div>

      {/* City Performance */}
      <div className="border border-[#222] rounded-lg bg-[#111] p-4">
        <h2 className="text-sm font-medium text-[#e5e5e5] mb-3">City Performance — Top 10</h2>
        <DataTable
          columns={[
            { key: 'city', label: 'City' },
            { key: 'total', label: 'Trades' },
            { key: 'win_rate', label: 'Win Rate', render: (v: number) => {
              const pct = (v * 100).toFixed(1)
              const c = v >= 0.5 ? '#00ff88' : v >= 0.35 ? '#f59e0b' : '#ff4444'
              return <span style={{ color: c }}>{pct}%</span>
            }},
            { key: 'edge_avg', label: 'Avg Edge', render: (v: number | null) => v != null ? `${(v * 100).toFixed(1)}%` : '—' },
          ]}
          data={cities}
          rowClassName={(row: any) => {
            if (cities.length && row.city === cities.reduce((a, b) => a.win_rate > b.win_rate ? a : b).city) return 'bg-[#00ff8808]'
            return ''
          }}
        />
      </div>

      {/* Source comparison */}
      <div className="grid grid-cols-2 gap-4">
        {sources.map(([name, s]) => (
          <div key={name} className="border border-[#222] rounded-lg bg-[#111] p-4">
            <h3 className="text-sm font-medium text-[#e5e5e5] mb-2">{name}</h3>
            <div className="space-y-2">
              <div className="flex justify-between text-sm">
                <span className="text-[#888]">Trades</span>
                <span className="text-[#e5e5e5]">{s.total}</span>
              </div>
              <div className="flex justify-between text-sm">
                <span className="text-[#888]">Win Rate</span>
                <span className="text-[#e5e5e5]">{(s.win_rate * 100).toFixed(1)}%</span>
              </div>
              <div className="w-full bg-[#222] rounded-full h-2 mt-1">
                <div className="bg-[#00ff88] h-2 rounded-full" style={{ width: `${s.win_rate * 100}%` }} />
              </div>
            </div>
            <p className="text-[10px] text-[#555] mt-2">
              {name.toLowerCase().includes('tomorrow') ? 'Primary forecast source (500 req/day limit)' : 'Free fallback — no limit'}
            </p>
          </div>
        ))}
        {sources.length === 0 && (
          <>
            <div className="border border-[#222] rounded-lg bg-[#111] p-4">
              <h3 className="text-sm font-medium text-[#e5e5e5] mb-2">Tomorrow.io</h3>
              <p className="text-[#666] text-xs">Primary forecast source (500 req/day limit)</p>
            </div>
            <div className="border border-[#222] rounded-lg bg-[#111] p-4">
              <h3 className="text-sm font-medium text-[#e5e5e5] mb-2">Open-Meteo</h3>
              <p className="text-[#666] text-xs">Free fallback — no limit</p>
            </div>
          </>
        )}
      </div>
      <p className="text-[10px] text-[#555]">Source logged per trade for calibration analysis</p>

      {/* Recent trades */}
      <div className="border border-[#222] rounded-lg bg-[#111] p-4">
        <h2 className="text-sm font-medium text-[#e5e5e5] mb-3">Recent Trades</h2>
        <DataTable
          columns={[
            { key: 'date', label: 'Date', render: (_: any, row: any) => row.date || row.timestamp || '—' },
            { key: 'city', label: 'City' },
            { key: 'market', label: 'Market' },
            { key: 'direction', label: 'Direction' },
            { key: 'forecast', label: 'Forecast', render: (v: any) => v != null ? String(v) : '—' },
            { key: 'result', label: 'Result', render: (v: string) => (
              <span className={v === 'WIN' ? 'text-[#00ff88]' : v === 'LOSS' ? 'text-[#ff4444]' : 'text-[#888]'}>{v || 'OPEN'}</span>
            )},
          ]}
          data={(data.recent || []).slice(0, 20)}
          rowClassName={(row: any) => row.result === 'WIN' ? 'bg-[#00ff8808]' : row.result === 'LOSS' ? 'bg-[#ff444408]' : ''}
        />
      </div>
    </div>
  )
}
