'use client'

import { useEffect, useState, useCallback } from 'react'
import { fetchStatus, fetchActivity, fetchPositions, fetchWeather, fetchBrad, fetchEval, StatusResponse, ActivityEntry, PositionItem, EvalRecord } from '@/lib/api'
import LoadingSpinner from '@/components/shared/LoadingSpinner'
import ErrorState from '@/components/shared/ErrorState'

// agent display config — keys match what shared_state.json writes
const AGENT_META: Record<string, { icon: string; name: string; interval: string }> = {
  economics:     { icon: '⚡', name: 'Economics',    interval: 'Every 2h' },
  weather:       { icon: '🌤️', name: 'Weather',      interval: 'Every 3m' },
  sports:        { icon: '🏈', name: 'Sports',       interval: 'Every 15m' },
  congressional: { icon: '🏛️', name: 'Intelligence', interval: 'Every 4h' },
  options:       { icon: '📈', name: 'Portfolio',    interval: 'Every 15m' },
  supervisor:    { icon: '🔧', name: 'System',       interval: 'Every 30m' },
  // legacy key aliases (old shared_state entries)
  donnie:        { icon: '⚡', name: 'Economics',    interval: 'Every 2h' },
  brad:          { icon: '🏈', name: 'Sports',       interval: 'Every 15m' },
  rugrat:        { icon: '🏛️', name: 'Intelligence', interval: 'Every 4h' },
  jordan:        { icon: '📈', name: 'Portfolio',    interval: 'Every 15m' },
}

// how long an agent can go without a run before turning stale/dead
const AGENT_MAX_AGE: Record<string, number> = {
  economics: 10800, donnie: 10800,
  weather: 600,
  sports: 2700, brad: 2700,
  congressional: 18000, rugrat: 18000,
  options: 2700, jordan: 2700,
  supervisor: 3600,
}

// primary display agents (deduped — don't show both old and new keys)
const PRIMARY_AGENTS = ['economics', 'weather', 'sports', 'congressional', 'options', 'supervisor']

const AGENT_COLORS: Record<string, string> = {
  economics: '#00ff88', donnie: '#00ff88',
  weather: '#38bdf8', 'weather-bot': '#38bdf8',
  sports: '#f59e0b', brad: '#f59e0b',
  congressional: '#a78bfa', rugrat: '#a78bfa',
  options: '#fb923c', jordan: '#fb923c',
  supervisor: '#888',
  FIRM: '#888',
}

const UPCOMING_EVENTS = [
  { date: '2026-04-30', label: 'Q1 GDP Advance + Core PCE', tag: '9 positions resolve' },
  { date: '2026-05-02', label: 'Non-Farm Payrolls (April)', tag: 'watching' },
  { date: '2026-05-13', label: 'CPI Release (April)', tag: 'watching' },
]

// activity feed agent name → display group
const AGENT_FILTER_MAP: Record<string, string> = {
  'economics': 'Economics', 'ECONOMICS': 'Economics', 'donnie': 'Economics', 'DONNIE': 'Economics', 'DONNIE V2': 'Economics', 'donnie-v3': 'Economics',
  'weather-bot': 'Weather', 'weather': 'Weather', 'WEATHER': 'Weather',
  'sports': 'Sports', 'SPORTS': 'Sports', 'brad': 'Sports', 'BRAD': 'Sports',
  'congressional': 'Intelligence', 'CONGRESSIONAL': 'Intelligence', 'rugrat': 'Intelligence', 'RUGRAT': 'Intelligence',
  'options': 'Portfolio', 'OPTIONS': 'Portfolio', 'jordan': 'Portfolio', 'JORDAN': 'Portfolio',
  'supervisor': 'System', 'SUPERVISOR': 'System', 'FIRM': 'System', 'firm': 'System',
}

const FILTER_PILLS = ['All', 'Economics', 'Weather', 'Sports', 'Intelligence', 'Portfolio', 'System'] as const

