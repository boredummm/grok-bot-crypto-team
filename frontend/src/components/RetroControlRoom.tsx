import { FormEvent, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ApiError,
  CandlePoint,
  DemoPlaybackData,
  DemoPlaybackStep,
  DeskState,
  FeaturedToken,
  Provider,
  SeatCard,
  SeatInspection,
  SeatName,
  Summary,
  api,
  openEventStream,
} from "../api";
import { compact, money, pct, shortMint, signed, tone } from "../lib/format";
import { GmgnEmblem, SEAT_COLORS, SeatSprite } from "./PixelSprites";
import { TradingFloor } from "./TradingFloor";

const PIPELINE_STAGES = ["free_kill", "trade_kill", "chain_kill", "soft_kill", "pick"] as const;

export function RetroControlRoom({
  summary,
  providers,
}: {
  summary: Summary | null;
  providers: Provider[];
}) {
  const navigate = useNavigate();
  const [desk, setDesk] = useState<DeskState | null>(null);
  const [viewMode, setViewMode] = useState<"office" | "classic">("office");
  const [reducedMotion, setReducedMotion] = useState<boolean>(() =>
    typeof window !== "undefined" && typeof window.matchMedia === "function"
      ? window.matchMedia("(prefers-reduced-motion: reduce)").matches
      : false,
  );

  // Explicit Demo Playback storyboard state (never modifies paper book)
  const [playbackOn, setPlaybackOn] = useState(false);
  const [playbackData, setPlaybackData] = useState<DemoPlaybackData | null>(null);
  const [playbackIdx, setPlaybackIdx] = useState(0);

  // Selected token override on the big screen & JEV · ANALYSIS
  const [selectedMint, setSelectedMint] = useState<string | null>(null);
  const [customCandles, setCustomCandles] = useState<CandlePoint[] | null>(null);

  // Click-to-inspect seat drawer
  const [inspectedSeat, setInspectedSeat] = useState<SeatName | null>(null);
  const [inspection, setInspection] = useState<SeatInspection | null>(null);
  const [inspectLoading, setInspectLoading] = useState(false);

  // Hover preview for crawler web
  const [hoverCrawler, setHoverCrawler] = useState(false);

  // thresholds.py controls
  const [activeStage, setActiveStage] = useState<string>("pick");
  const [showGuideRef, setShowGuideRef] = useState(false);
  const [editingThresholds, setEditingThresholds] = useState(false);
  const [thresholdNotice, setThresholdNotice] = useState("");

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      api<DeskState>("/api/desk")
        .then((d) => {
          if (!cancelled) setDesk(d);
        })
        .catch(() => undefined);
    };
    load();
    const poll = window.setInterval(load, 4000);
    const closeSse = openEventStream(load);
    return () => {
      cancelled = true;
      window.clearInterval(poll);
      closeSse();
    };
  }, []);

  useEffect(() => {
    if (!playbackOn) return;
    let active = true;
    if (!playbackData) {
      api<DemoPlaybackData>("/api/desk/demo-playback")
        .then((d) => {
          if (active) {
            setPlaybackData(d);
            setPlaybackIdx(0);
          }
        })
        .catch(() => undefined);
    }
    const timer = window.setInterval(() => {
      setPlaybackIdx((prev) => {
        const len = playbackData?.steps.length || 7;
        return (prev + 1) % len;
      });
    }, reducedMotion ? 3500 : 2200);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [playbackOn, playbackData, reducedMotion]);

  useEffect(() => {
    if (!inspectedSeat) {
      setInspection(null);
      return;
    }
    setInspectLoading(true);
    api<SeatInspection>(`/api/desk/seats/${inspectedSeat}`)
      .then((res) => setInspection(res))
      .catch(() => setInspection(null))
      .finally(() => setInspectLoading(false));
  }, [inspectedSeat]);

  useEffect(() => {
    if (!selectedMint || !desk || selectedMint === desk.featured_token?.mint) {
      setCustomCandles(null);
      return;
    }
    api<{ candles?: CandlePoint[] }>(`/api/tokens/${encodeURIComponent(selectedMint)}`)
      .then((detail) => setCustomCandles(detail.candles || []))
      .catch(() => setCustomCandles([]));
  }, [selectedMint, desk]);

  const currentPlaybackStep: DemoPlaybackStep | null = useMemo(() => {
    if (!playbackOn || !playbackData?.steps.length) return null;
    return playbackData.steps[playbackIdx % playbackData.steps.length];
  }, [playbackOn, playbackData, playbackIdx]);

  const activeToken: FeaturedToken | null = useMemo(() => {
    if (!desk) return null;
    if (!selectedMint || selectedMint === desk.featured_token?.mint) {
      return desk.featured_token;
    }
    const found = desk.candidates.find((c) => c.mint === selectedMint);
    if (!found) return desk.featured_token;
    return {
      ...found,
      candles: customCandles || [],
      checks: desk.featured_token?.checks || [],
    };
  }, [desk, selectedMint, customCandles]);

  async function saveThresholds(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!desk) return;
    const fd = new FormData(event.currentTarget);
    const hardPatch = {
      min_age_minutes: Number(fd.get("min_age_minutes")),
      min_liquidity_usd: Number(fd.get("min_liquidity_usd")),
      min_volume_h24: Number(fd.get("min_volume_h24")),
      min_mcap_usd: Number(fd.get("min_mcap_usd")),
      min_trades_h24: Number(fd.get("min_trades_h24")),
      max_top_wallet: Number(fd.get("max_top_wallet")),
      max_top_10: Number(fd.get("max_top_10")),
    };
    try {
      await api("/api/thresholds", {
        method: "PUT",
        body: JSON.stringify({
          hard: hardPatch,
          shadow_mode: fd.get("shadow_mode") === "on",
          one_position_at_a_time: fd.get("one_position_at_a_time") === "on",
          use_bench: fd.get("use_bench") === "on",
        }),
      });
      const updated = await api<DeskState>("/api/desk");
      setDesk(updated);
      setEditingThresholds(false);
      setThresholdNotice("Saved thresholds.py (versioned). RiskEngine retains final veto.");
    } catch (err) {
      setThresholdNotice(err instanceof ApiError ? err.message : "Save failed (admin token required)");
    }
  }

  async function triggerScan() {
    setThresholdNotice("");
    try {
      await api("/api/control/cycle", { method: "POST" });
      const updated = await api<DeskState>("/api/desk");
      setDesk(updated);
    } catch (err) {
      setThresholdNotice(err instanceof ApiError ? err.message : "Cycle trigger requires admin token");
    }
  }

  if (!desk) {
    return <p className="muted">Booting SOL SWARM + JEV control room…</p>;
  }

  if (viewMode === "classic" && summary) {
    return (
      <div className="classic-overview-wrap">
        <div className="toolbar" style={{ justifyContent: "space-between" }}>
          <span className="muted">Classic Overview · Paper safety active</span>
          <button type="button" className="action" onClick={() => setViewMode("office")}>
            Return to 2D Control Room
          </button>
        </div>
        <section className="grid">
          <ClassicMetric label="Equity" value={money(summary.equity)} />
          <ClassicMetric label="Daily P&L" value={summary.daily_pnl_available ? signed(summary.daily_pnl) : "—"} tone={tone(summary.daily_pnl)} />
          <ClassicMetric label="Total P&L" value={summary.total_pnl_available ? signed(summary.total_pnl) : "—"} tone={tone(summary.total_pnl)} />
          <ClassicMetric label="Win rate" value={summary.win_rate_available ? pct(Number(summary.win_rate) * 100) : "—"} hint={summary.win_rate_available ? "" : "No closed trades"} />
          <ClassicMetric label="Profit factor" value={summary.profit_factor_available ? Number(summary.profit_factor).toFixed(2) : "—"} hint={summary.profit_factor_available ? "" : "Needs wins and losses"} />
          <ClassicMetric label="Open exposure" value={money(summary.open_exposure)} />
          <ClassicMetric label="Max drawdown" value={summary.max_drawdown_available ? pct(Number(summary.max_drawdown) * 100) : "—"} />
          <ClassicMetric label="Agents" value={String(summary.active_agents)} hint={`${summary.alerts_open} open alerts`} />
        </section>
        <section className="split" style={{ marginTop: 14 }}>
          <div className="panel">
            <h2>Providers &amp; AI Status</h2>
            <p className="muted">{summary.data_label}. JEV: {desk.jev.status_label}. GMGN: {desk.gmgn.venue_label}.</p>
            {providers.map((row) => (
              <div key={row.provider} className="event">
                <strong>{row.provider}</strong>
                <span className={row.status === "ok" ? "up" : "muted"}>{row.configured ? row.status : "unconfigured"}</span>
                <span className="muted">{row.last_error || row.detail || row.circuit}</span>
              </div>
            ))}
          </div>
          <div className="panel">
            <h2>Tailscale Private Serve</h2>
            <p>{summary.tailscale.detail}</p>
            <p className="mono muted">{summary.tailscale.dns_name || "no MagicDNS name"}</p>
            <p className="muted">Serve {summary.tailscale.serve_enabled ? "enabled" : "off"} · Funnel {summary.tailscale.funnel_enabled ? "ON" : "off"}</p>
          </div>
        </section>
      </div>
    );
  }

  const curve = desk.balance.curve;
  const eqValues = curve.map((c) => c.equity);
  const minEq = eqValues.length ? Math.min(...eqValues, Number(desk.balance.equity || 1000) - 0.1) : 999.8;
  const maxEq = eqValues.length ? Math.max(...eqValues, Number(desk.balance.equity || 1000) + 0.1) : 1000.1;
  const spanEq = Math.max(maxEq - minEq, 0.05);

  const displayStage = currentPlaybackStep ? currentPlaybackStep.funnel_stage : activeStage;

  return (
    <div className="retro-board" data-testid="retro-control-room">
      {/* 1. HOT PINK / MAGENTA GRAPH-PAPER HEADER */}
      <header className="retro-pink-header">
        <div className="retro-header-left">
          <SeatSprite seat="CHIEF" size={28} />
          <span className="retro-title">SOL SWARM + JEV</span>
          <span className="retro-chip-icon" aria-hidden="true">❖</span>
          <span className="retro-subtitle">JEV PICKS, SWARM TRADES · GMGN PAPER</span>
        </div>

        <div className="retro-header-controls">
          <span
            className={`retro-badge ${desk.jev.connected ? "ok" : "warn"}`}
            data-testid="jev-status-badge"
          >
            {desk.jev.status_label}
          </span>
          <span className="retro-badge gmgn" data-testid="gmgn-venue-badge">
            GMGN PAPER ONLY
          </span>
          <button
            type="button"
            className={`retro-btn ${playbackOn ? "active-demo" : ""}`}
            onClick={() => setPlaybackOn((v) => !v)}
            data-testid="demo-playback-toggle"
          >
            {playbackOn ? "■ STOP DEMO PLAYBACK" : "▶ DEMO PLAYBACK"}
          </button>
          <button
            type="button"
            className="retro-btn"
            onClick={() => setReducedMotion((v) => !v)}
            title="Toggle reduced motion"
          >
            {reducedMotion ? "MOTION: REDUCED" : "MOTION: ON"}
          </button>
          <button
            type="button"
            className="retro-btn"
            onClick={triggerScan}
            title="Run one scan cycle (admin)"
          >
            ↻ SCAN
          </button>
          <button
            type="button"
            className="retro-btn"
            onClick={() => setViewMode("classic")}
          >
            CLASSIC
          </button>
        </div>

        <div className="retro-header-right">
          <span className="retro-day-label">{desk.day_label}</span>
          <span className="retro-clock-box">
            {currentPlaybackStep ? currentPlaybackStep.clock : desk.clock_utc}
          </span>
        </div>
      </header>

      {/* Explicit Demo Playback Banner when active */}
      {playbackOn && (
        <div className="retro-playback-banner" data-testid="demo-playback-banner">
          <strong>DEMO PLAYBACK MODE</strong> — Showing pre-recorded deterministic choreography step {playbackIdx + 1}/7 ({currentPlaybackStep?.active_seat}: “{currentPlaybackStep?.bubble}”). Paper balances remain untouched.
        </div>
      )}

      {/* 2. TOP 2x4 BLACK AGENT STATUS GRID */}
      <section className="retro-seat-grid" aria-label="Eight desk agent status panels">
        {desk.seats.map((card) => (
          <SeatPanel
            key={card.seat}
            card={card}
            highlighted={currentPlaybackStep?.active_seat === card.seat}
            onClick={() => setInspectedSeat(card.seat)}
            onHover={
              card.seat === "CRAWLER" || card.seat === "SCAN"
                ? setHoverCrawler
                : undefined
            }
          />
        ))}
      </section>

      {/* 3. CENTRAL TOP-DOWN 2D PIXEL-ART TRADING FLOOR */}
      <TradingFloor
        desk={desk}
        selectedToken={activeToken}
        onSelectSeat={(seat) => setInspectedSeat(seat)}
        onSelectToken={(mint) => setSelectedMint(mint)}
        playbackStep={currentPlaybackStep}
        reducedMotion={reducedMotion}
        showWebOverride={hoverCrawler}
      />

      {/* 4. WIDE BALANCE / REAL PAPER EQUITY PANEL */}
      <section className="retro-balance-panel" data-testid="balance-panel">
        <div className="retro-balance-header">
          <span>
            BALANCE · {desk.day_label.split("·")[0].trim()} · {desk.balance.held_symbol} ·{" "}
            <em className="retro-tag-inline">{desk.data_label}</em>
          </span>
          <span>
            {desk.balance.wins}W {desk.balance.losses}L · EXPOSURE {money(desk.balance.open_exposure)} · MAX DD{" "}
            {desk.balance.max_drawdown ? `${(Number(desk.balance.max_drawdown) * 100).toFixed(2)}%` : "0.00%"}
          </span>
        </div>
        <div className="retro-balance-body">
          <div className="retro-balance-chart">
            <svg viewBox="0 0 820 135" className="retro-eq-svg" preserveAspectRatio="none">
              <defs>
                <linearGradient id="eqGlow" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#00e676" stopOpacity="0.28" />
                  <stop offset="100%" stopColor="#00e676" stopOpacity="0.01" />
                </linearGradient>
              </defs>
              {/* Y-axis reference labels */}
              <text x="8" y="22" fill="#52637a" fontSize="11" fontFamily="IBM Plex Mono, monospace">
                ${maxEq.toFixed(2)}
              </text>
              <text x="8" y="68" fill="#52637a" fontSize="11" fontFamily="IBM Plex Mono, monospace">
                ${((maxEq + minEq) / 2).toFixed(2)}
              </text>
              <text x="8" y="112" fill="#52637a" fontSize="11" fontFamily="IBM Plex Mono, monospace">
                ${minEq.toFixed(2)}
              </text>
              <line x1="78" y1="20" x2="804" y2="20" stroke="#131c2b" strokeWidth="1" />
              <line x1="78" y1="65" x2="804" y2="65" stroke="#131c2b" strokeWidth="1" />
              <line x1="78" y1="110" x2="804" y2="110" stroke="#131c2b" strokeWidth="1" />

              {curve.length > 0 && (() => {
                const pts = curve.map((pt, idx) => {
                  const x = 84 + (idx / Math.max(curve.length - 1, 1)) * 712;
                  const y = 18 + (1 - (pt.equity - minEq) / spanEq) * 86;
                  return { x, y, delta: pt.delta };
                });
                const linePoints = pts.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
                const areaPoints = `84,114 ${linePoints} ${pts[pts.length - 1].x.toFixed(1)},114`;
                const last = pts[pts.length - 1];
                return (
                  <>
                    <polygon points={areaPoints} fill="url(#eqGlow)" />
                    <polyline points={linePoints} fill="none" stroke="#00e676" strokeWidth="2.4" />
                    <circle cx={last.x} cy={last.y} r="4.5" fill="#00e676" stroke="#052e16" strokeWidth="2" />
                    {pts.map((p, idx) => {
                      const barH = Math.min(18, Math.max(3, Math.abs(p.delta) * 120 + 4));
                      return (
                        <rect
                          key={idx}
                          x={p.x - 3}
                          y={132 - barH}
                          width="5"
                          height={barH}
                          fill={p.delta < 0 ? "#ff384c" : "#00e676"}
                          opacity="0.72"
                        />
                      );
                    })}
                  </>
                );
              })()}
            </svg>
          </div>
          <div className="retro-balance-readout">
            <span className="retro-balance-caption">PAPER BALANCE</span>
            <strong className="retro-balance-figure" data-testid="paper-balance-value">
              {money(desk.balance.equity)}
            </strong>
            <span className={`retro-balance-sub ${tone(desk.balance.total_pnl)}`}>
              P&amp;L {signed(desk.balance.total_pnl)} · Cash {money(desk.balance.cash)}
            </span>
          </div>
        </div>
      </section>

      {/* 5. BOTTOM SPLIT ROW: LEFT `JEV · ANALYSIS` | RIGHT `thresholds.py` */}
      <section className="retro-bottom-split">
        {/* LEFT: JEV · ANALYSIS */}
        <div className="retro-jev-panel" data-testid="jev-analysis-panel">
          <div className="retro-subpanel-header">
            <strong>JEV · ANALYSIS</strong>
            <span className="retro-pink-stat">
              {String(desk.jev_analysis.scanned_count).padStart(3, "0")} SCANNED · {desk.jev_analysis.benched_count} BENCHED
            </span>
          </div>

          {activeToken ? (
            <>
              <div className="retro-analysis-tokenbar">
                <div className="retro-analysis-title">
                  <GmgnEmblem size={20} />
                  <div>
                    <strong>{activeToken.symbol}</strong>
                    <span className="muted"> · {shortMint(activeToken.mint)}</span>
                  </div>
                </div>
                <div className="retro-analysis-metrics">
                  <span>MCap <b>{compact(activeToken.market_cap)}</b></span>
                  <span>Price <b>{activeToken.price ? `$${Number(activeToken.price).toFixed(4)}` : "—"}</b></span>
                  <span className={tone(activeToken.change_m5)}>5m <b>{activeToken.change_m5 ? `${activeToken.change_m5}%` : "—"}</b></span>
                  <span>Liq <b>{compact(activeToken.liquidity)}</b></span>
                  <span>Holders <b>{activeToken.holder_count ?? "—"}</b></span>
                  <button
                    type="button"
                    className="retro-link-btn"
                    onClick={() => navigate(`/token/${encodeURIComponent(activeToken.mint)}`)}
                  >
                    INSPECT ↗
                  </button>
                </div>
              </div>

              <div className="retro-analysis-stage">
                <MiniCandleChart candles={activeToken.candles} />
                <div className="retro-checklist-overlay">
                  {(activeToken.checks || []).map((chk) => (
                    <div key={chk.name} className="retro-check-row">
                      <span>{chk.name}</span>
                      <b
                        className={
                          chk.status === "PASS" || chk.status === "CONNECTED" || chk.status === "VERIFIED"
                            ? "up"
                            : chk.status === "DROP"
                              ? "down"
                              : "muted"
                        }
                      >
                        {chk.status}
                      </b>
                    </div>
                  ))}
                </div>
              </div>
            </>
          ) : (
            <p className="muted" style={{ padding: 12 }}>No candidate observed yet in {desk.mode} mode.</p>
          )}

          {/* Bottom candidate check audit log */}
          <div className="retro-audit-table">
            {desk.jev_analysis.audit_rows.slice(0, 6).map((row) => (
              <div
                key={`${row.seq}-${row.mint}-${row.at}`}
                className="retro-audit-row"
                onClick={() => setSelectedMint(row.mint)}
              >
                <span className="muted">{String(row.seq).padStart(4, "0")}</span>
                <strong>{row.symbol}</strong>
                <span className="muted">{row.stage} · {row.reason}</span>
                <span className={row.status === "PASS" ? "up" : "down"}>{row.status}</span>
                <span className="muted">{row.jev_model || "det"}</span>
              </div>
            ))}
          </div>
        </div>

        {/* RIGHT: thresholds.py */}
        <div className="retro-thresholds-panel" data-testid="thresholds-panel">
          <div className="retro-subpanel-header">
            <strong>thresholds.py</strong>
            <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
              <span className="retro-pink-stat">EVERY NUMBER THE DESK BELIEVES</span>
              <button
                type="button"
                className="retro-mini-btn"
                onClick={() => setShowGuideRef((v) => !v)}
              >
                {showGuideRef ? "HIDE GUIDE REF" : "GUIDE REF"}
              </button>
              <button
                type="button"
                className="retro-mini-btn"
                onClick={() => setEditingThresholds((v) => !v)}
              >
                {editingThresholds ? "CANCEL" : "EDIT"}
              </button>
            </div>
          </div>

          <div className="retro-thresholds-disclaimer">
            {desk.thresholds.disclaimer}
            {showGuideRef && " (Right column shows unvalidated guide constants for comparison only.)"}
          </div>

          {editingThresholds ? (
            <form className="retro-thresholds-form" onSubmit={saveThresholds}>
              <div className="retro-code-grid">
                <label>min_age_minutes <input name="min_age_minutes" defaultValue={desk.thresholds.hard.min_age_minutes} /></label>
                <label>min_liquidity_usd <input name="min_liquidity_usd" defaultValue={desk.thresholds.hard.min_liquidity_usd} /></label>
                <label>min_volume_h24 <input name="min_volume_h24" defaultValue={desk.thresholds.hard.min_volume_h24} /></label>
                <label>min_mcap_usd <input name="min_mcap_usd" defaultValue={desk.thresholds.hard.min_mcap_usd} /></label>
                <label>min_trades_h24 <input name="min_trades_h24" defaultValue={desk.thresholds.hard.min_trades_h24} /></label>
                <label>max_top_wallet <input name="max_top_wallet" defaultValue={desk.thresholds.hard.max_top_wallet} /></label>
                <label>max_top_10 <input name="max_top_10" defaultValue={desk.thresholds.hard.max_top_10} /></label>
              </div>
              <div className="retro-toggle-strip">
                <label><input type="checkbox" name="shadow_mode" defaultChecked={desk.thresholds.shadow_mode} /> shadow_mode</label>
                <label><input type="checkbox" name="one_position_at_a_time" defaultChecked={desk.thresholds.one_position_at_a_time} /> one_position_at_a_time</label>
                <label><input type="checkbox" name="use_bench" defaultChecked={desk.thresholds.use_bench} /> use_bench</label>
              </div>
              <button type="submit" className="action">Save thresholds.py</button>
            </form>
          ) : (
            <div className="retro-code-body">
              <div className="retro-code-comment">
                HARD = &#123; <span># arithmetic in code, runs before any model call</span>
              </div>
              {[
                ["min_age_minutes", desk.thresholds.hard.min_age_minutes],
                ["min_liquidity_usd", desk.thresholds.hard.min_liquidity_usd?.toLocaleString()],
                ["min_volume_h24", desk.thresholds.hard.min_volume_h24?.toLocaleString()],
                ["min_mcap_usd", desk.thresholds.hard.min_mcap_usd?.toLocaleString()],
                ["min_trades_h24", desk.thresholds.hard.min_trades_h24],
                ["max_top_wallet", desk.thresholds.hard.max_top_wallet],
                ["max_top_10", desk.thresholds.hard.max_top_10],
              ].map(([key, val]) => (
                <div key={String(key)} className="retro-code-line">
                  <span className="code-key">{key}</span>
                  <span className="code-val">{val}</span>
                  <span className="code-dots">
                    {showGuideRef
                      ? `guide: ${String(desk.thresholds.guide_inspiration.hard[String(key)] ?? "—")}`
                      : "···"}
                  </span>
                </div>
              ))}
              <div className="retro-code-comment">&#125;</div>

              <div className="retro-code-comment" style={{ marginTop: 8 }}>
                SOFT = &#123;{" "}
                <span>
                  # applied to JEV answers ({desk.jev.connected ? "ACTIVE" : "JEV NOT CONNECTED"})
                </span>
              </div>
              {[
                ["concentration_is_exit_risk", "max 0.55"],
                ["momentum_already_spent", "max 0.60"],
                ["liquidity_fits_ticket", "min 0.60"],
                ["account_is_the_project", "min 0.70"],
                ["recycled_account", "max 0.50"],
                ["dev_still_loaded", "max 0.55"],
              ].map(([key, spec]) => (
                <div key={key} className="retro-code-line">
                  <span className="code-key">{key}</span>
                  <span className="code-val">{spec}</span>
                  <span className="code-dots">···</span>
                </div>
              ))}
              <div className="retro-code-comment">&#125;</div>
            </div>
          )}

          {thresholdNotice && <div className="retro-threshold-note">{thresholdNotice}</div>}

          {/* filter.py stage bar */}
          <div className="retro-filter-footer">
            <div className="retro-filter-caption">
              filter.py · THE ORDER IS THE WHOLE POINT · shadow={String(desk.thresholds.shadow_mode)}
            </div>
            <div className="retro-stage-pills">
              {PIPELINE_STAGES.map((stage, idx) => {
                const count = desk.thresholds.stage_counts[stage] ?? 0;
                const isSelected = displayStage === stage;
                return (
                  <span key={stage} className="retro-stage-wrap">
                    <button
                      type="button"
                      className={`retro-stage-pill ${isSelected ? "active" : ""} ${stage === "pick" && count > 0 ? "picked" : ""}`}
                      onClick={() => setActiveStage(stage)}
                    >
                      {stage} ({count})
                    </button>
                    {idx < PIPELINE_STAGES.length - 1 && <span className="retro-stage-arrow">→</span>}
                  </span>
                );
              })}
            </div>
          </div>
        </div>
      </section>

      {/* 6. CLICK-TO-INSPECT AGENT MODAL / DRAWER */}
      {inspectedSeat && (
        <div className="retro-modal-backdrop" onClick={() => setInspectedSeat(null)} data-testid="seat-inspector-modal">
          <div className="retro-modal" onClick={(e) => e.stopPropagation()}>
            <div className="retro-modal-header">
              <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
                <SeatSprite seat={inspectedSeat} size={28} />
                <div>
                  <strong style={{ color: SEAT_COLORS[inspectedSeat], fontSize: 16 }}>{inspectedSeat}</strong>
                  <div className="muted">
                    {inspection ? `Logical engine: ${inspection.logical_agent}` : "Loading seat dossier…"}
                  </div>
                </div>
              </div>
              <button type="button" className="retro-btn" onClick={() => setInspectedSeat(null)}>
                ✕ CLOSE
              </button>
            </div>

            {inspectLoading && <p className="muted">Reading real seat telemetry…</p>}
            {inspection && (
              <div className="retro-modal-body">
                <div className="retro-modal-grid">
                  <div>
                    <span className="muted">Status &amp; Heartbeat</span>
                    <div>
                      <strong>{inspection.card.status.toUpperCase()}</strong> ·{" "}
                      {inspection.heartbeat_age_seconds != null ? `${inspection.heartbeat_age_seconds}s ago` : "idle"} ·{" "}
                      {inspection.last_duration_ms != null ? `${inspection.last_duration_ms}ms` : "0ms"}
                    </div>
                  </div>
                  <div>
                    <span className="muted">Active Candidate</span>
                    <div className="mono">
                      {inspection.card.active_symbol} ({inspection.card.active_mint || "none"})
                    </div>
                  </div>
                  <div>
                    <span className="muted">JEV / GMGN Provenance</span>
                    <div>
                      {inspection.data_sources.jev.status_label} · {inspection.data_sources.gmgn.venue_label}
                    </div>
                  </div>
                  <div>
                    <span className="muted">Current Metric</span>
                    <div>
                      {inspection.card.metric_label}: <strong>{inspection.card.metric_value}</strong>
                    </div>
                  </div>
                </div>

                {inspection.last_error && (
                  <div className="down" style={{ marginTop: 8 }}>
                    Last error: {inspection.last_error}
                  </div>
                )}

                <h3 className="retro-modal-subheading">Recent RiskEngine Vetoes &amp; Approvals</h3>
                <div className="retro-modal-events">
                  {inspection.recent_vetoes.length === 0 && <span className="muted">No risk decisions recorded yet.</span>}
                  {inspection.recent_vetoes.slice(0, 4).map((v, i) => (
                    <div key={i} className="retro-modal-evrow">
                      <span className={v.approved ? "up" : "down"}>{v.approved ? "APPROVED" : "VETO"}</span>
                      <span className="mono">{v.mint}</span>
                      <span className="muted">{v.explanation}</span>
                    </div>
                  ))}
                </div>

                <h3 className="retro-modal-subheading">Seat Event Timeline (Real Persisted Events)</h3>
                <div className="retro-modal-events">
                  {inspection.events.length === 0 && <span className="muted">No events emitted for this seat yet.</span>}
                  {inspection.events.map((ev) => (
                    <div key={ev.id} className="retro-modal-evrow">
                      <span className="mono muted">{ev.at.slice(11, 19)}</span>
                      <strong style={{ color: SEAT_COLORS[inspectedSeat] }}>{ev.type}</strong>
                      <span className="mono muted">{JSON.stringify(ev.payload).slice(0, 150)}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function SeatPanel({
  card,
  highlighted,
  onClick,
  onHover,
}: {
  card: SeatCard;
  highlighted: boolean;
  onClick: () => void;
  onHover?: (hovering: boolean) => void;
}) {
  const accent = SEAT_COLORS[card.seat];
  const isCrawler = card.seat === "CRAWLER";
  return (
    <article
      className={`retro-seat-card ${highlighted ? "highlighted" : ""}`}
      style={{ borderTopColor: accent }}
      data-testid={`seat-card-${card.seat}`}
      title={`${card.seat} (${card.status}) — Click to open full inspector`}
      onClick={onClick}
      onMouseEnter={() => onHover?.(true)}
      onMouseLeave={() => onHover?.(false)}
    >
      <div className="retro-seat-top">
        <div className="retro-seat-ident">
          {!isCrawler && <SeatSprite seat={card.seat} size={24} />}
          <div>
            <div className="retro-seat-name" style={{ color: card.seat === "SCAN" || card.seat === "CHIEF" ? "#f8fafc" : accent }}>
              {card.seat}
            </div>
            <div className="retro-seat-sub">{card.subtitle}</div>
          </div>
        </div>
        <span className={`retro-seat-badge ${card.jev_connected ? "jev-live" : ""}`}>{card.badge}</span>
      </div>

      {isCrawler && card.stats ? (
        <div className="retro-crawler-stats">
          <div><span>pages</span><b>{card.stats.pages}</b></div>
          <div><span>posts read</span><b>{card.stats.posts_read}</b></div>
          <div><span>webs spun</span><b>{card.stats.webs_spun}</b></div>
          <div><span>rugs killed</span><b>{card.stats.rugs_killed}</b></div>
        </div>
      ) : (
        <>
          <div className="retro-seat-metric-row">
            <span className="retro-seat-metric-lbl">{card.metric_label}</span>
            <strong className="retro-seat-metric-val">{card.metric_value}</strong>
          </div>
          <div className="retro-seat-bar-track">
            <div
              className="retro-seat-bar-fill"
              style={{
                width: `${Math.max(4, Math.min(100, Math.round((card.bar_ratio ?? 0) * 100)))}%`,
                background: accent,
              }}
            />
          </div>
        </>
      )}

      <div className="retro-seat-log">
        {isCrawler && <span className="retro-live-dot" aria-hidden="true" />}
        <span>{card.log_line}</span>
      </div>
    </article>
  );
}

function MiniCandleChart({ candles }: { candles: CandlePoint[] }) {
  const prices = candles.flatMap((c) => [c.high, c.low]);
  const minP = prices.length ? Math.min(...prices) : 0.9;
  const maxP = prices.length ? Math.max(...prices) : 1.05;
  const spanP = Math.max(maxP - minP, 0.000001);
  const maxV = Math.max(...candles.map((c) => c.volume), 1);

  return (
    <svg viewBox="0 0 520 185" className="retro-mini-candle-svg" shapeRendering="crispEdges">
      <rect x="0" y="0" width="520" height="185" fill="#060910" />
      {[36, 76, 116, 152].map((gy) => (
        <line key={gy} x1="8" y1={gy} x2="512" y2={gy} stroke="#111a2b" strokeWidth="1" />
      ))}
      {candles.map((c, idx) => {
        const step = 490 / Math.max(candles.length, 1);
        const x = 14 + idx * step;
        const bw = Math.max(5, Math.min(13, step * 0.62));
        const yH = 16 + (1 - (c.high - minP) / spanP) * 124;
        const yL = 16 + (1 - (c.low - minP) / spanP) * 124;
        const yO = 16 + (1 - (c.open - minP) / spanP) * 124;
        const yC = 16 + (1 - (c.close - minP) / spanP) * 124;
        const top = Math.min(yO, yC);
        const h = Math.max(3, Math.abs(yC - yO));
        const vH = Math.max(2, (c.volume / maxV) * 24);
        const color = c.up ? "#00e676" : "#ff384c";
        return (
          <g key={c.at + idx}>
            <line x1={x + bw / 2} y1={yH} x2={x + bw / 2} y2={yL} stroke={color} strokeWidth="1.5" />
            <rect x={x} y={top} width={bw} height={h} fill={color} />
            <rect x={x} y={180 - vH} width={bw} height={vH} fill={color} opacity="0.35" />
          </g>
        );
      })}
    </svg>
  );
}

function ClassicMetric({
  label,
  value,
  hint,
  tone: color,
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: "up" | "down" | "flat";
}) {
  return (
    <article className="card">
      <div className="label">{label}</div>
      <div className={`value ${color === "up" ? "up" : color === "down" ? "down" : ""}`}>{value}</div>
      {hint && <div className="muted">{hint}</div>}
    </article>
  );
}
