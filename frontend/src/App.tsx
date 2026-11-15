import { FormEvent, ReactNode, useEffect, useMemo, useState } from "react";
import { NavLink, Route, Routes, useNavigate, useParams } from "react-router-dom";
import {
  Activity,
  Gauge,
  LineChart,
  Radar as RadarIcon,
  Settings as SettingsIcon,
  Shield,
  Wallet,
} from "lucide-react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  Agent,
  ApiError,
  Order,
  Position,
  Provider,
  Summary,
  SwarmEvent,
  Tailscale,
  TokenRow,
  api,
  clearToken,
  getToken,
  setToken,
} from "./api";
import { compact, money, pct, shortMint, signed, tone } from "./lib/format";

function Login() {
  const [value, setValue] = useState("");
  const [error, setError] = useState("");
  async function submit(event: FormEvent) {
    event.preventDefault();
    setToken(value);
    try {
      await api("/api/summary");
      window.location.assign("/");
    } catch (err) {
      clearToken();
      setError(err instanceof ApiError ? err.message : "Could not authenticate");
    }
  }
  return (
    <div className="login">
      <form className="panel" onSubmit={submit}>
        <h1>SOL SWARM</h1>
        <p className="muted">Paper trading only. No wallet, no signing, no live orders.</p>
        <label>
          Monitor or admin token
          <input value={value} onChange={(event) => setValue(event.target.value)} autoFocus style={{ width: "100%", marginTop: 6 }} />
        </label>
        {error && <p className="down">{error}</p>}
        <button className="action" type="submit">Enter terminal</button>
      </form>
    </div>
  );
}

function usePoll<T>(path: string, fallback: T): T {
  const [data, setData] = useState<T>(fallback);
  useEffect(() => {
    let stop = false;
    const load = () => api<T>(path).then((row) => { if (!stop) setData(row); }).catch(() => undefined);
    load();
    const timer = window.setInterval(load, 4000);
    const token = getToken();
    let socket: WebSocket | null = null;
    if (token) {
      const proto = window.location.protocol === "https:" ? "wss" : "ws";
      socket = new WebSocket(`${proto}://${window.location.host}/ws?token=${encodeURIComponent(token)}`);
      socket.onmessage = () => load();
    }
    return () => {
      stop = true;
      window.clearInterval(timer);
      socket?.close();
    };
  }, [path]);
  return data;
}

function Shell({ children }: { children: ReactNode }) {
  const summary = usePoll<Summary | null>("/api/summary", null);
  const [message, setMessage] = useState("");
  async function stop() {
    try {
      await api("/api/control/emergency-stop", { method: "POST", body: JSON.stringify({ reason: "operator pause from dashboard" }) });
      setMessage("Entries paused. Open paper positions were not liquidated.");
    } catch (err) {
      setMessage(err instanceof ApiError ? err.message : "Stop failed");
    }
  }
  async function resume() {
    try {
      await api("/api/control/resume", { method: "POST" });
      setMessage("New entries resumed. Existing positions were left unchanged.");
    } catch (err) {
      setMessage(err instanceof ApiError ? err.message : "Resume failed");
    }
  }
  return (
    <div className="app-shell">
      <aside className="nav">
        <div className="brand">
          <strong>SOL SWARM</strong>
          <span>Paper research desk</span>
        </div>
        <NavLink to="/" end><Gauge size={16} /> Overview</NavLink>
        <NavLink to="/radar"><RadarIcon size={16} /> Radar</NavLink>
        <NavLink to="/agents"><Activity size={16} /> Agents</NavLink>
        <NavLink to="/terminal"><Wallet size={16} /> Terminal</NavLink>
        <NavLink to="/analytics"><LineChart size={16} /> Analytics</NavLink>
        <NavLink to="/replay"><Shield size={16} /> Replay</NavLink>
        <NavLink to="/settings"><SettingsIcon size={16} /> Settings</NavLink>
      </aside>
      <div className="main">
        <header className="topbar">
          <div className="pills">
            <span className="pill bad">PAPER ONLY</span>
            <span className={summary?.mode === "live" ? "pill info" : "pill warn"}>
              {summary?.mode === "live" ? "LIVE DATA" : "DEMO FIXTURES"}
            </span>
            <span className={summary?.emergency_stop ? "pill bad" : "pill good"}>
              {summary?.emergency_stop ? "ENTRIES PAUSED" : "ENTRIES OPEN"}
            </span>
            <TailPill data={summary?.tailscale} />
          </div>
          <div className="pills">
            <span className="mono">{summary ? money(summary.equity) : "—"}</span>
            {summary?.emergency_stop ? (
              <button className="action" onClick={resume}>Resume entries</button>
            ) : (
              <button className="danger" onClick={stop}>Emergency stop</button>
            )}
            <button className="ghost" onClick={() => { clearToken(); window.location.assign("/"); }}>Lock</button>
          </div>
        </header>
        {message && <div className="content" style={{ paddingBottom: 0 }}><div className="panel">{message}</div></div>}
        <div className="content">{children}</div>
      </div>
    </div>
  );
}

