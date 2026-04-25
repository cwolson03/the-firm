const API = process.env.NEXT_PUBLIC_API_URL || "http://100.110.64.74:8000";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${API}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API ${path}: ${res.status}`);
  return res.json();
}

// Types

export interface AgentInfo {
  last_run: string;
  last_run_ts: number;
  ok?: boolean;
  mode?: string;
  [key: string]: unknown;
}

export interface StatusResponse {
  service_running: boolean;
  uptime_seconds: number;
  agents: Record<string, AgentInfo>;
  kalshi_positions: number;
  rag_stats: {
    disclosures: number;
    profiles: number;
    context: number;
  };
  eval_trades: number;
  weather_win_rate: number;
}

export interface ActivityEntry {
  timestamp: string;
  level: string;
  agent: string;
  message: string;
  raw: string;
}

export interface PositionItem {
  ticker: string;
  title: string;
  side: string;
  contracts: number;
  avg_price_cents: number;
  exposure: number;
  realized_pnl: number;
}

export interface WeatherResponse {
  total: number;
  resolved: number;
  wins: number;
  win_rate: number;
  by_city: Record<string, { total: number; wins: number; win_rate: number; edge_avg: number | null }>;
  by_source: Record<string, { total: number; wins: number; win_rate: number }>;
  recent: any[];
  open: any[];
}

export interface BradResponse {
  total: number;
  resolved: number;
  wins: number;
  win_rate: number;
  by_strategy: Record<string, { total: number; wins: number; win_rate: number }>;
  recent: any[];
  open: any[];
}

export interface RagDemoResponse {
  query: { member: string; ticker: string; trade_type: string };
  retrieved_context: { text: string; index: number }[];
  member_profile: string;
  market_context: string[];
  llm_model: string;
  llm_reasoning: string;
  llm_confidence: string;
  go: boolean;
  risks: string[];
  latency_ms: number;
}

export interface PortfolioItem {
  ticker: string;
  price: number | null;
  change_pct: number | null;
  gain_pct: number | null;
  error?: string;
}

export interface EvalRecord {
  trade_id: string;
  agent: string;
  market: string;
  direction: string;
  outcome: string;
  pnl_pct: number;
  llm_eval?: {
    process_score: number;
    edge_quality: string;
    what_worked: string;
    what_to_improve: string;
    lesson: string;
    avoid_next_time: string;
  };
}

export interface FileInfo {
  name: string;
  lines: number;
  size_kb: number;
  modified: string;
}

export interface FileContent {
  path: string;
  content: string;
  lines: number;
  size_kb: number;
  modified: string;
}

// API calls

export const fetchHealth = () => get<{ status: string; timestamp: string }>("/health");
export const fetchStatus = () => get<StatusResponse>("/api/status");
export const fetchActivity = (n = 100, agent = "") =>
  get<ActivityEntry[]>(`/api/activity?n=${n}${agent ? `&agent=${agent}` : ""}`);
export const fetchPositions = () => get<PositionItem[]>("/api/positions");
export const fetchWeather = () => get<WeatherResponse>("/api/weather");
export const fetchBrad = () => get<BradResponse>("/api/brad");
export const fetchEval = () => get<EvalRecord[]>("/api/eval");
export const fetchRagDemo = (member: string, ticker: string, trade_type: string) =>
  get<RagDemoResponse>(
    `/api/rag-demo?member=${encodeURIComponent(member)}&ticker=${encodeURIComponent(ticker)}&trade_type=${encodeURIComponent(trade_type)}`
  );
export const fetchPortfolio = () => get<PortfolioItem[]>("/api/portfolio");
export const fetchKalshiBalance = () => get<{ balance: number }>("/api/kalshi/balance");
export const fetchKalshiHistory = () => get<any[]>("/api/kalshi/history");
export const fetchFiles = () => get<FileInfo[]>("/api/files");
export const fetchFile = (path: string) => get<FileContent>(`/api/file?path=${encodeURIComponent(path)}`);
