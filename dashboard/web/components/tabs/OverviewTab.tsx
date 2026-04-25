'use client'

import { useEffect, useState, useCallback } from 'react'
import { fetchStatus, fetchActivity, fetchPositions, fetchWeather, fetchBrad, StatusResponse, ActivityEntry, PositionItem } from '@/lib/api'
import StatCard from '@/components/shared/StatCard'
import LoadingSpinner from '@/components/shared/LoadingSpinner'
import ErrorState from '@/components/shared/ErrorState'

const AGENT_META: Record<string, { icon: string; name: string; interval: string }> = {
  donnie: { icon: '⚡', name: 'Economics', interval: 'Every 2h' },
  weather: { icon: '🌤️', name: 'Weather', interval: 'Every 3m' },
  brad: { icon: '🏈', name: 'Sports', interval: 'Every 15m' },
  rugrat: { icon: '🏛️', name: 'Intelligence', interval: 'Every 4h' },
  jordan: { icon: '📈', name: 'Portfolio', interval: 'Every 15m' },
  supervisor: { icon: '🔧', name: 'System', interval: 'Every 30m' },
}

const AGENT_COLORS: Record<string, string> = {
  donnie: '#00ff88',
  weather: '#38bdf8',
  brad: '#f59e0b',
  rugrat: '#a78bfa',
  jordan: '#fb923c',
  supervisor: '#888',
  mark_hanna: '#ec4899',
}

const UPCOMING_EVENTS = [
  { date: '2026-04-30', label: 'Q1 GDP Advance + Core PCE', tag: '9 positions resolve' },
  { date: '2026-05-02', label: 'Non-Farm Payrolls (April)', tag: 'watching' },
  { date: '2026-05-13', label: 'CPI Release (April)', tag: 'watching' },
]

