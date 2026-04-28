'use client'

export default function ErrorState({ message, onRetry }: { message?: string; onRetry?: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center py-8 text-center">
      <div className="text-[#ff4444] text-lg mb-2">⚠️</div>
      <p className="text-[#888] text-sm">{message || 'Data unavailable — API offline'}</p>
      {onRetry && (
        <button
          onClick={onRetry}
          className="mt-3 px-4 py-1.5 text-xs rounded bg-[#1a1a1a] border border-[#333] text-[#e5e5e5] hover:border-[#00ff88] transition-colors"
        >
          Retry
        </button>
      )}
    </div>
  )
}
