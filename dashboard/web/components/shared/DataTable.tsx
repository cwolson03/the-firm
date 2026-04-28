'use client'

import { useState } from 'react'

interface Column {
  key: string
  label: string
  render?: (value: any, row: any) => React.ReactNode
  sortable?: boolean
}

export default function DataTable({
  columns,
  data,
  rowClassName,
  emptyMessage,
  onRowClick,
}: {
  columns: Column[]
  data: any[]
  rowClassName?: (row: any) => string
  emptyMessage?: string
  onRowClick?: (row: any) => void
}) {
  const [sortKey, setSortKey] = useState<string | null>(null)
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')

  const sorted = sortKey
    ? [...data].sort((a, b) => {
        const va = a[sortKey], vb = b[sortKey]
        if (va == null) return 1
        if (vb == null) return -1
        const cmp = typeof va === 'number' ? va - vb : String(va).localeCompare(String(vb))
        return sortDir === 'asc' ? cmp : -cmp
      })
    : data

  const handleSort = (key: string) => {
    if (sortKey === key) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setSortKey(key); setSortDir('desc') }
  }

  if (!data.length) return <p className="text-[#666] text-sm py-4 text-center">{emptyMessage || 'No data'}</p>

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-[#222]">
            {columns.map(c => (
              <th
                key={c.key}
                className={`text-left py-2 px-3 text-[#888] font-medium text-xs uppercase tracking-wider ${c.sortable !== false ? 'cursor-pointer hover:text-[#e5e5e5]' : ''}`}
                onClick={() => c.sortable !== false && handleSort(c.key)}
              >
                {c.label}
                {sortKey === c.key && (sortDir === 'asc' ? ' ↑' : ' ↓')}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((row, i) => (
            <tr
              key={i}
              className={`border-b border-[#1a1a1a] hover:bg-[#1a1a1a] transition-colors ${rowClassName?.(row) || ''} ${onRowClick ? 'cursor-pointer' : ''}`}
              onClick={() => onRowClick?.(row)}
            >
              {columns.map(c => (
                <td key={c.key} className="py-2 px-3 text-[#e5e5e5]">
                  {c.render ? c.render(row[c.key], row) : (row[c.key] ?? '—')}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
