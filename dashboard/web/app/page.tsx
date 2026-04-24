"use client";

import { useEffect, useState, useCallback } from "react";
import {
  fetchStatus,
  fetchActivity,
  fetchWeather,
  fetchEval,
  fetchPositions,
  type StatusResponse,
  type ActivityEntry,
  type WeatherResponse,
  type EvalRecord,
} from "@/lib/api";
import AgentCard from "@/components/AgentCard";
import ActivityFeed from "@/components/ActivityFeed";
import RagDemo from "@/components/RagDemo";
import Positions from "@/components/Positions";
import WeatherPerformance from "@/components/WeatherPerformance";
import EvalFramework from "@/components/EvalFramework";
import Link from "next/link";

// Agent intervals in minutes (approximate)
const AGENT_INTERVALS: Record<string, number> = {
  donnie: 30,
  weather: 15,
  brad: 30,
  rugrat: 60,
  jordan: 15,
  supervisor: 120,
  test: 0,
};

const DISPLAY_AGENTS = ["donnie", "weather", "brad", "rugrat", "jordan", "supervisor"];

export default function Dashboard() {
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [activity, setActivity] = useState<ActivityEntry[]>([]);
  const [weather, setWeather] = useState<WeatherResponse | null>(null);
  const [evalData, setEvalData] = useState<EvalRecord[] | null>(null);
  const [positions, setPositions] = useState<Record<string, unknown>[] | null>(null);
  const [lastUpdated, setLastUpdated] = useState<number | null>(null);
  const [loadingActivity, setLoadingActivity] = useState(false);

  const loadAll = useCallback(async () => {
    try {
      const [s, w, e, p] = await Promise.all([
        fetchStatus().catch(() => null),
        fetchWeather().catch(() => null),
        fetchEval().catch(() => null),
        fetchPositions().catch(() => null),
      ]);
      if (s) setStatus(s);
      if (w) setWeather(w);
      if (e) setEvalData(e);
      if (p) setPositions(p as Record<string, unknown>[]);
    } catch {}
  }, []);

  const loadActivity = useCallback(async () => {
    setLoadingActivity(true);
    try {
      const a = await fetchActivity(150);
      setActivity(a);
      setLastUpdated(Date.now());
    } catch {}
    setLoadingActivity(false);
  }, []);

  useEffect(() => {
    loadAll();
    loadActivity();
    const i1 = setInterval(loadAll, 30000);
    const i2 = setInterval(loadActivity, 15000);
    return () => {
      clearInterval(i1);
      clearInterval(i2);
    };
  }, [loadAll, loadActivity]);

  const formatUptime = (s: number) => {
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    return h > 0 ? `${h}h ${m}m` : `${m}m`;
  };

  return (
    <div className="min-h-screen">
      {/* Header */}
      <div className="border-b border-gray-800 px-6 py-4 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold" style={{ color: "#00ff88" }}>
            THE FIRM
          </h1>
          <p className="text-gray-500 text-sm">Autonomous Trading Intelligence System</p>
        </div>
        <div className="flex items-center gap-4">
          <Link
            href="/portfolio"
            className="text-xs text-gray-500 hover:text-gray-300 border border-gray-700 px-3 py-1.5 rounded transition-colors"
          >
            Portfolio →
          </Link>
          {status && (
            <div className="flex items-center gap-2">
              <div
                className={`w-2 h-2 rounded-full ${
                  status.service_running ? "pulse-green" : ""
                }`}
                style={{
                  background: status.service_running ? "#00ff88" : "#ff4444",
                }}
              />
              <span className="text-xs text-gray-400">
                {status.service_running ? "Running" : "Stopped"}
              </span>
              <span className="text-xs text-gray-600">
                API up {formatUptime(status.uptime_seconds)}
              </span>
            </div>
          )}
        </div>
      </div>

      <div className="p-6 space-y-6">
        {/* Agent Status Grid */}
        <div className="grid grid-cols-6 gap-3">
          {DISPLAY_AGENTS.map((name) => {
            const agent = status?.agents?.[name] || status?.agents?.[`${name}-bot`];
            return (
              <AgentCard
                key={name}
                name={name}
                status={agent ? "ok" : "unknown"}
                lastRun={agent?.last_run || ""}
                intervalMin={AGENT_INTERVALS[name] || 30}
                extra={agent as Record<string, string | number | boolean | null | undefined> | undefined}
              />
            );
          })}
        </div>

        {/* System Stats Bar */}
        {status && (
          <div className="grid grid-cols-4 gap-3">
            <div className="rounded-lg border border-gray-800 p-3 text-center" style={{ background: "#111" }}>
              <div className="text-lg font-bold text-green-400">{status.rag_stats?.disclosures || 0}</div>
              <div className="text-[10px] text-gray-600">RAG Disclosures</div>
            </div>
            <div className="rounded-lg border border-gray-800 p-3 text-center" style={{ background: "#111" }}>
              <div className="text-lg font-bold text-blue-400">{status.rag_stats?.profiles || 0}</div>
              <div className="text-[10px] text-gray-600">Member Profiles</div>
            </div>
            <div className="rounded-lg border border-gray-800 p-3 text-center" style={{ background: "#111" }}>
              <div className="text-lg font-bold text-purple-400">{status.eval_trades}</div>
              <div className="text-[10px] text-gray-600">Eval Records</div>
            </div>
            <div className="rounded-lg border border-gray-800 p-3 text-center" style={{ background: "#111" }}>
              <div className="text-lg font-bold text-yellow-400">{status.kalshi_positions}</div>
              <div className="text-[10px] text-gray-600">Open Positions</div>
            </div>
          </div>
        )}

        {/* Two-column main content */}
        <div className="grid grid-cols-5 gap-6">
          {/* Left 60% */}
          <div className="col-span-3">
            <ActivityFeed
              entries={activity}
              lastUpdated={lastUpdated}
              onRefresh={loadActivity}
              loading={loadingActivity}
            />
          </div>
          {/* Right 40% */}
          <div className="col-span-2">
            <RagDemo />
          </div>
        </div>

        {/* Bottom Grid */}
        <div className="grid grid-cols-3 gap-6">
          <Positions data={positions} />
          <WeatherPerformance data={weather} />
          <EvalFramework data={evalData} />
        </div>
      </div>

      {/* Footer */}
      <div className="border-t border-gray-800 px-6 py-3 text-center">
        <p className="text-[10px] text-gray-700">
          The Firm · Multi-Agent Trading Intelligence · Atlas (Raspberry Pi 5) · Built by Cody Olson
        </p>
      </div>
    </div>
  );
}
