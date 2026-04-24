"use client";

interface Props {
  data: Record<string, unknown>[] | null;
}

export default function Positions({ data }: Props) {
  const positions = data || [];

  return (
    <div className="rounded-lg border border-gray-800 p-4" style={{ background: "#111111" }}>
      <h3 className="text-sm font-semibold uppercase tracking-wider text-gray-400 mb-3">
        Kalshi Positions
      </h3>

      {positions.length === 0 ? (
        <p className="text-xs text-gray-500 italic">No open positions</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-gray-600 border-b border-gray-800">
                <th className="text-left py-1 font-medium">Market</th>
                <th className="text-right py-1 font-medium">Side</th>
                <th className="text-right py-1 font-medium">Contracts</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((p, i) => (
                <tr key={i} className="border-b border-gray-800/50">
                  <td className="py-1.5 text-gray-300 max-w-[200px] truncate">
                    {String(p.ticker || p.market || "—")}
                  </td>
                  <td className="py-1.5 text-right text-gray-400">
                    {String(p.side || p.direction || "—")}
                  </td>
                  <td className="py-1.5 text-right text-gray-400">
                    {String(p.contracts || p.count || "—")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
