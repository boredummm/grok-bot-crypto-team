const TOKEN_KEY = "solswarm.token";

export function getToken(): string {
  return sessionStorage.getItem(TOKEN_KEY) || "";
}

export function setToken(value: string): void {
  sessionStorage.setItem(TOKEN_KEY, value.trim());
}

export function clearToken(): void {
  sessionStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const response = await fetch(path, { ...init, headers });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const payload = await response.json();
      detail = payload.detail || detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(response.status, typeof detail === "string" ? detail : "request failed");
  }
  return response.json() as Promise<T>;
}

export type Summary = {
  mode: "demo" | "live";
  data_label: string;
  paper_trading: boolean;
  live_execution_enabled: boolean;
  emergency_stop: boolean;
  emergency_stop_reason: string | null;
  equity: string | null;
  cash: string | null;
  daily_pnl: string | null;
  daily_pnl_available: boolean;
  total_pnl: string | null;
  total_pnl_available: boolean;
  win_rate: string | null;
  win_rate_available: boolean;
  profit_factor: string | null;
  profit_factor_available: boolean;
  open_exposure: string | null;
  max_drawdown: string | null;
  max_drawdown_available: boolean;
  open_positions: number;
  active_agents: number;
  provider_health: Provider[];
  tailscale: Tailscale;
  last_scan_at: string | null;
  alerts_open: number;
  ai_commentary: string;
};

export type Provider = {
  provider: string;
  status: string;
  configured: boolean;
  circuit: string;
  latency_ms: number | null;
  last_error: string | null;
};

export type Tailscale = {
  installed: boolean;
  connected: boolean;
  dns_name: string | null;
  tailscale_ips: string[];
  backend_state: string | null;
  serve_enabled: boolean;
  funnel_enabled: boolean;
  detail: string;
};

export type TokenRow = {
  mint: string;
  symbol: string;
  name: string;
  promoted: boolean;
  liquidity: string | null;
  market_cap: string | null;
  volume_m5: string | null;
  price: string | null;
  price_change_m5: string | null;
  discovery_score: number | null;
  momentum_score: number | null;
  security_status: string;
  candidate_status: string;
  provider: string | null;
  observed_at: string | null;
  data_label: string | null;
};

export type Position = {
  id: string;
  mint: string;
  symbol: string;
  pool: string;
  status: string;
  qty: string;
  entry_price: string;
  mark_price: string | null;
  mark_stale: boolean;
  cost_basis: string;
  unrealized_pnl: string | null;
  unrealized_available: boolean;
  realized_pnl: string;
  fees: string;
  exit_conditions: Record<string, string | number>;
  exit_reason: string | null;
  unsellable_reason: string | null;
  data_label: string | null;
};

export type Order = {
  id: string;
  mint: string;
  side: string;
  status: string;
  requested_notional: string;
  filled_notional: string;
  remaining_notional: string;
  reason: string | null;
  created_at: string;
  quote: { source?: string; price_usd?: string } | null;
  fills: { price: string; fee: string; fee_source: string; price_impact_bps: string | null }[];
};

export type Agent = {
  name: string;
  status: string;
  last_heartbeat: string | null;
  last_task: string | null;
  last_duration_ms: number | null;
  processed: number;
  errors: number;
  last_error: string | null;
  last_decision: string | null;
};

export type SwarmEvent = {
  id: string;
  at: string;
  source: string;
  type: string;
  mint: string | null;
  correlation_id: string;
  sequence: number;
  payload: Record<string, unknown>;
};
