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

const realtimeListeners = new Set<() => void>();
let sharedEventSource: EventSource | null = null;

export function openEventStream(onEvent: () => void): () => void {
  const token = getToken();
  if (!token || typeof EventSource === "undefined") return () => undefined;
  realtimeListeners.add(onEvent);
  if (!sharedEventSource) {
    sharedEventSource = new EventSource(`/api/events/stream?token=${encodeURIComponent(token)}`);
    sharedEventSource.onmessage = () => {
      for (const fn of realtimeListeners) fn();
    };
    sharedEventSource.onerror = () => {
      /* EventSource reconnects automatically */
    };
  }
  return () => {
    realtimeListeners.delete(onEvent);
    if (realtimeListeners.size === 0 && sharedEventSource) {
      sharedEventSource.close();
      sharedEventSource = null;
    }
  };
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
  jev_status?: string;
  gmgn_configured?: boolean;
};

export type Provider = {
  provider: string;
  status: string;
  configured: boolean;
  circuit: string;
  latency_ms: number | null;
  last_error: string | null;
  detail?: string | null;
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
  quote: { source?: string; price_usd?: string; venue?: string } | null;
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
  seat?: string | null;
  type: string;
  mint: string | null;
  correlation_id: string;
  sequence: number;
  payload: Record<string, unknown>;
};

export type SeatName = "CHIEF" | "SCAN" | "VET" | "SOCIAL" | "SIZE" | "FILLS" | "RISK" | "CRAWLER";

export type SeatCard = {
  seat: SeatName;
  subtitle: string;
  badge: string;
  jev_connected: boolean;
  status: string;
  metric_label: string;
  metric_value: string;
  bar_ratio: number | null;
  stats?: {
    pages: number;
    posts_read: number;
    webs_spun: number;
    rugs_killed: number;
    benched: number;
  };
  log_line: string;
  speech_bubble: string | null;
  target_station: "desk" | "screen" | "center" | "chief";
  active_mint: string | null;
  active_symbol: string;
  last_heartbeat: string | null;
  processed: number;
  errors: number;
};

export type CandlePoint = {
  at: string;
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  up: boolean;
  provider: string;
};

export type CandidateToken = {
  mint: string;
  symbol: string;
  name: string;
  holder_count: number | null;
  promoted: boolean;
  price: string | null;
  liquidity: string | null;
  market_cap: string | null;
  volume_m5: string | null;
  volume_h1: string | null;
  change_m5: string | null;
  change_h1: string | null;
  buys_m5: number | null;
  sells_m5: number | null;
  discovery_score: number;
  momentum_score: number | null;
  security_status: string;
  security_score: number | null;
  critical_flags: string[];
  top_holder_pct: string | null;
  x_handle: string | null;
  proposal_status: string;
  provider: string | null;
  observed_at: string | null;
};

export type FeaturedToken = CandidateToken & {
  candles: CandlePoint[];
  checks: { name: string; status: string; detail: string }[];
};

export type DeskState = {
  mode: "demo" | "live";
  data_label: string;
  paper_trading: boolean;
  live_execution_enabled: boolean;
  emergency_stop: boolean;
  emergency_stop_reason: string | null;
  utc_now: string;
  day_label: string;
  clock_utc: string;
  jev: {
    connected: boolean;
    configured: boolean;
    status_label: string;
    requested_model: string;
    resolved_model: string | null;
    total_calls: number;
    total_input_tokens: number;
    halted_reason: string | null;
  };
  gmgn: {
    configured: boolean;
    status: string;
    detail: string | null;
    venue_label: string;
    live_swaps_enabled: boolean;
  };
  grok: {
    configured: boolean;
    status_label: string;
  };
  tailscale: Tailscale;
  seats: SeatCard[];
  featured_token: FeaturedToken | null;
  candidates: CandidateToken[];
  crawler_web: {
    active: boolean;
    source: string;
    symbol: string | null;
    mint: string | null;
    callouts: string[];
  };
  balance: {
    equity: string | null;
    cash: string | null;
    initial_equity: string;
    daily_pnl: string | null;
    total_pnl: string | null;
    open_exposure: string | null;
    max_drawdown: string | null;
    open_positions: number;
    held_symbol: string;
    wins: number;
    losses: number;
    curve: { at: string; time: string; equity: number; delta: number; drawdown: number }[];
  };
  jev_analysis: {
    scanned_count: number;
    observations_total: number;
    benched_count: number;
    audit_rows: {
      seq: number;
      mint: string;
      symbol: string;
      stage: string;
      reason: string;
      status: string;
      jev_model: string | null;
      disagreement: boolean;
      at: string;
    }[];
  };
  thresholds: {
    label: string;
    disclaimer: string;
    hard: Record<string, number>;
    soft: Record<string, { dir: string; limit: number } | number>;
    guide_inspiration: {
      label: string;
      disclaimer: string;
      hard: Record<string, number>;
    };
    stage_counts: Record<string, number>;
    stage_breakdown: Record<string, unknown>;
    shadow_mode: boolean;
    one_position_at_a_time: boolean;
    use_bench: boolean;
  };
};

export type SeatInspection = {
  seat: SeatName;
  logical_agent: string;
  card: SeatCard;
  heartbeat_age_seconds: number | null;
  last_duration_ms: number | null;
  last_error: string | null;
  data_sources: {
    mode: string;
    gmgn: DeskState["gmgn"];
    jev: DeskState["jev"];
    grok: DeskState["grok"];
  };
  thresholds: {
    hard: Record<string, number>;
    soft: Record<string, unknown>;
  };
  recent_vetoes: {
    at: string;
    mint: string | null;
    approved: boolean;
    reason_codes: string[];
    explanation: string;
  }[];
  events: SwarmEvent[];
};

export type DemoPlaybackStep = {
  step: number;
  phase: string;
  clock: string;
  active_seat: SeatName;
  target: "desk" | "screen" | "center" | "chief";
  mint: string;
  symbol: string;
  bubble: string;
  crawler_web: boolean;
  funnel_stage: string;
};

export type DemoPlaybackData = {
  is_demo_playback: boolean;
  label: string;
  steps: DemoPlaybackStep[];
};
