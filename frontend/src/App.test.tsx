import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import App from "./App";

const MOCK_SUMMARY = {
  mode: "demo",
  data_label: "demo fixture",
  paper_trading: true,
  live_execution_enabled: false,
  emergency_stop: false,
  emergency_stop_reason: null,
  equity: "999.940000",
  cash: "979.940000",
  daily_pnl: "-0.060000",
  daily_pnl_available: true,
  total_pnl: "-0.060000",
  total_pnl_available: true,
  win_rate: null,
  win_rate_available: false,
  profit_factor: null,
  profit_factor_available: false,
  open_exposure: "20.000000",
  max_drawdown: "0.000060",
  max_drawdown_available: true,
  open_positions: 1,
  active_agents: 8,
  provider_health: [],
  tailscale: {
    installed: false,
    connected: false,
    dns_name: null,
    tailscale_ips: [],
    backend_state: null,
    serve_enabled: false,
    funnel_enabled: false,
    detail: "Tailscale CLI not installed.",
  },
  last_scan_at: "2026-11-15T22:00:00+00:00",
  alerts_open: 0,
  ai_commentary: "unavailable",
  jev_status: "not_connected",
  gmgn_configured: false,
};

const MOCK_DESK = {
  mode: "demo",
  data_label: "DEMO FIXTURES (PAPER)",
  paper_trading: true,
  live_execution_enabled: false,
  emergency_stop: false,
  emergency_stop_reason: null,
  utc_now: "2026-11-15T22:00:00+00:00",
  day_label: "DAY 1 · NOV 15",
  clock_utc: "22:00 UTC",
  jev: {
    connected: false,
    configured: false,
    status_label: "JEV NOT CONNECTED",
    requested_model: "jev-latest",
    resolved_model: null,
    total_calls: 0,
    total_input_tokens: 0,
    halted_reason: null,
  },
  gmgn: {
    configured: false,
    status: "not_used",
    detail: "Demo mode uses labeled fixtures.",
    venue_label: "GMGN KEY NOT SET (SECONDARY FEEDS)",
    live_swaps_enabled: false,
  },
  grok: { configured: false, status_label: "GROK UNCONFIGURED" },
  tailscale: MOCK_SUMMARY.tailscale,
  seats: [
    { seat: "CHIEF", subtitle: "never takes a position", badge: "JEV OFF · DET", jev_connected: false, status: "ok", metric_label: "mode · deterministic", metric_value: "last pick DEMO-MOM", bar_ratio: 0.75, log_line: "JEV NOT CONNECTED · deterministic strategy gate", speech_bubble: "pick: DEMO-MOM", target_station: "chief", active_mint: "demo:momentum", active_symbol: "DEMO-MOM", last_heartbeat: "2026-11-15T22:00:00Z", processed: 1, errors: 0 },
    { seat: "SCAN", subtitle: "finding what moves", badge: "MATH ONLY", jev_connected: false, status: "ok", metric_label: "momentum.det · DEMO-MOM", metric_value: "0.86", bar_ratio: 0.86, log_line: "5-minute price is higher.", speech_bubble: "scoring DEMO-MOM (0.86)", target_station: "screen", active_mint: "demo:momentum", active_symbol: "DEMO-MOM", last_heartbeat: "2026-11-15T22:00:00Z", processed: 5, errors: 0 },
    { seat: "VET", subtitle: "chain risk, is it real", badge: "RPC + GMGN", jev_connected: false, status: "ok", metric_label: "chain_safety · DEMO-MOM", metric_value: "PASS", bar_ratio: 1.0, log_line: "Checked authorities and liquidity.", speech_bubble: "1 rugs blocked", target_station: "center", active_mint: "demo:momentum", active_symbol: "DEMO-MOM", last_heartbeat: "2026-11-15T22:00:00Z", processed: 5, errors: 0 },
    { seat: "SOCIAL", subtitle: "the project's X account", badge: "HANDLE ONLY", jev_connected: false, status: "unconfigured", metric_label: "x_account · DEMO-MOM", metric_value: "@demomom_sol", bar_ratio: 0.5, log_line: "On-chain X handle @demomom_sol verified", speech_bubble: null, target_station: "desk", active_mint: "demo:momentum", active_symbol: "DEMO-MOM", last_heartbeat: "2026-11-15T22:00:00Z", processed: 5, errors: 0 },
    { seat: "SIZE", subtitle: "how much", badge: "MATH", jev_connected: false, status: "ok", metric_label: "ticket 2.0%", metric_value: "$20.00", bar_ratio: 0.33, log_line: "max $20 · <=2% bank", speech_bubble: "ticket $20", target_station: "desk", active_mint: "demo:momentum", active_symbol: "DEMO-MOM", last_heartbeat: "2026-11-15T22:00:00Z", processed: 1, errors: 0 },
    { seat: "FILLS", subtitle: "execution (paper)", badge: "GMGN PAPER", jev_connected: false, status: "ok", metric_label: "last paper fill", metric_value: "buy $20.00 @ $1.00", bar_ratio: 1.0, log_line: "fee $0.06 (demo_fixture)", speech_bubble: "buy $20.00 @ $1.00", target_station: "center", active_mint: "demo:momentum", active_symbol: "DEMO-MOM", last_heartbeat: "2026-11-15T22:00:00Z", processed: 1, errors: 0 },
    { seat: "RISK", subtitle: "closing & final veto", badge: "FINAL VETO", jev_connected: false, status: "ok", metric_label: "open 1 · closed", metric_value: "0W 0L", bar_ratio: 0.65, log_line: "Entry approved within paper risk limits.", speech_bubble: "holding 1 open", target_station: "screen", active_mint: "demo:momentum", active_symbol: "DEMO-MOM", last_heartbeat: "2026-11-15T22:00:00Z", processed: 1, errors: 0 },
    { seat: "CRAWLER", subtitle: "reads first, then the desk trades", badge: "TAPS 5/5", jev_connected: false, status: "ok", metric_label: "observations", metric_value: "18", bar_ratio: 0.6, stats: { pages: 18, posts_read: 0, webs_spun: 5, rugs_killed: 1, benched: 3 }, log_line: "swept 6 pools (demo_fixture)", speech_bubble: "swept 5 mints", target_station: "screen", active_mint: "demo:momentum", active_symbol: "DEMO-MOM", last_heartbeat: "2026-11-15T22:00:00Z", processed: 5, errors: 0 },
  ],
  featured_token: {
    mint: "demo:momentum",
    symbol: "DEMO-MOM",
    name: "Demo Momentum",
    holder_count: 420,
    promoted: false,
    price: "1.00",
    liquidity: "80000",
    market_cap: "150000",
    volume_m5: "8000",
    volume_h1: "20000",
    change_m5: "4.0",
    change_h1: "6.0",
    buys_m5: 40,
    sells_m5: 10,
    discovery_score: 88,
    momentum_score: 86,
    security_status: "pass",
    security_score: 95,
    critical_flags: [],
    top_holder_pct: "8",
    x_handle: "demomom_sol",
    proposal_status: "approved",
    provider: "demo_fixture",
    observed_at: "2026-11-15T22:00:00Z",
    candles: [
      { at: "2026-11-15T21:55:00Z", time: "21:55", open: 0.96, high: 0.99, low: 0.96, close: 0.99, volume: 4000, up: true, provider: "demo_fixture" },
      { at: "2026-11-15T22:00:00Z", time: "22:00", open: 0.99, high: 1.0, low: 0.99, close: 1.0, volume: 8000, up: true, provider: "demo_fixture" },
    ],
    checks: [
      { name: "liquidity depth", status: "PASS", detail: "$80,000 vs $50,000" },
      { name: "authority safety", status: "PASS", detail: "mint+freeze checked" },
      { name: "jev judgement", status: "OFFLINE", detail: "JEV NOT CONNECTED" },
    ],
  },
  candidates: [
    {
      mint: "demo:momentum",
      symbol: "DEMO-MOM",
      name: "Demo Momentum",
      holder_count: 420,
      promoted: false,
      price: "1.00",
      liquidity: "80000",
      market_cap: "150000",
      volume_m5: "8000",
      volume_h1: "20000",
      change_m5: "4.0",
      change_h1: "6.0",
      buys_m5: 40,
      sells_m5: 10,
      discovery_score: 88,
      momentum_score: 86,
      security_status: "pass",
      security_score: 95,
      critical_flags: [],
      top_holder_pct: "8",
      x_handle: "demomom_sol",
      proposal_status: "approved",
      provider: "demo_fixture",
      observed_at: "2026-11-15T22:00:00Z",
    },
  ],
  crawler_web: { active: true, source: "demo_cycle_event", symbol: "DEMO-MOM", mint: "demo:momentum", callouts: ["liq $80,000"] },
  balance: {
    equity: "999.940000",
    cash: "979.940000",
    initial_equity: "1000.000000",
    daily_pnl: "-0.060000",
    total_pnl: "-0.060000",
    open_exposure: "20.000000",
    max_drawdown: "0.000060",
    open_positions: 1,
    held_symbol: "DEMO-MOM",
    wins: 0,
    losses: 0,
    curve: [
      { at: "2026-11-15T21:55:00Z", time: "21:55:00", equity: 1000.0, delta: 0, drawdown: 0 },
      { at: "2026-11-15T22:00:00Z", time: "22:00:00", equity: 999.94, delta: -0.06, drawdown: 0.00006 },
    ],
  },
  jev_analysis: {
    scanned_count: 5,
    observations_total: 18,
    benched_count: 3,
    audit_rows: [
      { seq: 1, mint: "demo:momentum", symbol: "DEMO-MOM", stage: "pick", reason: "clean_pass", status: "PASS", jev_model: null, disagreement: false, at: "2026-11-15T22:00:00Z" },
      { seq: 2, mint: "demo:freeze", symbol: "DEMO-FRZ", stage: "chain_kill", reason: "authority_open", status: "DROP", jev_model: null, disagreement: false, at: "2026-11-15T22:00:00Z" },
    ],
  },
  thresholds: {
    label: "experimental_paper_defaults",
    disclaimer: "Experimental paper-trading gates for a $1,000 virtual book.",
    hard: { min_age_minutes: 15, min_liquidity_usd: 50000, min_volume_h24: 12000, min_mcap_usd: 40000, min_trades_h24: 40, max_top_wallet: 0.25, max_top_10: 0.6 },
    soft: { shape_min_crowd: 0.55 },
    guide_inspiration: { label: "guide_reference_unvalidated", disclaimer: "Unvalidated", hard: { min_liquidity_usd: 12000 } },
    stage_counts: { free_kill: 1, trade_kill: 0, chain_kill: 1, soft_kill: 0, pick: 1 },
    stage_breakdown: {},
    shadow_mode: false,
    one_position_at_a_time: false,
    use_bench: false,
  },
};

