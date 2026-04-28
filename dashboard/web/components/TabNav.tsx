'use client'

const ICONS: Record<string, string> = {
  Overview: '📊',
  Economics: '⚡',
  Weather: '🌤️',
  Sports: '🏈',
  Intelligence: '🏛️',
  Portfolio: '📈',
  System: '🔧',
}

export default function TabNav({ tabs, active, onChange }: { tabs: string[]; active: string; onChange: (t: string) => void }) {
  return (
    <nav className="border-b border-[#222] bg-[#0a0a0a] sticky top-0 z-50">
      <div className="max-w-[1600px] mx-auto px-6 flex gap-1 overflow-x-auto">
        {tabs.map(t => (
          <button
            key={t}
            onClick={() => onChange(t)}
            className={`px-4 py-3 text-sm font-medium whitespace-nowrap border-b-2 transition-colors ${
              active === t
                ? 'text-[#00ff88] border-[#00ff88]'
                : 'text-[#888] border-transparent hover:text-[#e5e5e5]'
            }`}
          >
            {ICONS[t] || ''} {t}
          </button>
        ))}
      </div>
    </nav>
  )
}
