"use client";

import { useEffect, useState } from "react";
import { fetchPortfolio, type PortfolioItem } from "@/lib/api";
import Link from "next/link";

export default function PortfolioPage() {
  const [data, setData] = useState<PortfolioItem[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchPortfolio()
      .then(setData)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="min-h-screen">
      <div className="border-b border-gray-800 px-6 py-4 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold" style={{ color: "#00ff88" }}>
            Portfolio Performance
          </h1>
          <p className="text-gray-500 text-sm">Gain % vs. average cost basis</p>
        </div>
        <Link
          href="/"
          className="text-xs text-gray-500 hover:text-gray-300 border border-gray-700 px-3 py-1.5 rounded transition-colors"
        >
          ← Dashboard
        </Link>
      </div>

      <div className="p-6">
        {loading ? (
          <p className="text-gray-500 text-sm">Loading prices...</p>
        ) : (
          <div className="rounded-lg border border-gray-800 overflow-hidden" style={{ background: "#111" }}>
            <table className="w-full text-sm">
              <thead>
                <tr className="text-gray-500 border-b border-gray-800 text-xs">
                  <th className="text-left px-4 py-3 font-medium">Symbol</th>
                  <th className="text-right px-4 py-3 font-medium">Current Price</th>
                  <th className="text-right px-4 py-3 font-medium">Day %</th>
                  <th className="text-right px-4 py-3 font-medium">Gain %</th>
                </tr>
              </thead>
              <tbody>
                {data.map((item) => (
                  <tr key={item.ticker} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                    <td className="px-4 py-2.5 font-medium text-gray-300">{item.ticker}</td>
                    <td className="px-4 py-2.5 text-right text-gray-400">
                      {item.price ? `$${item.price.toFixed(2)}` : "—"}
                    </td>
                    <td
                      className="px-4 py-2.5 text-right font-medium"
                      style={{
                        color:
                          item.change_pct === null
                            ? "#666"
                            : item.change_pct >= 0
                            ? "#00ff88"
                            : "#ff4444",
                      }}
                    >
                      {item.change_pct !== null
                        ? `${item.change_pct >= 0 ? "+" : ""}${item.change_pct}%`
                        : "—"}
                    </td>
                    <td
                      className="px-4 py-2.5 text-right font-semibold"
                      style={{
                        color:
                          item.gain_pct === null
                            ? "#666"
                            : item.gain_pct >= 0
                            ? "#00ff88"
                            : "#ff4444",
                      }}
                    >
                      {item.gain_pct !== null
                        ? `${item.gain_pct >= 0 ? "+" : ""}${item.gain_pct.toLocaleString()}%`
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <p className="text-[10px] text-gray-700 mt-4 text-center">
          Prices updated live · No dollar amounts displayed
        </p>
      </div>
    </div>
  );
}