function TailPill({ data }: { data?: Tailscale }) {
  if (!data) return <span className="pill">Tailscale …</span>;
  if (data.funnel_enabled) return <span className="pill bad">Funnel is on</span>;
  if (!data.installed) return <span className="pill">Tailscale not installed</span>;
  if (!data.connected) return <span className="pill warn">Tailscale offline</span>;
  return <span className="pill good">{data.serve_enabled ? "Serve on tailnet" : "Tailscale connected"}</span>;
}

function Overview() {
  const summary = usePoll<Summary | null>("/api/summary", null);
  const providers = usePoll<Provider[]>("/api/providers", []);
  if (!summary) return <p className="muted">Loading book…</p>;
  return (
    <>
      <section className="grid">
        <Metric label="Equity" value={money(summary.equity)} />
        <Metric label="Daily P&L" value={summary.daily_pnl_available ? signed(summary.daily_pnl) : "—"} tone={tone(summary.daily_pnl)} />
        <Metric label="Total P&L" value={summary.total_pnl_available ? signed(summary.total_pnl) : "—"} tone={tone(summary.total_pnl)} />
        <Metric label="Win rate" value={summary.win_rate_available ? pct(Number(summary.win_rate) * 100) : "—"} hint={summary.win_rate_available ? "" : "No closed trades"} />
        <Metric label="Profit factor" value={summary.profit_factor_available ? Number(summary.profit_factor).toFixed(2) : "—"} hint={summary.profit_factor_available ? "" : "Needs wins and losses"} />
        <Metric label="Open exposure" value={money(summary.open_exposure)} />
        <Metric label="Max drawdown" value={summary.max_drawdown_available ? pct(Number(summary.max_drawdown) * 100) : "—"} />
        <Metric label="Agents" value={String(summary.active_agents)} hint={`${summary.alerts_open} open alerts`} />
      </section>
      <section className="split">
        <div className="panel">
          <h2>Market data</h2>
          <p className="muted">{summary.data_label}. Last scan {summary.last_scan_at || "not yet"}.</p>
          <p className="muted">AI commentary: {summary.ai_commentary}. Deterministic notes are not labeled as Grok.</p>
          {providers.map((row) => (
            <div key={row.provider} className="event">
              <strong>{row.provider}</strong>
              <span className={row.status === "ok" ? "up" : "muted"}>{row.configured ? row.status : "unconfigured"}</span>
              <span className="muted">{row.last_error || row.circuit}</span>
            </div>
          ))}
        </div>
        <div className="panel">
          <h2>Tailscale</h2>
          <TailscaleCard data={summary.tailscale} />
        </div>
      </section>
    </>
  );
}

function Metric({ label, value, hint, tone: color }: { label: string; value: string; hint?: string; tone?: "up" | "down" | "flat" }) {
  return (
    <article className="card">
      <div className="label">{label}</div>
      <div className={`value ${color === "up" ? "up" : color === "down" ? "down" : ""}`}>{value}</div>
      {hint && <div className="muted">{hint}</div>}
    </article>
  );
}

function TailscaleCard({ data }: { data: Tailscale }) {
  return (
    <>
      <p>{data.detail}</p>
      <p className="mono muted">{data.dns_name || "no MagicDNS name"}</p>
      <p className="muted">Serve {data.serve_enabled ? "enabled" : "off"} · Funnel {data.funnel_enabled ? "ON" : "off"}</p>
    </>
  );
}