beforeEach(() => {
  vi.restoreAllMocks();
});

afterEach(() => {
  cleanup();
});

it("shows the paper-trading gate before a token is stored", () => {
  sessionStorage.clear();
  render(
    <MemoryRouter>
      <App />
    </MemoryRouter>,
  );
  expect(screen.getByText("SOL SWARM")).toBeTruthy();
  expect(screen.getByText(/paper trading only/i)).toBeTruthy();
});

it("renders the SOL SWARM + JEV 8-seat GMGN control room, real paper balance, and seat inspector", async () => {
  sessionStorage.setItem("solswarm.token", "monitor-token-test");
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/api/summary")) return new Response(JSON.stringify(MOCK_SUMMARY), { status: 200 });
    if (url.includes("/api/providers")) return new Response(JSON.stringify([]), { status: 200 });
    if (url.includes("/api/desk/demo-playback")) {
      return new Response(
        JSON.stringify({
          is_demo_playback: true,
          label: "DEMO PLAYBACK",
          steps: [
            { step: 1, phase: "crawl", clock: "00:01 DEMO", active_seat: "CRAWLER", target: "screen", mint: "demo:momentum", symbol: "DEMO-MOM", bubble: "reading 5 demo pools", crawler_web: true, funnel_stage: "free_kill" },
          ],
        }),
        { status: 200 },
      );
    }
    if (url.includes("/api/desk/seats/VET")) {
      return new Response(
        JSON.stringify({
          seat: "VET",
          logical_agent: "security",
          card: MOCK_DESK.seats[2],
          heartbeat_age_seconds: 1.2,
          last_duration_ms: 4,
          last_error: null,
          data_sources: { mode: "demo", gmgn: MOCK_DESK.gmgn, jev: MOCK_DESK.jev, grok: MOCK_DESK.grok },
          thresholds: { hard: MOCK_DESK.thresholds.hard, soft: MOCK_DESK.thresholds.soft },
          recent_vetoes: [{ at: "2026-11-15T22:00:00Z", mint: "demo:freeze", approved: false, reason_codes: ["SECURITY_FAIL"], explanation: "active_freeze_authority" }],
          events: [{ id: "ev-1", at: "2026-11-15T22:00:00Z", source: "security", seat: "VET", type: "security.report", mint: "demo:freeze", correlation_id: "c1", sequence: 1, payload: { classification: "fail" } }],
        }),
        { status: 200 },
      );
    }
    if (url.includes("/api/desk")) return new Response(JSON.stringify(MOCK_DESK), { status: 200 });
    return new Response(JSON.stringify({}), { status: 200 });
  });

  const { container } = render(
    <MemoryRouter initialEntries={["/"]}>
      <App />
    </MemoryRouter>,
  );

  await waitFor(() => expect(screen.getByTestId("retro-control-room")).toBeTruthy());

  // Branding adapted to SOL SWARM + JEV and GMGN (zero FOMO references)
  expect(screen.getByText("SOL SWARM + JEV")).toBeTruthy();
  expect(screen.getByTestId("gmgn-venue-badge").textContent).toContain("GMGN PAPER ONLY");
  expect(container.textContent?.toUpperCase()).not.toContain("FOMO");

  // Honest JEV NOT CONNECTED status and real $999.94 paper balance
  expect(screen.getAllByText("JEV NOT CONNECTED").length).toBeGreaterThanOrEqual(1);
  expect(screen.getByTestId("paper-balance-value").textContent).toBe("$999.94");

  // All 8 original seats present in the 2x4 status grid
  for (const seat of ["CHIEF", "SCAN", "VET", "SOCIAL", "SIZE", "FILLS", "RISK", "CRAWLER"]) {
    expect(screen.getByTestId(`seat-card-${seat}`)).toBeTruthy();
  }

  // JEV · ANALYSIS and thresholds.py with pipeline stages
  expect(screen.getByTestId("jev-analysis-panel").textContent).toContain("005 SCANNED");
  expect(screen.getByTestId("thresholds-panel").textContent).toContain("free_kill");
  expect(screen.getByTestId("thresholds-panel").textContent).toContain("chain_kill");

  // Click-to-inspect opens the real seat modal
  fireEvent.click(screen.getByTestId("seat-card-VET"));
  await waitFor(() => expect(screen.getByTestId("seat-inspector-modal")).toBeTruthy());
  await waitFor(() => expect(screen.getByText(/Logical engine: security/i)).toBeTruthy());

  // Toggle Demo Playback shows explicit DEMO PLAYBACK MODE banner
  fireEvent.click(screen.getByText("✕ CLOSE"));
  fireEvent.click(screen.getByTestId("demo-playback-toggle"));
  await waitFor(() => expect(screen.getByTestId("demo-playback-banner")).toBeTruthy());
});

