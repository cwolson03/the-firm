"use client";

import { useState, useEffect, useCallback } from "react";
import { fetchRagDemo, type RagDemoResponse } from "@/lib/api";

const PRESETS = [
  { label: "Pelosi / NVDA / Purchase", member: "Nancy Pelosi", ticker: "NVDA", trade_type: "Purchase" },
  { label: "Khanna / TSM / Purchase", member: "Ro Khanna", ticker: "TSM", trade_type: "Purchase" },
  { label: "Crenshaw / COIN / Purchase", member: "Dan Crenshaw", ticker: "COIN", trade_type: "Purchase" },
];

const STAGES = [
  "Embedding Query",
  "Retrieving Context",
  "Loading Profile",
  "LLM Analysis",
  "Signal Generation",
];

export default function RagDemo() {
  const [member, setMember] = useState("Nancy Pelosi");
  const [ticker, setTicker] = useState("NVDA");
  const [tradeType, setTradeType] = useState("Purchase");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<RagDemoResponse | null>(null);
  const [error, setError] = useState("");
  const [activeStage, setActiveStage] = useState(-1);
  const [revealedStage, setRevealedStage] = useState(-1);

  const run = useCallback(async () => {
    setLoading(true);
    setError("");
    setResult(null);
    setActiveStage(0);
    setRevealedStage(-1);

    try {
      // Animate stages while waiting
      const stageTimer = setInterval(() => {
        setActiveStage((prev) => (prev < 4 ? prev + 1 : prev));
      }, 800);

      const data = await fetchRagDemo(member, ticker, tradeType);
      clearInterval(stageTimer);
      setResult(data);

      // Reveal stages sequentially
      for (let i = 0; i < 5; i++) {
        await new Promise((r) => setTimeout(r, 300));
        setRevealedStage(i);
      }
      setActiveStage(5);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Pipeline failed");
    } finally {
      setLoading(false);
    }
  }, [member, ticker, tradeType]);

  return (
    <div className="rounded-lg border border-gray-800 p-4" style={{ background: "#111111" }}>
      <h2 className="text-sm font-semibold uppercase tracking-wider text-gray-400 mb-3">
        RAG Pipeline Demo
      </h2>

      {/* Presets */}
      <div className="flex gap-1.5 mb-3 flex-wrap">
        {PRESETS.map((p) => (
          <button
            key={p.label}
            onClick={() => {
              setMember(p.member);
              setTicker(p.ticker);
              setTradeType(p.trade_type);
            }}
            className="text-[10px] px-2 py-0.5 rounded-full border border-gray-700 text-gray-500 hover:text-gray-300 hover:border-gray-500 transition-all"
          >
            {p.label}
          </button>
        ))}
      </div>

      {/* Inputs */}
      <div className="grid grid-cols-3 gap-2 mb-3">
        <input
          value={member}
          onChange={(e) => setMember(e.target.value)}
          placeholder="Member"
          className="bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-xs text-gray-300 focus:border-green-500/50 outline-none"
        />
        <input
          value={ticker}
          onChange={(e) => setTicker(e.target.value)}
          placeholder="Ticker"
          className="bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-xs text-gray-300 focus:border-green-500/50 outline-none"
        />
        <select
          value={tradeType}
          onChange={(e) => setTradeType(e.target.value)}
          className="bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-xs text-gray-300 focus:border-green-500/50 outline-none"
        >
          <option>Purchase</option>
          <option>Sale</option>
        </select>
      </div>

      <button
        onClick={run}
        disabled={loading}
        className="w-full py-2 rounded text-sm font-semibold transition-all disabled:opacity-50"
        style={{
          background: loading ? "#1a1a1a" : "rgba(0,255,136,0.1)",
          color: "#00ff88",
          border: "1px solid rgba(0,255,136,0.3)",
        }}
      >
        {loading ? "Running Pipeline..." : "Run Pipeline →"}
      </button>

      {error && (
        <div className="mt-3 text-xs text-red-400 bg-red-500/10 p-2 rounded">{error}</div>
      )}

      {/* Pipeline Stages */}
      {(loading || result) && (
        <div className="mt-4 space-y-2">
          {STAGES.map((stage, i) => {
            const isActive = loading && activeStage === i;
            const isComplete = loading ? activeStage > i : revealedStage >= i;
            const isPending = loading ? activeStage < i : revealedStage < i;

            return (
              <div key={stage}>
                <div className="flex items-center gap-2 mb-1">
                  <div
                    className={`w-2 h-2 rounded-full transition-all duration-300 ${
                      isActive ? "pulse-green" : ""
                    }`}
                    style={{
                      background: isComplete
                        ? "#00ff88"
                        : isActive
                        ? "#00ff88"
                        : "#333",
                    }}
                  />
                  <span
                    className={`text-xs font-medium transition-all ${
                      isComplete || isActive ? "text-gray-300" : "text-gray-600"
                    }`}
                  >
                    {stage}
                  </span>
                  {isActive && (
                    <span className="text-[10px] text-green-400 animate-pulse">processing...</span>
                  )}
                </div>

                {/* Stage content */}
                {result && revealedStage >= i && (
                  <div className="ml-4 animate-fade-in">
                    {i === 0 && (
                      <div className="text-[10px] text-gray-500 bg-gray-900 p-2 rounded font-mono">
                        embed(&quot;{result.query.member} {result.query.ticker} {result.query.trade_type}&quot;)
                      </div>
                    )}
                    {i === 1 && (
                      <div className="space-y-1 max-h-32 overflow-y-auto">
                        {result.retrieved_context.slice(0, 3).map((ctx) => (
                          <div
                            key={ctx.index}
                            className="text-[10px] text-gray-500 bg-gray-900 p-2 rounded border-l-2 border-blue-500/30"
                          >
                            {ctx.text.slice(0, 200)}
                            {ctx.text.length > 200 ? "..." : ""}
                          </div>
                        ))}
                        {result.retrieved_context.length === 0 && (
                          <div className="text-[10px] text-gray-600 italic">
                            No prior disclosures found
                          </div>
                        )}
                      </div>
                    )}
                    {i === 2 && result.member_profile && (
                      <div className="text-[10px] text-gray-500 bg-gray-900 p-2 rounded max-h-20 overflow-y-auto">
                        {result.member_profile.slice(0, 300)}
                        {result.member_profile.length > 300 ? "..." : ""}
                      </div>
                    )}
                    {i === 3 && (
                      <div className="space-y-1">
                        <div className="flex items-center gap-2">
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-purple-500/20 text-purple-400 font-mono">
                            {result.llm_model}
                          </span>
                          {result.llm_confidence && (
                            <span
                              className="text-[10px] px-1.5 py-0.5 rounded font-semibold"
                              style={{
                                background:
                                  result.llm_confidence === "high"
                                    ? "rgba(0,255,136,0.15)"
                                    : result.llm_confidence === "medium"
                                    ? "rgba(234,179,8,0.15)"
                                    : "rgba(255,68,68,0.15)",
                                color:
                                  result.llm_confidence === "high"
                                    ? "#00ff88"
                                    : result.llm_confidence === "medium"
                                    ? "#eab308"
                                    : "#ff4444",
                              }}
                            >
                              {result.llm_confidence}
                            </span>
                          )}
                        </div>
                        <div className="text-[10px] text-gray-400 bg-gray-900 p-2 rounded max-h-28 overflow-y-auto leading-relaxed">
                          {result.llm_reasoning || "No reasoning returned"}
                        </div>
                      </div>
                    )}
                    {i === 4 && (
                      <div className="space-y-1">
                        <div className="flex items-center gap-2">
                          <span
                            className="text-xs font-bold px-2 py-0.5 rounded"
                            style={{
                              background: result.go
                                ? "rgba(0,255,136,0.15)"
                                : "rgba(255,68,68,0.15)",
                              color: result.go ? "#00ff88" : "#ff4444",
                            }}
                          >
                            {result.go ? "GO" : "NO-GO"}
                          </span>
                          <span className="text-[10px] text-gray-600">
                            Pipeline completed in {result.latency_ms}ms
                          </span>
                        </div>
                        {result.risks && result.risks.length > 0 && (
                          <div className="text-[10px] text-gray-500 space-y-0.5 mt-1">
                            {result.risks.map((r, ri) => (
                              <div key={ri} className="flex items-start gap-1">
                                <span className="text-red-500">⚠</span>
                                <span>{r}</span>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      <p className="text-gray-600 text-[10px] italic mt-4">
        Replace STOCK Act disclosures with ATS contact histories and it&apos;s the same architecture.
      </p>
    </div>
  );
}
