'use client'

export default function LoadingSpinner({ size = 'md' }: { size?: 'sm' | 'md' | 'lg' }) {
  const px = size === 'sm' ? 'h-4 w-4' : size === 'lg' ? 'h-8 w-8' : 'h-6 w-6'
  return (
    <div className={`${px} animate-spin rounded-full border-2 border-[#333] border-t-[#00ff88]`} />
  )
}
