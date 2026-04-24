"use client";

import type { WeatherResponse } from "@/lib/api";

interface Props {
  data: WeatherResponse | null;
}

export default function WeatherPerformance({ data }: Props) {
  if (!data) return null;

  // Top 5 cities by total trades
  const cities = Object.entries(data.by_city)
    .sort((a, b) => b[1].total - a[1].total)
    .slice(0, 5);

  return (
    <div className="rounded-lg border border-gray-800 p-4" style={{ background: "#111111" }}>
      <h3 className="text-sm font-semibold uppercase tracking-wider text-gray-400 mb-3">
        Weather Performance
      </h3>

      <div className="text-center mb-4">
        <div className="text-4xl font-bold" style={{ color: "#3b82f6" }}>
          {data.win_rate}%
        </div>
        <div className="text-xs text-gray-500 mt-1">Win Rate</div>
        {/* Progress bar */}
        <div className="w-full h-2 bg-gray-800 rounded-full mt-2 overflow-hidden">
          <div
            className="h-full rounded-full transition-all"
            style={{
              width: `${data.win_rate}%`,
              background: "linear-gradient(90deg, #3b82f6, #60a5fa)",
            }}
          />
        </div>
      </div>

      <div className="grid grid-cols-3 gap-2 text-center mb-4">
        <div>
          <div className="text-lg font-semibold text-gray-300">{data.total}</div>
          <div className="text-[10px] text-gray-600">Total</div>
        </div>
        <div>
          <div className="text-lg font-semibold text-gray-300">{data.resolved}</div>
          <div className="text-[10px] text-gray-600">Resolved</div>
        </div>
        <div>
          <div className="text-lg font-semibold text-green-400">{data.wins}</div>
          <div className="text-[10px] text-gray-600">Wins</div>
        </div>
      </div>

      <div className="text-xs text-gray-500 mb-1 font-medium">Top Cities</div>
      <div className="space-y-1">
        {cities.map(([city, stats]) => (
          <div key={city} className="flex items-center justify-between text-[10px]">
            <span className="text-gray-400 truncate max-w-[120px]">{city}</span>
            <div className="flex items-center gap-2">
              <span className="text-gray-600">{stats.total} trades</span>
              <span
                className="font-medium"
                style={{ color: stats.win_rate > 40 ? "#00ff88" : stats.win_rate > 30 ? "#eab308" : "#ff4444" }}
              >
                {stats.win_rate}%
              </span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
