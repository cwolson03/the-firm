'use client'

import { useEffect, useState, useCallback } from 'react'
import { fetchPositions, fetchEval, fetchActivity, EvalRecord, ActivityEntry } from '@/lib/api'
import DataTable from '@/components/shared/DataTable'
import PnlChart from '@/components/shared/PnlChart'
import LoadingSpinner from '@/components/shared/LoadingSpinner'
import ErrorState from '@/components/shared/ErrorState'

export default function EconomicsTab() {
  const [positions, setPositions] = useState<any[]>([])
  const [evals, setEvals] = useState<EvalRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)

  const load = useCallback(async () => {
    try {
      const [p, e] = await Promise.all([fetchPositions(), fetchEval()])
      setPositions(Array.isArray(p) ? p : [])
      setEvals(Array.isArray(e) ? e.filter(r => r.agent === 'donnie' || r.agent === 'kalshi') : [])
      setError(false)
    } catch { setError(true) }
    setLoading(false)
  }, [])

  useEffect(() => { load() }, [load])

  if (loading) return <div className="flex justify-center py-20"><LoadingSpinner size="lg" /></div>
  if (error) return <ErrorState onRetry={load} />

  const pnlData = evals.map((e, i) => ({
    date: e.trade_id,
    value: evals.slice(0, i + 1).reduce((s, r) => s + (r.pnl_pct || 0), 0),
  }))

  return (
    <div className="flex gap-6">
      {/* Left 60% */}
      <div className="flex-[3] space-y-6 min-w-0">
        {/* Open positions */}
        <div className="border border-[#222] rounded-lg bg-[#111] p-4">
          <div className="flex items-center gap-2 mb-3">
            <h2 className="text-sm font-medium text-[#e5e5e5]">Open Positions</h2>
            <span className="px-2 py-0.5 rounded-full bg-[#00ff8820] text-[#00ff88] text-[10px] font-medium">{positions.length}</span>
          </div>
          {positions.length ? (
            <DataTable
              columns={[
                { key: 'ticker', label: 'Ticker' },
                { key: 'side', label: 'Side', render: (v: string) => (
                  <span className={`px-2 py-0.5 rounded text-[10px] font-medium ${v === 'YES' || v === 'yes' ? 'bg-[#00ff8820] text-[#00ff88]' : 'bg-[#ff444420] text-[#ff4444]'}`}>{v?.toUpperCase()}</span>
                )},
                { key: 'thesis', label: 'Thesis' },
                { key: 'status', label: 'Status' },
              ]}
              data={positions}
            />
          ) : (
            <p className="text-[#666] text-sm py-8 text-center">No open positions — Donnie is watching 5,200+ markets</p>
          )}
        </div>

        {/* Trade history */}
        <div className="border border-[#222] rounded-lg bg-[#111] p-4">
          <h2 className="text-sm font-medium text-[#e5e5e5] mb-3">Trade History</h2>
          <DataTable
            columns={[
              { key: 'trade_id', label: 'Date' },
              { key: 'market', label: 'Market' },
              { key: 'direction', label: 'Direction' },
              { key: 'pnl_pct', label: 'Edge', render: (v: number) => v != null ? `${v > 0 ? '+' : ''}${v.toFixed(1)}%` : '—' },
              { key: 'outcome', label: 'Outcome', render: (v: string) => (
                <span className={v === 'WIN' ? 'text-[#00ff88]' : v === 'LOSS' ? 'text-[#ff4444]' : 'text-[#888]'}>{v}</span>
              )},
              { key: 'process_score', label: 'Process', render: (_: any, row: EvalRecord) => {
                const s = row.llm_eval?.process_score
                if (s == null) return '—'
                const c = s >= 8 ? '#00ff88' : s >= 5 ? '#f59e0b' : '#ff4444'
                return <span style={{ color: c }}>{s}/10</span>
              }},
            ]}
            data={evals}
            rowClassName={(row: EvalRecord) => row.outcome === 'WIN' ? 'border-l-2 border-l-[#00ff88]' : row.outcome === 'LOSS' ? 'border-l-2 border-l-[#ff4444]' : ''}
            emptyMessage="No resolved trades yet — eval framework active"
          />
          {pnlData.length > 1 && (
            <div className="mt-4">
              <PnlChart data={pnlData} />
            </div>
          )}
        </div>

        {/* LLM Reasoning */}
        <div className="border border-[#222] rounded-lg bg-[#111] p-4">
          <h2 className="text-sm font-medium text-[#e5e5e5] mb-3">LLM Reasoning</h2>
          {evals.length > 0 && evals[evals.length - 1].llm_eval ? (
            <div>
              <div className="flex gap-2 mb-2">
                <span className="px-2 py-0.5 rounded bg-[#1a1a1a] text-[#888] text-[10px]">Grok</span>
              </div>
              <pre className="text-xs text-[#aaa] bg-[#0a0a0a] rounded p-3 overflow-x-auto whitespace-pre-wrap">
                {evals[evals.length - 1].llm_eval?.what_worked || 'No reasoning data available'}
              </pre>
            </div>
          ) : (
            <p className="text-[#666] text-sm">Reasoning will appear after first LLM-evaluated trade</p>
          )}
        </div>
      </div>

      {/* Right 40% */}
      <div className="flex-[2] space-y-6 min-w-0">
        {/* Account overview */}
        <div className="border border-[#222] rounded-lg bg-[#111] p-4">
          <h2 className="text-sm font-medium text-[#e5e5e5] mb-3">Account Overview</h2>
          <div className="space-y-3">
            <div className="flex justify-between text-sm">
              <span className="text-[#888]">Positions</span>
              <span className="text-[#e5e5e5]">{positions.length}</span>
            </div>
            <div className="w-full bg-[#222] rounded-full h-2">
              <div className="bg-[#00ff88] h-2 rounded-full" style={{ width: positions.length ? '30%' : '5%' }} />
            </div>
            <div className="flex justify-between text-xs text-[#666]">
              <span>Deployed</span>
              <span>Available</span>
            </div>
          </div>
        </div>

        {/* Macro context */}
        <div className="border border-[#222] rounded-lg bg-[#111] p-4">
          <h2 className="text-sm font-medium text-[#e5e5e5] mb-3">Macro Context</h2>
          <p className="text-[10px] text-[#666] mb-3">Key inputs for Donnie&apos;s ECONOMIC_DATA models</p>
          <div className="space-y-2">
            {[
              { label: 'GDPNow (Atlanta Fed)', value: '1.24%' },
              { label: 'Core PCE YoY', value: '3.0%' },
            ].map(m => (
              <div key={m.label} className="flex justify-between text-sm">
                <span className="text-[#888]">{m.label}</span>
                <span className="text-[#e5e5e5] font-medium">{m.value}</span>
              </div>
            ))}
            <div className="mt-3 pt-3 border-t border-[#222]">
              <p className="text-[#888] text-xs font-medium mb-2">Upcoming Events</p>
              <div className="space-y-1 text-xs text-[#666]">
                <p>Apr 30 — Q1 GDP + PCE</p>
                <p>May 2 — Non-Farm Payrolls</p>
                <p>May 13 — CPI Release</p>
              </div>
            </div>
          </div>
        </div>

        {/* Watchlist */}
        <div className="border border-[#222] rounded-lg bg-[#111] p-4">
          <h2 className="text-sm font-medium text-[#e5e5e5] mb-3">Watchlist</h2>
          <div className="space-y-2">
            {[
              { ticker: 'KXGDP', event: 'GDP — Apr 30' },
              { ticker: 'KXPCE', event: 'PCE — Apr 30' },
              { ticker: 'KXNFP', event: 'NFP — May 2' },
              { ticker: 'KXCPI', event: 'CPI — May 13' },
            ].map(w => (
              <div key={w.ticker} className="flex justify-between text-sm">
                <span className="text-[#00ff88] font-mono">{w.ticker}</span>
                <span className="text-[#666]">{w.event}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
