/**
 * API client for The Firm dashboard.
 *
 * Two modes:
 *  - Live mode (default): calls FastAPI backend at NEXT_PUBLIC_API_URL
 *  - Fixture mode: reads from /data/*.json committed to the repo
 *    Enable by setting NEXT_PUBLIC_USE_FIXTURES=true
 *
 * For Vercel deployment set NEXT_PUBLIC_API_URL to your API server URL.
 * For local dev against Atlas: NEXT_PUBLIC_API_URL=http://100.110.64.74:8000
 */

const API_URL = process.env.NEXT_PUBLIC_API_URL || '';
const USE_FIXTURES = process.env.NEXT_PUBLIC_USE_FIXTURES === 'true';

async function get<T>(path: string, fixturePath?: string): Promise<T> {
  if (USE_FIXTURES && fixturePath) {
    const res = await fetch(fixturePath, { cache: 'no-store' });
    if (!res.ok) throw new Error(`Fixture ${fixturePath}: ${res.status}`);
    return res.json();
  }
  if (!API_URL) throw new Error('NEXT_PUBLIC_API_URL not set and fixtures disabled');
  const res = await fetch(`${API_URL}${path}`, { cache: 'no-store' });
  if (!res.ok) throw new Error(`API ${path}: ${res.status}`);
  return res.json();
}

// ── Types ──────────────────────────────────────────────────────────────────

export interface AgentInfo {
  last_run: string;
  last_run_ts?: number;
  ok?: boolean;
  mode?: string;
  source?: string;
  [key: string]: unknown;
}

export interface StatusResponse {
  service_running: boolean;
  uptime_seconds: number;
  agents: Record<string, AgentInfo>;
  kalshi_positions: number;
  rag_stats: { disclosures: number; profiles: number; context: number };
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

export interface WeatherTrade {
  ticker: string;
  series: string;
  city_name: string;
  date: string;
  strike_type: string;
  low?: number;
  high?: number;
  threshold?: number;
  direction: string;
  forecast_high?: number;
  forecast_source?: string;
  kalshi_prob: number;
  model_prob: number;
  edge: number;
  contracts: number;
  cost: number;
  timestamp: string;
  status: string;
  result?: string;
}

export interface WeatherResponse {
  total: number;
  resolved: number;
  wins: number;
  win_rate: number;
  by_city: Record<string, { total: number; wins: number; win_rate: number; edge_avg: number | null }>;
  by_source: Record<string, { total: number; wins: number; win_rate: number }>;
  recent: WeatherTrade[];
}

export interface BradTrade {
  id?: string;
  ticker?: string;
  title?: string;
  favorite_side?: string;
  stink_price?: number;
  stink_price_c?: number;
  contracts?: number;
  cost?: number;
  payout_if_win?: number;
  strategy?: string;
  status: string;
  result?: string;
  timestamp?: string;
}

export interface BradResponse {
  total: number;
  resolved: number;
  wins: number;
  win_rate: number;
  by_strategy: Record<string, { total: number; wins: number; win_rate: number }>;
  recent: BradTrade[];
}

export interface EvalRecord {
  trade_id: string;
  agent: string;
  market: string;
  direction: string;
  entry_date: string;
  entry_edge_pct: number;
  llm_confidence_at_entry: string;
  outcome: string;
  pnl_pct: number;
  resolved_date: string;
  llm_eval: {
    process_score?: number;
    edge_quality?: string;
    what_worked?: string;
    what_to_improve?: string;
    lesson?: string;
    avoid_next_time?: string;
  };
  raw_thesis: string;
  raw_llm_reason: string;
}

export interface PositionItem {
  ticker: string;
  side: string;
  contracts: number;
  avg_price_cents: number;
  exposure: number;
  realized_pnl?: number;
  title?: string;
}

export interface PortfolioItem {
  ticker: string;
  price: number;
  change_pct: number;
  gain_pct: number;
  avg_cost: number;
  day_gain?: number;
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
  error?: string;
}

export interface FileInfo {
  name: string;
  lines: number;
  size_kb: number;
  modified: string;
}

// ── API functions ──────────────────────────────────────────────────────────

export async function getStatus(): Promise<StatusResponse> {
  return get<StatusResponse>('/api/status', '/data/status.json');
}

export async function getActivity(n = 100, agent?: string): Promise<ActivityEntry[]> {
  const path = `/api/activity?n=${n}${agent ? `&agent=${encodeURIComponent(agent)}` : ''}`;
  const data = await get<ActivityEntry[]>(path, '/data/activity.json');
  if (agent && Array.isArray(data)) {
    return data.filter(e => e.agent.toLowerCase().includes(agent.toLowerCase()));
  }
  return data;
}

export async function getPositions(): Promise<PositionItem[]> {
  return get<PositionItem[]>('/api/positions', '/data/positions.json');
}

export async function getWeather(): Promise<WeatherResponse> {
  return get<WeatherResponse>('/api/weather', '/data/weather.json');
}

export async function getBrad(): Promise<BradResponse> {
  return get<BradResponse>('/api/brad', '/data/brad.json');
}

export async function getEval(): Promise<EvalRecord[]> {
  return get<EvalRecord[]>('/api/eval', '/data/eval.json');
}

export async function getPortfolio(): Promise<PortfolioItem[]> {
  return get<PortfolioItem[]>('/api/portfolio', '/data/portfolio.json');
}

export async function getRagDemo(
  member: string,
  ticker: string,
  tradeType: string
): Promise<RagDemoResponse> {
  // RAG demo always hits the live API — it's the only live feature in fixture mode
  if (!API_URL) {
    // Return a demo response when no API is configured
    return {
      query: { member, ticker, trade_type: tradeType },
      retrieved_context: [],
      member_profile: 'API not configured — set NEXT_PUBLIC_API_URL to enable live RAG queries.',
      market_context: [],
      llm_model: 'unavailable',
      llm_reasoning: 'Configure NEXT_PUBLIC_API_URL to run live congressional intelligence queries.',
      llm_confidence: 'none',
      go: false,
      risks: [],
      latency_ms: 0,
    };
  }
  const path = `/api/rag-demo?member=${encodeURIComponent(member)}&ticker=${encodeURIComponent(ticker)}&trade_type=${encodeURIComponent(tradeType)}`;
  return get<RagDemoResponse>(path);
}

export async function fetchKalshiBalance(): Promise<{ balance: number }> {
  return get<{ balance: number }>('/api/kalshi/balance', '/data/balance.json');
}

export async function fetchKalshiHistory(): Promise<EvalRecord[]> {
  return get<EvalRecord[]>('/api/kalshi/history', '/data/eval.json');
}

export async function getFiles(): Promise<FileInfo[]> {
  return get<FileInfo[]>('/api/files', '/data/files.json');
}

export async function getFile(path: string): Promise<{ content: string; lines: number; size_kb: number; modified: string }> {
  if (USE_FIXTURES) {
    return { content: '# Source files available when connected to live API', lines: 1, size_kb: 0, modified: '' };
  }
  return get<{ content: string; lines: number; size_kb: number; modified: string }>(`/api/file?path=${encodeURIComponent(path)}`);
}
