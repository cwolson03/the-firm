'use client'

export default function StatCard({ label, value, sub, color }: { label: string; value: string | number; sub?: string; color?: string }) {
  return (
    <div className="border border-[#222] rounded-lg bg-[#111] p-4 flex flex-col gap-1">
      <span className="text-[#888] text-xs uppercase tracking-wider">{label}</span>
      <span className="text-2xl font-bold" style={{ color: color || '#e5e5e5' }}>{value}</span>
      {sub && <span className="text-[#666] text-xs">{sub}</span>}
    </div>
  )
}