function timeAgo(ts: number): string {
  const diff = (Date.now() / 1000) - ts
  if (diff < 60) return 'just now'
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

const AGENT_MAX_AGE: Record<string, number> = {
  donnie: 10800,    // 3h (runs every 2h)
  weather: 600,     // 10m (runs every 3m)
  brad: 2700,       // 45m (runs every 15m)
  rugrat: 18000,    // 5h (runs every 4h)
  jordan: 2700,     // 45m (runs every 15m)
  supervisor: 3600, // 1h (runs every 30m)
}

function statusColor(ts: number, key?: string): string {
  const diff = (Date.now() / 1000) - ts
  const maxAge = key ? (AGENT_MAX_AGE[key] || 3600) : 1800
  if (diff < maxAge * 0.5) return '#00ff88'   // green: within half interval
  if (diff < maxAge * 1.5) return '#f59e0b'   // yellow: within 1.5x interval  
  return '#666'                                // gray: overdue
}

function formatUptime(s: number): string {
  const d = Math.floor(s / 86400)
  const h = Math.floor((s % 86400) / 3600)
  const m = Math.floor((s % 3600) / 60)
  return `${d}d ${h}h ${m}m`
}

function daysUntil(dateStr: string): number {
  const now = new Date()
  const target = new Date(dateStr + 'T00:00:00Z')
  return Math.max(0, Math.ceil((target.getTime() - now.getTime()) / 86400000))
}

export default function OverviewTab() {
  const [status, setStatus] = useState<StatusResponse | null>(null)
  const [activity, setActivity] = useState<ActivityEntry[]>([])
  const [posCount, setPosCount] = useState<number>(0)
  const [paperTradesActive, setPaperTradesActive] = useState<number>(0)
  const [systemsOnline, setSystemsOnline] = useState<number>(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)

  const load = useCallback(async () => {
    try {
      const [s, a, p, w, b] = await Promise.all([
        fetchStatus(), fetchActivity(20), fetchPositions(),
        fetchWeather().catch(() => null), fetchBrad().catch(() => null)
      ])
      setStatus(s)
      setActivity(a)
      setPosCount(Array.isArray(p) ? p.length : 0)

      // Paper trades = open weather + open sports
      const weatherOpen = w?.open?.length || 0
      const bradOpen = b?.open?.length || 0
      setPaperTradesActive(weatherOpen + bradOpen)

      // Systems online = service running + agents active in last interval
      let online = s.service_running ? 2 : 0 // firm + api
      const agents = s.agents || {}
      const now = Date.now() / 1000
      const getTs = (a: any) => a?.last_run_ts || (a?.last_run ? new Date(a.last_run).getTime() / 1000 : 0)
      if (getTs(agents.donnie) && (now - getTs(agents.donnie) < 10800)) online++
      if (getTs(agents.weather) && (now - getTs(agents.weather) < 600)) online++    // 10m — runs every 3m
      if (getTs(agents.brad) && (now - getTs(agents.brad) < 2700)) online++          // 45m — runs every 15m
      if (getTs(agents.rugrat) && (now - getTs(agents.rugrat) < 18000)) online++     // 5h — runs every 4h
      if (getTs(agents.jordan) && (now - getTs(agents.jordan) < 2700)) online++      // 45m — runs every 15m
      if (getTs(agents.supervisor) && (now - getTs(agents.supervisor) < 3600)) online++ // 1h — runs every 30m
      setSystemsOnline(online)
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
  if (error || !status) return <ErrorState onRetry={load} />

  const nextEvent = UPCOMING_EVENTS.find(e => daysUntil(e.date) > 0) || UPCOMING_EVENTS[0]

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-4xl font-bold text-[#00ff88]">THE FIRM</h1>
          <p className="text-[#888] text-sm mt-1">Autonomous Trading Intelligence System — Live</p>
        </div>
        <div className="flex items-center gap-3">
          <span className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-medium ${
            status.service_running ? 'bg-[#00ff8820] text-[#00ff88]' : 'bg-[#ff444420] text-[#ff4444]'
          }`}>
            <span className={`w-2 h-2 rounded-full ${status.service_running ? 'bg-[#00ff88]' : 'bg-[#ff4444]'}`} />
            {status.service_running ? 'Running' : 'Down'}
          </span>
          <span className="text-[#888] text-xs">{formatUptime(status.uptime_seconds)}</span>
        </div>
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-4 gap-4">
        <StatCard label="Open Kalshi Positions" value={posCount} color="#00ff88" />
        <StatCard label="Weather Win Rate" value={`${status.weather_win_rate.toFixed(1)}%`} color="#38bdf8" />
        <StatCard label="Paper Trades Active" value={paperTradesActive} color="#f59e0b" />
        <StatCard label="Systems Online" value={`${systemsOnline}/8`} color={systemsOnline >= 6 ? '#00ff88' : '#f59e0b'} />
      </div>

      {/* Agent grid */}
      <div>
        <h2 className="text-sm font-medium text-[#888] mb-3">AGENT STATUS</h2>
        <div className="grid grid-cols-3 gap-3">
          {Object.entries(AGENT_META).map(([key, meta]) => {
            const agent = status.agents?.[key]
            const ts = agent?.last_run_ts || (agent?.last_run ? new Date(agent.last_run).getTime() / 1000 : 0)
            return (
              <div key={key} className="border border-[#222] rounded-lg bg-[#111] p-4 flex items-center gap-3">
                <span className="text-2xl">{meta.icon}</span>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-[#e5e5e5] font-medium text-sm">{meta.name}</span>
                    <span className="w-2 h-2 rounded-full" style={{ background: ts ? statusColor(ts, key) : '#666' }} />
                  </div>
                  <div className="text-[#666] text-xs">
                    {ts ? `Last run: ${timeAgo(ts)}` : 'No data'}
                    <span className="ml-2 text-[#555]">{meta.interval}</span>
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {/* Activity feed */}
      <div className="border border-[#222] rounded-lg bg-[#111] p-4">
        <div className="flex items-center gap-2 mb-3">
          <h2 className="text-sm font-medium text-[#e5e5e5]">Live Activity</h2>
          <span className="flex items-center gap-1 px-2 py-0.5 rounded-full bg-[#00ff8820] text-[#00ff88] text-[10px] font-medium">
            <span className="w-1.5 h-1.5 rounded-full bg-[#00ff88] animate-pulse" />
            Live
          </span>
        </div>
        <div className="space-y-1 max-h-[400px] overflow-y-auto font-mono text-xs">
          {activity.map((a, i) => (
            <div key={i} className="flex items-start gap-2 py-1 border-b border-[#1a1a1a]">
              <span className="w-2 h-2 rounded-full mt-1 shrink-0" style={{ background: AGENT_COLORS[a.agent] || '#666' }} />
              <span className="text-[#555] shrink-0">{a.timestamp}</span>
              <span className="text-[#888] shrink-0 uppercase w-16">{a.agent}</span>
              <span className="text-[#ccc] truncate">{a.message}</span>
            </div>
          ))}
          {!activity.length && <p className="text-[#666]">No recent activity</p>}
        </div>
      </div>

      {/* Next Event Countdown */}
      <div className="border border-[#222] rounded-lg bg-[#111] p-4">
        <h2 className="text-sm font-medium text-[#888] mb-3">UPCOMING CATALYSTS</h2>
        <div className="flex items-center gap-6">
          {UPCOMING_EVENTS.map(evt => {
            const days = daysUntil(evt.date)
            const isNext = evt === nextEvent
            return (
              <div key={evt.date} className={`flex-1 rounded-lg p-3 ${isNext ? 'bg-[#00ff8810] border border-[#00ff8830]' : 'bg-[#0a0a0a]'}`}>
                <div className="flex items-center justify-between mb-1">
                  <span className={`text-xs font-medium ${isNext ? 'text-[#00ff88]' : 'text-[#888]'}`}>
                    {new Date(evt.date + 'T00:00:00Z').toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
                  </span>
                  <span className={`text-xs font-bold ${isNext ? 'text-[#00ff88]' : 'text-[#666]'}`}>
                    {days === 0 ? 'TODAY' : `${days}d`}
                  </span>
                </div>
                <p className="text-xs text-[#e5e5e5]">{evt.label}</p>
                <p className="text-[10px] text-[#666] mt-1">{evt.tag}</p>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