function timeAgo(ts: number): string {
  const diff = (Date.now() / 1000) - ts
  if (diff < 60) return 'just now'
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

function statusColor(ts: number, key?: string): string {
  const diff = (Date.now() / 1000) - ts
  const maxAge = key ? (AGENT_MAX_AGE[key] || 3600) : 1800
  if (diff < maxAge * 0.5) return '#00ff88'
  if (diff < maxAge * 1.5) return '#f59e0b'
  return '#666'
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

function getAgentTs(agent: Record<string, unknown> | undefined): number {
  if (!agent) return 0
  const ts = agent.last_run_ts
  if (typeof ts === 'number' && ts > 0) return ts
  const lr = agent.last_run
  if (typeof lr === 'string' && lr) return new Date(lr).getTime() / 1000
  return 0
}

// find best agent entry (prefer new name, fall back to legacy)
function resolveAgent(agents: Record<string, Record<string, unknown>>, primaryKey: string): Record<string, unknown> | undefined {
  if (agents[primaryKey]) return agents[primaryKey]
  // legacy key fallbacks
  const fallbacks: Record<string, string> = { economics: 'donnie', sports: 'brad', congressional: 'rugrat', options: 'jordan' }
  const legacy = fallbacks[primaryKey]
  return legacy ? agents[legacy] : undefined
}

export default function OverviewTab() {
  const [status, setStatus] = useState<StatusResponse | null>(null)
  const [activity, setActivity] = useState<ActivityEntry[]>([])
  const [positions, setPositions] = useState<PositionItem[]>([])
  const [weatherWR, setWeatherWR] = useState<number>(0)
  const [weatherOpen, setWeatherOpen] = useState<number>(0)
  const [sportsWR, setSportsWR] = useState<number>(0)
  const [sportsOpen, setSportsOpen] = useState<number>(0)
  const [econWins, setEconWins] = useState<number>(0)
  const [econResolved, setEconResolved] = useState<number>(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [activityFilter, setActivityFilter] = useState<string>('All')
  const [lastRefresh, setLastRefresh] = useState(Date.now())

  const load = useCallback(async () => {
    try {
      const [s, a, p, w, b, e] = await Promise.all([
        fetchStatus(),
        fetchActivity(80),
        fetchPositions(),
        fetchWeather().catch(() => null),
        fetchBrad().catch(() => null),
        fetchEval().catch(() => []),
      ])
      setStatus(s)
      setActivity(a)
      setPositions(Array.isArray(p) ? p : [])

      if (w) {
        setWeatherWR(w.win_rate || 0)
        // count open weather positions
        const wOpen = Array.isArray(w.recent) ? w.recent.filter((t: Record<string, unknown>) => t.status === 'OPEN').length : 0
        setWeatherOpen(wOpen)
      }

      if (b) {
        setSportsWR(b.win_rate || 0)
        const bOpen = Array.isArray(b.recent) ? b.recent.filter((t: Record<string, unknown>) => !['expired_win','expired_loss','expired_unfilled'].includes(t.status as string)).length : 0
        setSportsOpen(bOpen)
      }

      if (Array.isArray(e)) {
        const econEvals = (e as EvalRecord[]).filter(ev => ev.agent === 'donnie' || ev.agent === 'economics')
        const resolved = econEvals.filter(ev => ev.outcome === 'WIN' || ev.outcome === 'LOSS')
        setEconWins(resolved.filter(ev => ev.outcome === 'WIN').length)
        setEconResolved(resolved.length)
      }

      setError(false)
      setLastRefresh(Date.now())
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
  const agents = status.agents || {}

  const filteredActivity = activityFilter === 'All'
    ? activity
    : activity.filter(a => {
        const mapped = AGENT_FILTER_MAP[a.agent] || AGENT_FILTER_MAP[a.agent.toLowerCase()] || 'System'
        return mapped === activityFilter
      })

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
            <span className={`w-2 h-2 rounded-full ${status.service_running ? 'bg-[#00ff88] animate-pulse' : 'bg-[#ff4444]'}`} />
            {status.service_running ? 'Live' : 'Down'}
          </span>
          <span className="text-[#555] text-xs">{formatUptime(status.uptime_seconds)}</span>
        </div>
      </div>

      {/* Top stat row: Open positions + domain stats */}
      <div className="grid grid-cols-4 gap-4">
        {/* Open Kalshi positions */}
        <div className="border border-[#222] rounded-lg bg-[#111] p-4">
          <p className="text-[#888] text-xs mb-1">Open Kalshi Positions</p>
          <p className="text-3xl font-bold text-[#00ff88]">{positions.length}</p>
          <p className="text-[#555] text-xs mt-1">Economics engine</p>
        </div>

        {/* Economics performance */}
        <div className="border border-[#222] rounded-lg bg-[#111] p-4">
          <p className="text-[#888] text-xs mb-1">Economics</p>
          <p className="text-3xl font-bold text-[#00ff88]">
            {econResolved > 0 ? `${Math.round((econWins / econResolved) * 100)}%` : '—'}
          </p>
          <p className="text-[#555] text-xs mt-1">
            {econResolved > 0 ? `${econWins}/${econResolved} resolved` : 'No resolved trades yet'}
          </p>
        </div>

        {/* Weather performance */}
        <div className="border border-[#222] rounded-lg bg-[#38bdf8] bg-opacity-10 border-[#38bdf840] rounded-lg p-4">
          <p className="text-[#38bdf8] text-xs mb-1">Weather</p>
          <p className="text-3xl font-bold text-[#38bdf8]">{weatherWR.toFixed(1)}%</p>
          <p className="text-[#38bdf880] text-xs mt-1">
            {weatherOpen > 0 ? `${weatherOpen} open` : 'Live mode'} · post-tuning
          </p>
        </div>

        {/* Sports performance */}
        <div className="border border-[#f59e0b40] rounded-lg bg-[#f59e0b10] p-4">
          <p className="text-[#f59e0b] text-xs mb-1">Sports <span className="opacity-60">(paper)</span></p>
          <p className="text-3xl font-bold text-[#f59e0b]">{sportsWR.toFixed(1)}%</p>
          <p className="text-[#f59e0b80] text-xs mt-1">
            {sportsOpen > 0 ? `${sportsOpen} open` : 'stink bids'}
          </p>
        </div>
      </div>

      {/* Agent status grid */}
      <div>
        <h2 className="text-xs font-medium text-[#888] uppercase tracking-wide mb-3">Agent Status</h2>
        <div className="grid grid-cols-3 gap-3">
          {PRIMARY_AGENTS.map(key => {
            const meta = AGENT_META[key]
            if (!meta) return null
            const agent = resolveAgent(agents as Record<string, Record<string, unknown>>, key)
            const ts = getAgentTs(agent)
            const dotColor = ts ? statusColor(ts, key) : '#444'
            return (
              <div key={key} className="border border-[#1e1e1e] rounded-lg bg-[#111] px-4 py-3 flex items-center gap-3">
                <span className="text-xl">{meta.icon}</span>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-[#e5e5e5] text-sm font-medium">{meta.name}</span>
                    <span className="w-2 h-2 rounded-full shrink-0" style={{ background: dotColor }} />
                  </div>
                  <div className="text-[10px] text-[#555] mt-0.5">
                    {ts ? timeAgo(ts) : 'no data'} · {meta.interval}
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {/* Live activity + next event side by side */}
      <div className="grid grid-cols-3 gap-4">
        {/* Activity feed — takes 2 columns */}
        <div className="col-span-2 border border-[#222] rounded-lg bg-[#111] p-4">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-medium text-[#e5e5e5]">Live Activity</h2>
              <span className="flex items-center gap-1 px-2 py-0.5 rounded-full bg-[#00ff8815] text-[#00ff88] text-[10px]">
                <span className="w-1.5 h-1.5 rounded-full bg-[#00ff88] animate-pulse" />
                Live
              </span>
            </div>
            <span className="text-[#444] text-[10px]">
              {Math.round((Date.now() - lastRefresh) / 1000)}s ago
            </span>
          </div>

          {/* Filter pills */}
          <div className="flex gap-1 mb-3 flex-wrap">
            {FILTER_PILLS.map(pill => (
              <button
                key={pill}
                onClick={() => setActivityFilter(pill)}
                className={`px-2.5 py-0.5 rounded-full text-[10px] font-medium transition-colors ${
                  activityFilter === pill
                    ? 'bg-[#00ff8825] text-[#00ff88] border border-[#00ff8840]'
                    : 'bg-[#1a1a1a] text-[#555] border border-[#2a2a2a] hover:text-[#888]'
                }`}
              >{pill}</button>
            ))}
          </div>

          <div className="space-y-0.5 max-h-[320px] overflow-y-auto font-mono text-[11px]">
            {filteredActivity.length > 0 ? filteredActivity.map((a, i) => (
              <div key={i} className="flex items-start gap-2 py-1 border-b border-[#161616]">
                <span className="w-1.5 h-1.5 rounded-full mt-1.5 shrink-0"
                  style={{ background: AGENT_COLORS[a.agent] || AGENT_COLORS[a.agent.toLowerCase()] || '#666' }} />
                <span className="text-[#444] shrink-0 text-[10px]">{a.timestamp.slice(11, 19)}</span>
                <span className="text-[#666] shrink-0 w-[70px] truncate uppercase text-[10px]">{a.agent}</span>
                <span className="text-[#aaa] truncate">{a.message}</span>
              </div>
            )) : (
              <p className="text-[#555] text-center py-4">
                {activityFilter !== 'All' ? `No recent ${activityFilter} activity` : 'No recent activity'}
              </p>
            )}
          </div>
        </div>

        {/* Next event + upcoming — 1 column */}
        <div className="space-y-3">
          <h2 className="text-xs font-medium text-[#888] uppercase tracking-wide">Upcoming Catalysts</h2>
          {UPCOMING_EVENTS.map(evt => {
            const days = daysUntil(evt.date)
            const isNext = evt === nextEvent
            const isToday = days === 0
            return (
              <div key={evt.date} className={`rounded-lg p-3 border ${
                isNext ? 'border-[#00ff8840] bg-[#00ff8808]' : 'border-[#1e1e1e] bg-[#111]'
              }`}>
                <div className="flex items-center justify-between mb-1">
                  <span className={`text-xs font-medium ${isNext ? 'text-[#00ff88]' : 'text-[#666]'}`}>
                    {new Date(evt.date + 'T12:00:00Z').toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
                  </span>
                  <span className={`text-xs font-bold px-1.5 py-0.5 rounded ${
                    isToday ? 'bg-[#ff444420] text-[#ff4444]' :
                    isNext ? 'bg-[#00ff8820] text-[#00ff88]' : 'text-[#444]'
                  }`}>
                    {isToday ? 'TODAY' : `${days}d`}
                  </span>
                </div>
                <p className={`text-xs ${isNext ? 'text-[#e5e5e5]' : 'text-[#888]'}`}>{evt.label}</p>
                <p className="text-[10px] text-[#555] mt-0.5">{evt.tag}</p>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