it("renders the Cost Monitor view with verified $0.00/mo spend, Supabase Free headroom, and honest integration matrix", async () => {
  sessionStorage.setItem("solswarm.token", "monitor-token-test");
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/api/summary")) return new Response(JSON.stringify(MOCK_SUMMARY), { status: 200 });
    if (url.includes("/api/cost-monitor")) {
      return new Response(
        JSON.stringify({
          checked_at: "2026-11-16T05:00:00Z",
          required_monthly_cost_usd: "0.00",
          optional_paid_spend_usd: "0.00",
          zero_additional_cost_verified: true,
          paper_trading_only: true,
          live_execution_enabled: false,
          supabase: {
            configured: false,
            mode: "local_sqlite_fallback",
            organization_audit: { org_slug: "jxyerpsdnnregvudaqda", plan: "free", inactivity_pause_days: 7, pause_mitigation: "Task Scheduler" },
            free_tier_limit_bytes: 524288000,
            free_tier_limit_mb: 500.0,
            free_tier_egress_limit_bytes: 5368709120,
            database_bytes: null,
            swarm_tables_bytes: 14200,
            usage_pct: 0.003,
            headroom_bytes: 524273800,
            headroom_mb: 499.99,
            row_counts: { swarm_agent_tasks: 4, swarm_agent_findings: 3, swarm_agent_heartbeats: 8, swarm_decisions: 1, swarm_dashboard_state: 1 },
            retention_policy: { task_ttl_hours: 24, finding_ttl_hours: 72, heartbeat_ttl_hours: 24, decision_ttl_days: 14, max_payload_bytes: 4096, max_tasks_rows: 500, max_findings_rows: 1000, max_decisions_rows: 1000 },
            last_sync_at: null,
            last_prune_at: "2026-11-16T05:00:00Z",
            last_error: null,
          },
          local_storage: {
            sqlite_path: "/workspace/data/solswarm.db",
            sqlite_bytes: 131072,
            wal_bytes: 4096,
            shm_bytes: 32768,
            total_local_bytes: 167936,
            historical_data_isolated_locally: true,
            historical_row_counts: { market_observations: 18, kline_bars: 12, agent_events: 24, paper_orders: 1, backtest_runs: 0 },
          },
          gmgn_rate_limits: {
            configured: true,
            is_public_test_key: false,
            limit: 5,
            remaining: 3,
            reset_unix: null,
            banned_until_unix: null,
            quote_endpoint_plan_limited: true,
            quote_fallback: "gmgn_amm_reserve_quote",
          },
          grok_bot: {
            mcp_enabled: true,
            mcp_endpoint: "/api/mcp",
            freshness_window_seconds: 180,
            fresh_seats: ["CHIEF", "SCAN", "VET", "SOCIAL"],
            fallback_mode: "deterministic_fallback",
          },
          decision_sources: { grok_bot: 1, deterministic_fallback: 2, optional_jev: 0 },
          integrations: [
            { id: "gmgn_readonly_api", name: "GMGN Official Read-Only OpenAPI", classification: "free_active", monthly_cost_usd: "0.00", required: false, missing_env: [], detail: "Read-only Exist Auth active ($0 Free key)." },
            { id: "optional_jev_typesafe", name: "Optional TypeSafe System One (JEV)", classification: "optional_paid_disabled", monthly_cost_usd: "0.00", required: false, missing_env: [], detail: "Disabled by default." },
            { id: "live_swaps_wallet_signing", name: "Live Swap Execution & Wallet Signing", classification: "permanently_disabled", monthly_cost_usd: "0.00", required: false, missing_env: [], detail: "Structurally blocked." },
          ],
        }),
        { status: 200 },
      );
    }
    return new Response(JSON.stringify({}), { status: 200 });
  });

  render(
    <MemoryRouter initialEntries={["/cost"]}>
      <App />
    </MemoryRouter>,
  );

  await waitFor(() => expect(screen.getByTestId("cost-monitor-view")).toBeTruthy());
  expect(screen.getByTestId("zero-cost-verified-pill").textContent).toContain("$0.00/MO VERIFIED");
  expect(screen.getByTestId("required-monthly-cost").textContent).toBe("$0.00");
  expect(screen.getByTestId("integration-row-gmgn_readonly_api")).toBeTruthy();
  expect(screen.getByTestId("integration-row-optional_jev_typesafe").textContent).toContain("optional_paid_disabled");
  expect(screen.getByTestId("integration-row-live_swaps_wallet_signing").textContent).toContain("permanently_disabled");
});