function Radar() {
  const rows = usePoll<TokenRow[]>("/api/tokens", []);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [sort, setSort] = useState("score");
  const navigate = useNavigate();
  const visible = useMemo(() => {
    const filtered = rows.filter((row) => {
      const haystack = `${row.symbol} ${row.name} ${row.mint}`.toLowerCase();
      if (query && !haystack.includes(query.toLowerCase())) return false;
      if (status !== "all" && row.security_status !== status && row.candidate_status !== status) return false;
      return true;
    });
    return filtered.sort((a, b) => {
      if (sort === "liquidity") return Number(b.liquidity || 0) - Number(a.liquidity || 0);
      if (sort === "change") return Number(b.price_change_m5 || 0) - Number(a.price_change_m5 || 0);
      return Number(b.discovery_score || 0) - Number(a.discovery_score || 0);
    });
  }, [rows, query, status, sort]);
  return (
    <div className="panel">
      <div className="toolbar">
        <input placeholder="Search symbol or mint" value={query} onChange={(event) => setQuery(event.target.value)} />
        <select value={status} onChange={(event) => setStatus(event.target.value)}>
          <option value="all">All statuses</option>
          <option value="pass">Security pass</option>
          <option value="fail">Security fail</option>
          <option value="unknown">Security unknown</option>
          <option value="hold">Held</option>
          <option value="approved">Approved</option>
        </select>
        <select value={sort} onChange={(event) => setSort(event.target.value)}>
          <option value="score">Discovery score</option>
          <option value="liquidity">Liquidity</option>
          <option value="change">5m change</option>
        </select>
      </div>
      <table>
        <thead>
          <tr>
            <th>Token</th><th>Mint</th><th>Liquidity</th><th>Mkt cap</th><th>Vol 5m</th><th>Change</th><th>Momentum</th><th>Security</th><th>Status</th>
          </tr>
        </thead>
        <tbody>
          {visible.map((row) => (
            <tr key={row.mint} onClick={() => navigate(`/token/${encodeURIComponent(row.mint)}`)}>
              <td>{row.symbol}<div className="muted">{row.name}{row.promoted ? " · promoted" : ""}</div></td>
              <td className="mono">{shortMint(row.mint)}</td>
              <td>{compact(row.liquidity)}</td>
              <td>{compact(row.market_cap)}</td>
              <td>{compact(row.volume_m5)}</td>
              <td className={tone(row.price_change_m5)}>{row.price_change_m5 == null ? "—" : `${row.price_change_m5}%`}</td>
              <td>{row.momentum_score ?? "—"}</td>
              <td className={row.security_status === "pass" ? "up" : row.security_status === "fail" ? "down" : "muted"}>{row.security_status}</td>
              <td>{row.candidate_status}<div className="muted">{row.data_label}</div></td>
            </tr>
          ))}
        </tbody>
      </table>
      {visible.length === 0 && <p className="muted">No tokens in this filter.</p>}
    </div>
  );
}

