"use client";

import type { EvalRecord } from "@/lib/api";

interface Props {
  data: EvalRecord[] | null;
}

export default function EvalFramework({ data }: Props) {
  const records = Array.isArray(data) ? data : [];

  return (
    <div className="rounded-lg border border-gray-800 p-4" style={{ background: "#111111" }}>
      <h3 className="text-sm font-semibold uppercase tracking-wider text-gray-400 mb-3">
        Eval Framework
      </h3>

      {records.length === 0 ? (
        <p className="text-xs text-gray-600 italic">No evaluations yet</p>
      ) : (
        <div className="space-y-3">
          {records.map((r, i) => (
            <div key={i} className="bg-gray-900 rounded p-3 space-y-2">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-medium text-gray-300">{r.trade_id}</span>
                  {r.trade_id?.startsWith("TEST") && (
                    <span className="text-[9px] px-1.5 py-0.5 rounded bg-yellow-500/15 text-yellow-500 font-medium">
                      Test
                    </span>
                  )}
                </div>
                <span
                  className="text-[10px] font-semibold px-1.5 py-0.5 rounded"
                  style={{
                    background: r.outcome === "WIN" ? "rgba(0,255,136,0.15)" : "rgba(255,68,68,0.15)",
                    color: r.outcome === "WIN" ? "#00ff88" : "#ff4444",
                  }}
                >
                  {r.outcome} {r.pnl_pct > 0 ? "+" : ""}{r.pnl_pct}%
                </span>
              </div>

              <div className="text-[10px] text-gray-500">
                {r.agent} · {r.market} · {r.direction}
              </div>

              {r.llm_eval && (
                <>
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] text-gray-500">Process Score</span>
                    <div className="flex-1 h-1.5 bg-gray-800 rounded-full overflow-hidden">
                      <div
                        className="h-full rounded-full"
                        style={{
                          width: `${(r.llm_eval.process_score / 10) * 100}%`,
                          background:
                            r.llm_eval.process_score >= 7
                              ? "#00ff88"
                              : r.llm_eval.process_score >= 4
                              ? "#eab308"
                              : "#ff4444",
                        }}
                      />
                    </div>
                    <span className="text-[10px] text-gray-400 font-mono">
                      {r.llm_eval.process_score}/10
                    </span>
                  </div>
                  <div className="text-[10px] text-gray-500 leading-relaxed">
                    <strong className="text-gray-400">Lesson:</strong> {r.llm_eval.lesson}
                  </div>
                </>
              )}
            </div>
          ))}
        </div>
      )}

      <p className="text-[10px] text-gray-600 italic mt-3">
        Framework active — scores accumulate as live trades resolve
      </p>
    </div>
  );
}
