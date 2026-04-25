'use client'

import { useEffect, useState, useCallback } from 'react'
import { fetchStatus, fetchActivity, fetchPositions, StatusResponse, ActivityEntry } from '@/lib/api'
import StatCard from '@/components/shared/StatCard'
import LoadingSpinner from '@/components/shared/LoadingSpinner'
import ErrorState from '@/components/shared/ErrorState'

const AGENT_META: Record<string, { icon: string; name: string; interval: string }> = {
  donnie: { icon: '⚡', name: 'Economics', interval: 'Every 3h' },
  weather: { icon: '🌤️', name: 'Weather', interval: 'Every 5m' },
  brad: { icon: '🏈', name: 'Sports', interval: 'Every 30m' },
  rugrat: { icon: '🏛️', name: 'Intelligence', interval: 'Every 4h' },
  jordan: { icon: '📈', name: 'Portfolio', interval: 'Every 1m' },
  supervisor: { icon: '🔧', name: 'System', interval: 'Every 5m' },
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

function timeAgo(ts: number): string {
  const diff = (Date.now() / 1000) - ts
  if (diff < 60) return 'just now'
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

function statusColor(ts: number): string {
  const diff = (Date.now() / 1000) - ts
  if (diff < 1800) return '#00ff88'
  if (diff < 14400) return '#f59e0b'
  return '#666'
}

function formatUptime(s: number): string {
  const d = Math.floor(s / 86400)
  const h = Math.floor((s % 86400) / 3600)
  const m = Math.floor((s % 3600) / 60)
  return `${d}d ${h}h ${m}m`
}

export default function OverviewTab() {
  const [status, setStatus] = useState<StatusResponse | null>(null)
  const [activity, setActivity] = useState<ActivityEntry[]>([])
  const [posCount, setPosCount] = useState<number>(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)

  const load = useCallback(async () => {
    try {
      const [s, a, p] = await Promise.all([fetchStatus(), fetchActivity(20), fetchPositions()])
      setStatus(s)
      setActivity(a)
      setPosCount(Array.isArray(p) ? p.length : 0)
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
        <StatCard label="Weather Win Rate" value={`${(status.weather_win_rate * 100).toFixed(1)}%`} color="#38bdf8" />
        <StatCard label="RAG Disclosures Indexed" value={status.rag_stats?.disclosures ?? 0} color="#a78bfa" />
        <StatCard label="Eval Trades Scored" value={status.eval_trades} color="#f59e0b" />
      </div>

      {/* Agent grid */}
      <div>
        <h2 className="text-sm font-medium text-[#888] mb-3">AGENT STATUS</h2>
        <div className="grid grid-cols-3 gap-3">
          {Object.entries(AGENT_META).map(([key, meta]) => {
            const agent = status.agents?.[key]
            const ts = agent?.last_run_ts || 0
            return (
              <div key={key} className="border border-[#222] rounded-lg bg-[#111] p-4 flex items-center gap-3">
                <span className="text-2xl">{meta.icon}</span>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-[#e5e5e5] font-medium text-sm">{meta.name}</span>
                    <span className="w-2 h-2 rounded-full" style={{ background: ts ? statusColor(ts) : '#666' }} />
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
    </div>
  )
}