function Agents() {
  const agents = usePoll<Agent[]>("/api/agents", []);
  const events = usePoll<SwarmEvent[]>("/api/events", []);
  return (
    <>
      <div className="agents">
        {agents.map((agent) => (
          <article className="card" key={agent.name}>
            <div className="label">{agent.name}</div>
            <div className={agent.status === "error" ? "down" : "up"}>{agent.status}</div>
            <p className="muted">{agent.last_task || "waiting"}</p>
            <p className="muted">{agent.processed} processed · {agent.errors} errors · {agent.last_duration_ms ?? "—"} ms</p>
            <p>{agent.last_decision || agent.last_error || "—"}</p>
          </article>
        ))}
      </div>
      <div className="panel">
        <h2>Event timeline</h2>
        <div className="timeline">
          {events.map((event) => (
            <div className="event" key={event.id}>
              <span className="mono muted">{event.at.slice(11, 19)} · {event.sequence}</span>
              <span style={{ color: "var(--purple)" }}>{event.source}</span>
              <span>{event.type} {event.mint ? `· ${event.mint}` : ""}</span>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}

function Terminal() {
  const positions = usePoll<Position[]>("/api/positions", []);
  const orders = usePoll<Order[]>("/api/orders", []);
  return (
    <div className="split">
      <div className="panel">
        <h2>Paper positions</h2>
        {positions.length === 0 && <p className="muted">No positions in this mode.</p>}
        {positions.map((row) => (
          <article key={row.id} style={{ marginBottom: 12 }}>
            <strong>{row.symbol}</strong> <span className="muted">{row.status}</span>
            <p className="mono">{row.mint}</p>
            <p>Entry {money(row.entry_price)} · Mark {row.unrealized_available ? money(row.mark_price) : "unavailable"}</p>
            <p className={tone(row.unrealized_pnl)}>Unrealized {row.unrealized_available ? signed(row.unrealized_pnl) : "—"}</p>
            <p className="muted">Realized {signed(row.realized_pnl)} · Fees {money(row.fees)} · Size {row.qty}</p>
            <p className="muted">Exits: adverse {row.exit_conditions?.adverse_price_pct}% · data {row.data_label}</p>
            {row.unsellable_reason && <p className="down">{row.unsellable_reason}</p>}
          </article>
        ))}
      </div>
      <div className="panel">
        <h2>Orders</h2>
        {orders.map((order) => (
          <article key={order.id} style={{ marginBottom: 12 }}>
            <strong>{order.side}</strong> {order.status}
            <p className="mono muted">{shortMint(order.mint)} · {order.quote?.source || "no quote"}</p>
            <p>Requested {money(order.requested_notional)} · Filled {money(order.filled_notional)} · Left {money(order.remaining_notional)}</p>
            {order.reason && <p className="down">{order.reason}</p>}
            {order.fills.map((fill, index) => (
              <p key={index} className="muted">Fill {money(fill.price)} · fee {money(fill.fee)} ({fill.fee_source}) · impact {fill.price_impact_bps || "—"} bps</p>
            ))}
          </article>
        ))}
      </div>
    </div>
  );
}

function Analytics() {
  const data = usePoll<{
    equity_curve: { at: string; equity: string; drawdown: string }[];
    equity_curve_available: boolean;
    win_rate: string | null;
    win_rate_available: boolean;
    profit_factor: string | null;
    profit_factor_available: boolean;
    average_gain: string | null;
    average_gain_available: boolean;
    average_loss: string | null;
    average_loss_available: boolean;
    fee_impact: string | null;
    fee_impact_available: boolean;
    slippage_bps: (string | null)[];
    rejected: { mint: string | null; reasons: string[]; at: string }[];
    data_label: string;
    closed_trades: number;
  } | null>("/api/analytics", null);
  if (!data) return <p className="muted">Loading analytics…</p>;
  const curve = data.equity_curve.map((row) => ({ time: row.at.slice(11, 19), equity: Number(row.equity), drawdown: Number(row.drawdown) * 100 }));
  const slips = data.slippage_bps.filter((row): row is string => Boolean(row)).map((row, index) => ({ index, bps: Number(row) }));
  return (
    <>
      <p className="muted">{data.data_label}. Metrics stay blank when there is no observation.</p>
      <div className="grid">
        <Metric label="Win rate" value={data.win_rate_available ? pct(Number(data.win_rate) * 100) : "—"} />
        <Metric label="Profit factor" value={data.profit_factor_available ? Number(data.profit_factor).toFixed(2) : "—"} />
        <Metric label="Avg gain" value={data.average_gain_available ? signed(data.average_gain) : "—"} />
        <Metric label="Avg loss" value={data.average_loss_available ? signed(data.average_loss) : "—"} />
        <Metric label="Fees" value={data.fee_impact_available ? money(data.fee_impact) : "—"} />
        <Metric label="Closed trades" value={String(data.closed_trades)} />
      </div>
      <div className="panel" style={{ height: 280 }}>
        <h2>Equity</h2>
        {data.equity_curve_available ? (
          <ResponsiveContainer width="100%" height={210}>
            <AreaChart data={curve}>
              <XAxis dataKey="time" stroke="#8d99ab" />
              <YAxis stroke="#8d99ab" domain={["auto", "auto"]} />
              <Tooltip />
              <Area dataKey="equity" stroke="#67e8f9" fill="rgba(103,232,249,0.15)" />
            </AreaChart>
          </ResponsiveContainer>
        ) : <p className="muted">No equity snapshots yet.</p>}
      </div>
      <div className="split">
        <div className="panel" style={{ height: 240 }}>
          <h2>Quoted price impact</h2>
          {slips.length ? (
            <ResponsiveContainer width="100%" height={170}>
              <BarChart data={slips}><XAxis dataKey="index" stroke="#8d99ab" /><YAxis stroke="#8d99ab" /><Bar dataKey="bps" fill="#c4b5fd" /></BarChart>
            </ResponsiveContainer>
          ) : <p className="muted">No fills yet.</p>}
        </div>
        <div className="panel">
          <h2>Rejected entries</h2>
          {data.rejected.length === 0 && <p className="muted">No risk rejections recorded.</p>}
          {data.rejected.slice(0, 8).map((row) => (
            <p key={row.at + (row.mint || "")} className="muted">{row.mint}: {row.reasons.join(", ")}</p>
          ))}
        </div>
      </div>
    </>
  );
}

function TokenPage() {
  const params = useParams();
  const mint = params["*"] || "";
  const [detail, setDetail] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api<Record<string, unknown>>(`/api/tokens/${encodeURIComponent(mint)}`).then(setDetail).catch((err: unknown) => {
      setError(err instanceof Error ? err.message : "missing");
    });
  }, [mint]);
  if (error) return <p className="down">{error}</p>;
  if (!detail) return <p className="muted">Loading token…</p>;
  const market = detail.market as Record<string, string | null>;
  const chart = (detail.chart as { at: string; price: string; provider: string }[]) || [];
  const security = (detail.security as { classification: string; explanation: string; critical_flags: string[]; warning_flags: string[] }[]) || [];
  const proposals = (detail.proposals as { thesis: string; status: string; strategy: string }[]) || [];
  const decisions = (detail.decisions as { approved: boolean; reason_codes: string[]; explanation: string }[]) || [];
  return (
    <div className="split">
      <div className="panel">
        <h2>{String(detail.symbol)} · {String(detail.name)}</h2>
        <p className="mono">{String(detail.mint)}</p>
        <p className="muted">Data label: {String(detail.data_label)}. Promoted listings are not treated as demand.</p>
        <p>Price {money(market.price)} · Pool liquidity {compact(market.liquidity)} · FDV {compact(market.fdv)}</p>
        <p className="muted">Observed {market.observed_at || "—"} · Provider time {market.provider_reported_at || "not supplied"}</p>
        <div style={{ height: 220 }}>
          {chart.length ? (
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={chart.map((row) => ({ time: row.at.slice(11, 16), price: Number(row.price) }))}>
                <XAxis dataKey="time" stroke="#8d99ab" /><YAxis stroke="#8d99ab" domain={["auto", "auto"]} /><Tooltip />
                <Area dataKey="price" stroke="#3dd68c" fill="rgba(61,214,140,0.12)" />
              </AreaChart>
            </ResponsiveContainer>
          ) : <p>No chart points.</p>}
        </div>
      </div>
      <div className="panel">
        <h2>Decision</h2>
        {security[0] && <p className={security[0].classification === "pass" ? "up" : "down"}>{security[0].classification}: {security[0].explanation}</p>}
        {security[0]?.critical_flags?.length > 0 && <p className="down">{security[0].critical_flags.join(", ")}</p>}
        {proposals[0] && <p>{proposals[0].status} · {proposals[0].strategy}<br />{proposals[0].thesis}</p>}
        {decisions[0] && <p>{decisions[0].approved ? "Risk approved" : "Risk veto"}: {decisions[0].explanation}</p>}
      </div>
    </div>
  );
}

function Replay() {
  const [speed, setSpeed] = useState(4);
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState("");
  const runs = usePoll<{ id: string; disclosure: { text?: string } }[]>("/api/backtests", []);
  async function start() {
    setError("");
    try {
      const body = await api<{ results: Record<string, unknown>; disclosure: { text: string } }>("/api/backtests", {
        method: "POST",
        body: JSON.stringify({ speed, notional: "20" }),
      });
      setResult(body.results);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Replay failed");
    }
  }
  const curve = ((result?.equity_curve as { ts: string; equity: string }[]) || []).map((row) => ({
    time: row.ts.slice(11, 16),
    equity: Number(row.equity),
  }));
  return (
    <div className="panel">
      <h2>Historical replay</h2>
      <p className="muted">Demo bars are fixtures. Decisions cannot see later bars. Missing point-in-time security is not treated as a pass. Replay does not enter the paper book.</p>
      <div className="toolbar">
        <label>Playback speed <input type="number" min={1} max={20} value={speed} onChange={(event) => setSpeed(Number(event.target.value))} /></label>
        <button className="action" onClick={start}>Run demo replay</button>
      </div>
      {error && <p className="down">{error}</p>}
      {result && <p>{String(result.disclosure)}</p>}
      {curve.length > 0 && (
        <div style={{ height: 220 }}>
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={curve}><XAxis dataKey="time" stroke="#8d99ab" /><YAxis stroke="#8d99ab" /><Tooltip /><Area dataKey="equity" stroke="#c4b5fd" fill="rgba(196,181,253,0.15)" /></AreaChart>
          </ResponsiveContainer>
        </div>
      )}
      <p className="muted">{runs.length} stored runs.</p>
    </div>
  );
}

function SettingsPage() {
  const current = usePoll<Record<string, unknown> | null>("/api/settings", null);
  const [note, setNote] = useState("");
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const patch = {
      scanner: { interval_seconds: Number(form.get("interval")), max_candidates: Number(form.get("candidates")) },
      strategy: { momentum_threshold: Number(form.get("momentum")) },
      risk: {
        max_new_trade_usd: String(form.get("trade")),
        min_pool_liquidity_usd: String(form.get("liquidity")),
        min_pool_age_minutes: Number(form.get("age")),
        daily_loss_pct: String(form.get("daily")),
        drawdown_pct: String(form.get("drawdown")),
      },
      simulation: { network_fee_usdc: String(form.get("fee")) },
    };
    try {
      await api("/api/settings", { method: "PUT", body: JSON.stringify(patch) });
      setNote("Saved. The change is versioned. Risk limits stay independent of any model prompt.");
    } catch (err) {
      setNote(err instanceof ApiError ? err.message : "Save failed");
    }
  }
  async function mode(next: string) {
    try {
      await api("/api/control/mode", { method: "POST", body: JSON.stringify({ mode: next }) });
      setNote(`Mode is ${next}. Demo trades stay out of the other book.`);
    } catch (err) {
      setNote(err instanceof ApiError ? err.message : "Mode change failed");
    }
  }
  const risk = (current?.risk as Record<string, string | number>) || {};
  const scanner = (current?.scanner as Record<string, number>) || {};
  const strategy = (current?.strategy as Record<string, number>) || {};
  const simulation = (current?.simulation as Record<string, string>) || {};
  const providers = (current?.providers as Record<string, boolean>) || {};
  return (
    <form className="panel" key={current ? "loaded" : "empty"} onSubmit={save}>
      <h2>Paper settings</h2>
      <p className="muted">Mode {String(current?.mode || "…")}. Jupiter configured: {String(providers.jupiter_configured)}. Grok configured: {String(providers.xai_configured)}. Keys are never shown.</p>
      <div className="toolbar">
        <button type="button" className="ghost" onClick={() => mode("demo")}>Demo mode</button>
        <button type="button" className="ghost" onClick={() => mode("live")}>Live data mode</button>
      </div>
      <div className="grid">
        <label>Scan seconds<input name="interval" defaultValue={scanner.interval_seconds ?? 60} /></label>
        <label>Max candidates<input name="candidates" defaultValue={scanner.max_candidates ?? 8} /></label>
        <label>Momentum threshold<input name="momentum" defaultValue={strategy.momentum_threshold ?? 65} /></label>
        <label>Max trade USD<input name="trade" defaultValue={risk.max_new_trade_usd ?? "20"} /></label>
        <label>Min liquidity<input name="liquidity" defaultValue={risk.min_pool_liquidity_usd ?? "50000"} /></label>
        <label>Min pool age min<input name="age" defaultValue={risk.min_pool_age_minutes ?? 15} /></label>
        <label>Daily loss fraction<input name="daily" defaultValue={risk.daily_loss_pct ?? "0.03"} /></label>
        <label>Drawdown fraction<input name="drawdown" defaultValue={risk.drawdown_pct ?? "0.10"} /></label>
        <label>Assumed network fee<input name="fee" defaultValue={simulation.network_fee_usdc ?? "0.02"} /></label>
      </div>
      <button className="action" type="submit">Save settings</button>
      {note && <p>{note}</p>}
    </form>
  );
}

export default function App() {
  if (!getToken()) return <Login />;
  return (
    <Shell>
      <Routes>
        <Route path="/" element={<Overview />} />
        <Route path="/radar" element={<Radar />} />
        <Route path="/agents" element={<Agents />} />
        <Route path="/terminal" element={<Terminal />} />
        <Route path="/analytics" element={<Analytics />} />
        <Route path="/replay" element={<Replay />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/token/*" element={<TokenPage />} />
      </Routes>
    </Shell>
  );
}
