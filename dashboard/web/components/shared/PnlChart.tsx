'use client'

export default function PnlChart({ data }: { data: { date: string; value: number }[] }) {
  if (!data.length) return <div className="text-[#666] text-xs text-center py-4">No data points</div>

  const w = 400, h = 100, pad = 4
  const vals = data.map(d => d.value)
  const min = Math.min(...vals, 0)
  const max = Math.max(...vals, 1)
  const range = max - min || 1

  const points = data.map((d, i) => {
    const x = pad + (i / Math.max(data.length - 1, 1)) * (w - pad * 2)
    const y = h - pad - ((d.value - min) / range) * (h - pad * 2)
    return { x, y }
  })

  const line = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x},${p.y}`).join(' ')
  const area = `${line} L${points[points.length - 1].x},${h - pad} L${points[0].x},${h - pad} Z`

  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full" preserveAspectRatio="none">
      <defs>
        <linearGradient id="pnlGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#00ff88" stopOpacity="0.3" />
          <stop offset="100%" stopColor="#00ff88" stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill="url(#pnlGrad)" />
      <path d={line} fill="none" stroke="#00ff88" strokeWidth="2" />
    </svg>
  )
}
