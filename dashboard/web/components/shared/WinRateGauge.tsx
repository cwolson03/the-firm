'use client'

export default function WinRateGauge({ rate, label, size = 120 }: { rate: number; label?: string; size?: number }) {
  const r = size / 2 - 8
  const circ = 2 * Math.PI * r
  const pct = Math.min(Math.max(rate, 0), 100)
  const offset = circ - (pct / 100) * circ
  const color = pct >= 50 ? '#00ff88' : pct >= 35 ? '#f59e0b' : '#ff4444'

  return (
    <div className="flex flex-col items-center">
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="#222" strokeWidth="6" />
        <circle
          cx={size / 2} cy={size / 2} r={r} fill="none"
          stroke={color} strokeWidth="6" strokeLinecap="round"
          strokeDasharray={circ} strokeDashoffset={offset}
        />
      </svg>
      <span className="text-2xl font-bold -mt-[70px] mb-[40px]" style={{ color }}>{pct.toFixed(1)}%</span>
      {label && <span className="text-[#888] text-xs mt-1">{label}</span>}
    </div>
  )
}
