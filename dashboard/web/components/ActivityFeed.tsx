"use client";

import { useState } from "react";
import type { ActivityEntry } from "@/lib/api";

const AGENTS = ["ALL", "DONNIE", "WEATHER", "BRAD", "RUGRAT", "JORDAN", "SUPERVISOR"];

const AGENT_COLORS: Record<string, string> = {
  donnie: "#00ff88",
  "weather-bot": "#3b82f6",
  weather: "#3b82f6",
  brad: "#f97316",
  "brad-bot": "#f97316",
  rugrat: "#a855f7",
  "rugrat-bot": "#a855f7",
  jordan: "#eab308",
  "jordan-bot": "#eab308",
  supervisor: "#6b7280",
};

interface Props {
  entries: ActivityEntry[];
  lastUpdated: number | null;
  onRefresh: () => void;
  loading: boolean;
}

export default function ActivityFeed({ entries, lastUpdated, onRefresh, loading }: Props) {
  const [filter, setFilter] = useState("ALL");

  const filtered =
    filter === "ALL"
      ? entries
      : entries.filter((e) => e.agent.toLowerCase().startsWith(filter.toLowerCase()));

  const ago = lastUpdated ? Math.floor((Date.now() - lastUpdated) / 1000) : null;

  return (
    <div className="rounded-lg border border-gray-800 p-4" style={{ background: "#111111" }}>
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-gray-400">
          Activity Feed
        </h2>
        <div className="flex items-center gap-2">
          {ago !== null && (
            <span className="text-xs text-gray-600">{ago}s ago</span>
          )}
          <button
            onClick={onRefresh}
            disabled={loading}
            className="text-xs px-2 py-1 rounded bg-gray-800 hover:bg-gray-700 text-gray-400 disabled:opacity-50"
          >
            {loading ? "..." : "↻"}
          </button>
        </div>
      </div>

      {/* Filter pills */}
      <div className="flex gap-1.5 mb-3 flex-wrap">
        {AGENTS.map((a) => (
          <button
            key={a}
            onClick={() => setFilter(a)}
            className={`text-[10px] px-2 py-0.5 rounded-full border transition-all ${
              filter === a
                ? "border-green-500/50 text-white bg-green-500/10"
                : "border-gray-700 text-gray-500 hover:text-gray-300"
            }`}
          >
            {a}
          </button>
        ))}
      </div>

      {/* Entries */}
      <div className="max-h-[500px] overflow-y-auto space-y-1 pr-1">
        {filtered.length === 0 && (
          <p className="text-gray-600 text-xs italic">No activity</p>
        )}
        {filtered.map((e, i) => {
          const color = AGENT_COLORS[e.agent.toLowerCase()] || "#6b7280";
          const isWarn = e.level === "WARNING";
          const isErr = e.level === "ERROR";
          return (
            <div
              key={i}
              className={`flex items-start gap-2 px-2 py-1.5 rounded text-xs ${
                isErr
                  ? "bg-red-500/5 border-l-2 border-red-500/40"
                  : isWarn
                  ? "bg-yellow-500/5 border-l-2 border-yellow-500/30"
                  : ""
              }`}
            >
              <div
                className="w-1.5 h-1.5 rounded-full mt-1.5 flex-shrink-0"
                style={{ background: color }}
              />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="font-medium" style={{ color }}>
                    {e.agent}
                  </span>
                  <span className="text-gray-600 text-[10px]">
                    {e.timestamp.split(",")[0].split(" ")[1]}
                  </span>
                </div>
                <p className="text-gray-400 break-words leading-snug">{e.message}</p>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
