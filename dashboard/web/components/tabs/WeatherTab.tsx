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
    .map(([name, s]) => ({ name: name === 'unknown' || name === 'null' || !name ? 'Legacy' : name, ...s }))

  const openTrades = data.open || []
  const recentResolved = data.recent || []

  return (
    <div className="space-y-6">
      {/* Win Rate Hero */}
      <div className="border border-[#222] rounded-lg bg-[#111] p-6 flex items-center gap-8">
        <WinRateGauge rate={data.win_rate} size={140} />
        <div>
          <h2 className="text-2xl font-bold text-[#e5e5e5]">Weather Performance</h2>
          <p className="text-[#888] text-sm mt-1">{data.total.toLocaleString()} paper trades (post-optimization, Apr 19+)</p>
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
          <p className="text-[10px] text-[#555] mt-2">Stats reflect post-optimization data (Apr 19+). Earlier trades excluded — parameter tuning period.</p>
        </div>
      </div>

      {/* Open Positions */}
      <div className="border border-[#222] rounded-lg bg-[#111] p-4">
        <div className="flex items-center gap-2 mb-3">
          <h2 className="text-sm font-medium text-[#e5e5e5]">Open Positions</h2>
          <span className="px-2 py-0.5 rounded-full bg-[#38bdf820] text-[#38bdf8] text-[10px] font-medium">{openTrades.length}</span>
        </div>
        {openTrades.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-[#222]">
                  <th className="text-left py-2 px-2 text-[#888]">City</th>
                  <th className="text-left py-2 px-2 text-[#888]">Ticker</th>
                  <th className="text-center py-2 px-2 text-[#888]">Direction</th>
                  <th className="text-right py-2 px-2 text-[#888]">Edge</th>
                  <th className="text-right py-2 px-2 text-[#888]">Forecast</th>
                  <th className="text-right py-2 px-2 text-[#888]">Time</th>
                </tr>
              </thead>
              <tbody>
                {openTrades.slice(0, 15).map((t: any, i: number) => (
                  <tr key={i} className="border-b border-[#1a1a1a]">
                    <td className="py-1.5 px-2 text-[#e5e5e5]">{t.city_name || t.city || t.location || '—'}</td>
                    <td className="py-1.5 px-2 text-[#888] font-mono">{t.market || t.ticker || '—'}</td>
                    <td className="py-1.5 px-2 text-center">
                      <span className={`px-1.5 py-0.5 rounded text-[10px] ${
                        (t.direction || '').toUpperCase() === 'YES' ? 'bg-[#00ff8820] text-[#00ff88]' : 'bg-[#ff444420] text-[#ff4444]'
                      }`}>{(t.direction || '—').toUpperCase()}</span>
                    </td>
                    <td className="py-1.5 px-2 text-right text-[#e5e5e5]">{t.edge != null ? `${(t.edge * 100).toFixed(1)}%` : '—'}</td>
                    <td className="py-1.5 px-2 text-right text-[#888]">{t.forecast != null ? String(t.forecast) : '—'}</td>
                    <td className="py-1.5 px-2 text-right text-[#555]">{t.date || t.timestamp || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-[#666] text-sm py-4 text-center">No open weather positions — scanner running every 3 min.</p>
        )}
      </div>

      {/* City Performance */}
      <div className="border border-[#222] rounded-lg bg-[#111] p-4">
        <h2 className="text-sm font-medium text-[#e5e5e5] mb-3">City Performance — Top 10</h2>
        <DataTable
          columns={[
            { key: 'city', label: 'City' },
            { key: 'total', label: 'Trades' },
            { key: 'win_rate', label: 'Win Rate', render: (v: number) => {
              const c = v >= 50 ? '#00ff88' : v >= 35 ? '#f59e0b' : '#ff4444'
              return <span style={{ color: c }}>{v.toFixed(1)}%</span>
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
        {sources.map(s => (
          <div key={s.name} className="border border-[#222] rounded-lg bg-[#111] p-4">
            <h3 className="text-sm font-medium text-[#e5e5e5] mb-2">{s.name}</h3>
            <div className="space-y-2">
              <div className="flex justify-between text-sm">
                <span className="text-[#888]">Trades</span>
                <span className="text-[#e5e5e5]">{s.total}</span>
              </div>
              <div className="flex justify-between text-sm">
                <span className="text-[#888]">Win Rate</span>
                <span className="text-[#e5e5e5]">{s.win_rate.toFixed(1)}%</span>
              </div>
              <div className="w-full bg-[#222] rounded-full h-2 mt-1">
                <div className="bg-[#00ff88] h-2 rounded-full" style={{ width: `${s.win_rate}%` }} />
              </div>
            </div>
            <p className="text-[10px] text-[#555] mt-2">
              {s.name.toLowerCase().includes('tomorrow') ? 'Primary forecast source (500 req/day limit)' :
               s.name === 'Legacy' ? 'Pre-source-tracking trades' : 'Free fallback — no limit'}
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

      {/* Recent resolved trades */}
      <div className="border border-[#222] rounded-lg bg-[#111] p-4">
        <h2 className="text-sm font-medium text-[#e5e5e5] mb-3">Recent Resolved Trades</h2>
        <DataTable
          columns={[
            { key: 'date', label: 'Date', render: (_: any, row: any) => (row.date || row.timestamp || '—').slice(0, 10) },
            { key: 'city', label: 'City', render: (_: any, row: any) => row.city_name || row.city || '—' },
            { key: 'market', label: 'Market', render: (_: any, row: any) => <span className="font-mono text-[10px]">{row.market || row.ticker || '—'}</span> },
            { key: 'direction', label: 'Direction', render: (_: any, row: any) => (
              <span className={`px-1.5 py-0.5 rounded text-[10px] ${
                (row.direction || '').toUpperCase() === 'YES' ? 'bg-[#00ff8820] text-[#00ff88]' : 'bg-[#ff444420] text-[#ff4444]'
              }`}>{(row.direction || '—').toUpperCase()}</span>
            )},
            { key: 'forecast', label: 'Forecast', render: (v: any) => v != null ? String(v) : '—' },
            { key: 'actual', label: 'Actual', render: (_: any, row: any) => row.actual != null ? String(row.actual) : '—' },
            { key: 'status', label: 'Result', render: (_: any, row: any) => (
              <span className={`px-2 py-0.5 rounded text-[10px] font-medium ${
                row.status === 'WIN' ? 'bg-[#00ff8820] text-[#00ff88]' : 'bg-[#ff444420] text-[#ff4444]'
              }`}>{row.status}</span>
            )},
            { key: 'forecast_source', label: 'Source', render: (v: any) => (
              <span className="text-[10px] text-[#666]">{v || 'Legacy'}</span>
            )},
          ]}
          data={recentResolved.slice(0, 20)}
          rowClassName={(row: any) => row.status === 'WIN' ? 'bg-[#00ff8808]' : row.status === 'LOSS' ? 'bg-[#ff444408]' : ''}
        />
      </div>
    </div>
  )
}
