"use client";

interface AgentCardProps {
  name: string;
  status: string;
  lastRun: string;
  intervalMin: number;
  extra?: Record<string, string | number | boolean | null | undefined>;
}

const AGENT_COLORS: Record<string, string> = {
  donnie: "#00ff88",
  weather: "#3b82f6",
  brad: "#f97316",
  rugrat: "#a855f7",
  jordan: "#eab308",
  supervisor: "#6b7280",
  test: "#6b7280",
};

function timeAgo(iso: string): string {
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

export default function AgentCard({ name, status, lastRun, intervalMin, extra }: AgentCardProps) {
  const color = AGENT_COLORS[name.toLowerCase()] || "#6b7280";
  const isRecent = (Date.now() - new Date(lastRun).getTime()) / 1000 < intervalMin * 60 * 2;
  const dotColor = status === "error" ? "#ff4444" : isRecent ? "#00ff88" : "#eab308";

  return (
    <div className="rounded-lg p-4 border border-gray-800" style={{ background: "#111111" }}>
      <div className="flex items-center gap-2 mb-2">
        <div
          className="w-2.5 h-2.5 rounded-full flex-shrink-0"
          style={{ background: dotColor, boxShadow: `0 0 6px ${dotColor}` }}
        />
        <span className="font-semibold text-sm uppercase tracking-wide" style={{ color }}>
          {name}
        </span>
      </div>
      <div className="text-xs text-gray-500">
        <div>{timeAgo(lastRun)}</div>
        {intervalMin > 0 && <div className="text-gray-600">every {intervalMin}m</div>}
        {extra?.mode && <div className="text-gray-600 mt-1">{String(extra.mode)}</div>}
      </div>
    </div>
  );
}
