"""Read models for the dashboard, 8-seat retro control room, and monitor API. Secrets are never included."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import desc, func, select

from app.accounting import position_mark_value
from app.config import Settings
from app.db import session_scope
from app.funnel import DEFAULT_THRESHOLDS, GUIDE_INSPIRATION_THRESHOLDS, SEAT_ORDER, demo_playback_frames
from app.models import (
    AgentEvent,
    AgentHealth,
    Alert,
    AppSetting,
    BacktestRun,
    BenchEntry,
    ConfigVersion,
    EquitySnapshot,
    FunnelRun,
    LedgerEntry,
    MarketObservation,
    PaperFill,
    PaperOrder,
    PortfolioState,
    Position,
    ProviderHealth,
    RiskDecision,
    SecurityAssessment,
    ShadowDecision,
    StrategyProposal,
    SwarmAgentFinding,
    SwarmAgentHeartbeat,
    SwarmStructuredDecision,
    Token,
    KlineBar,
)
from app.money import q_usdc
from app.swarm import Swarm, limits_from_config
from app.tailscale_status import tailscale_health

SEAT_AGENT_MAP = {
    "CHIEF": "orchestrator",
    "SCAN": "momentum",
    "VET": "security",
    "SOCIAL": "discovery",
    "SIZE": "strategy",
    "FILLS": "execution",
    "RISK": "risk",
    "CRAWLER": "discovery",
}


def _s(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def _mode(session) -> str:
    row = session.get(AppSetting, "runtime")
    if row and row.value.get("mode") in {"demo", "live"}:
        return row.value["mode"]
    return "demo"


def _equity(session, mode: str, now: datetime) -> dict:
    state = session.get(PortfolioState, mode)
    if state is None:
        return {}
    limits = limits_from_config(session.get(AppSetting, "runtime").value)
    positions_rows = session.scalars(
        select(Position).where(Position.mode == mode, Position.status.in_(("open", "unsellable")))
    ).all()
    exposure = Decimal("0")
    fresh = True
    for pos in positions_rows:
        obs = session.scalar(
            select(MarketObservation)
            .where(MarketObservation.mode == mode, MarketObservation.mint == pos.mint)
            .order_by(desc(MarketObservation.observed_at))
        )
        mark = obs.price_usd if obs else None
        mark_fresh = bool(
            obs and mark is not None and (now - obs.observed_at).total_seconds() <= limits.market_max_age_seconds
        )
        value, is_fresh = position_mark_value(cost_basis=pos.cost_basis, qty=pos.qty, mark=mark, mark_fresh=mark_fresh)
        exposure += value
        fresh = fresh and is_fresh
    equity = q_usdc(state.cash + exposure)
    peak = state.peak_equity or equity
    drawdown = (peak - equity) / peak if peak else Decimal("0")
    daily = equity - state.day_start_equity
    total = equity - state.initial_equity
    return {
        "state": state,
        "equity": equity,
        "exposure": q_usdc(exposure),
        "drawdown": drawdown,
        "daily_pnl": daily,
        "total_pnl": total,
        "marks_fresh": fresh,
        "limits": limits,
    }


def summary(settings: Settings, swarm: Swarm) -> dict:
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        mode = _mode(session)
        book = _equity(session, mode, now)
        state = book.get("state")
        closed = session.scalars(select(Position).where(Position.mode == mode, Position.status == "closed")).all()
        wins = [row for row in closed if row.realized_pnl > 0]
        losses = [row for row in closed if row.realized_pnl < 0]
        win_rate = None
        profit_factor = None
        if closed:
            win_rate = Decimal(len(wins)) / Decimal(len(closed))
        if losses:
            profit_factor = (
                sum((row.realized_pnl for row in wins), Decimal("0"))
                / abs(sum((row.realized_pnl for row in losses), Decimal("0")))
                if wins
                else Decimal("0")
            )
        agents_rows = session.scalars(select(AgentHealth)).all()
        active = sum(1 for row in agents_rows if row.status in {"ok", "idle", "running"})
        providers_rows = session.scalars(select(ProviderHealth)).all()
        last_scan = session.scalar(
            select(func.max(MarketObservation.observed_at)).where(MarketObservation.mode == mode)
        )
        open_alerts = session.scalar(
            select(func.count()).select_from(Alert).where(Alert.mode == mode, Alert.acknowledged == False)  # noqa: E712
        )
        return {
            "mode": mode,
            "data_label": "demo fixture" if mode == "demo" else "provider data",
            "paper_trading": True,
            "live_execution_enabled": False,
            "network_submission_enabled": False,
            "emergency_stop": bool(state.emergency_stop) if state else False,
            "emergency_stop_reason": state.emergency_stop_reason if state else None,
            "equity": _s(book.get("equity")),
            "cash": _s(state.cash) if state else None,
            "daily_pnl": _s(book.get("daily_pnl")),
            "daily_pnl_available": state is not None,
            "total_pnl": _s(book.get("total_pnl")),
            "total_pnl_available": state is not None,
            "win_rate": _s(win_rate) if win_rate is not None else None,
            "win_rate_available": win_rate is not None,
            "profit_factor": _s(profit_factor) if profit_factor is not None else None,
            "profit_factor_available": profit_factor is not None,
            "open_exposure": _s(book.get("exposure")),
            "max_drawdown": _s(book.get("drawdown")),
            "max_drawdown_available": state is not None,
            "marks_fresh": book.get("marks_fresh", False),
            "open_positions": len(
                session.scalars(
                    select(Position).where(Position.mode == mode, Position.status.in_(("open", "unsellable")))
                ).all()
            ),
            "active_agents": active,
            "provider_health": [_provider(row) for row in providers_rows],
            "tailscale": tailscale_health(),
            "last_scan_at": last_scan.isoformat() if last_scan else None,
            "alerts_open": int(open_alerts or 0),
            "ai_commentary": "unavailable" if not settings.xai_api_key else "configured",
            "jev_status": "connected" if swarm.jev.available else "not_connected",
            "gmgn_configured": bool(settings.gmgn_api_key),
        }


def _provider(row: ProviderHealth) -> dict:
    return {
        "provider": row.provider,
        "status": row.status,
        "configured": row.configured,
        "circuit": row.circuit,
        "latency_ms": row.latency_ms,
        "last_ok_at": row.last_ok_at.isoformat() if row.last_ok_at else None,
        "last_error": row.last_error,
        "detail": row.detail,
    }


def _candles_from_klines(kline_rows: list[KlineBar], limit: int = 28) -> list[dict]:
    pts = kline_rows[-limit:]
    candles: list[dict] = []
    for row in pts:
        open_p = float(row.open_price)
        high_p = float(row.high_price)
        low_p = float(row.low_price)
        close_p = float(row.close_price)
        vol = float(row.volume_usd) if row.volume_usd is not None else 0.0
        dt = datetime.fromtimestamp(row.bar_ts, tz=timezone.utc)
        candles.append(
            {
                "at": dt.isoformat(),
                "time": dt.strftime("%H:%M"),
                "open": round(open_p, 8),
                "high": round(high_p, 8),
                "low": round(low_p, 8),
                "close": round(close_p, 8),
                "volume": round(vol, 2),
                "up": close_p >= open_p,
                "provider": row.provider,
            }
        )
    return candles


def _candles_from_observations(obs_rows: list[MarketObservation], limit: int = 28) -> list[dict]:
    """Build deterministic OHLC candles from sequential observed prints without inventing prices."""
    pts = [r for r in obs_rows if r.price_usd is not None][-limit:]
    candles: list[dict] = []
    for idx, row in enumerate(pts):
        close_p = float(row.price_usd)
        open_p = float(pts[idx - 1].price_usd) if idx > 0 and pts[idx - 1].price_usd is not None else close_p
        high_p = max(open_p, close_p)
        low_p = min(open_p, close_p)
        vol = float(row.volume_m5) if row.volume_m5 is not None else 0.0
        candles.append(
            {
                "at": row.observed_at.isoformat(),
                "time": row.observed_at.strftime("%H:%M"),
                "open": round(open_p, 8),
                "high": round(high_p, 8),
                "low": round(low_p, 8),
                "close": round(close_p, 8),
                "volume": round(vol, 2),
                "up": close_p >= open_p,
                "provider": row.provider,
            }
        )
    return candles


def desk_state(settings: Settings, swarm: Swarm) -> dict[str, Any]:
    """Complete state for the top-down pixel-art SOL SWARM + JEV Control Room."""
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        mode = _mode(session)
        runtime = (session.get(AppSetting, "runtime").value or {}) if session.get(AppSetting, "runtime") else {}
        hard, soft = swarm._thresholds(runtime)
        scanner_cfg = runtime.get("scanner") or {}
        book = _equity(session, mode, now)
        state = book.get("state")

        closed = session.scalars(select(Position).where(Position.mode == mode, Position.status == "closed")).all()
        open_pos = session.scalars(
            select(Position).where(Position.mode == mode, Position.status.in_(("open", "unsellable"))).order_by(desc(Position.opened_at))
        ).all()
        wins = sum(1 for r in closed if r.realized_pnl > 0)
        losses = sum(1 for r in closed if r.realized_pnl < 0)

        first_ledger = session.scalar(
            select(LedgerEntry).where(LedgerEntry.mode == mode).order_by(LedgerEntry.created_at)
        )
        day_num = 1
        if first_ledger and first_ledger.created_at:
            day_num = max(1, (now.date() - first_ledger.created_at.date()).days + 1)

        latest_funnel = session.scalar(
            select(FunnelRun).where(FunnelRun.mode == mode).order_by(desc(FunnelRun.created_at))
        )
        funnel_stats = (latest_funnel.stats if latest_funnel else None) or {
            "seen": 0,
            "benched": 0,
            "free": {},
            "trade": {},
            "chain": {},
            "soft": {},
            "picked": None,
            "pages_read": 0,
            "rugs_killed": 0,
            "webs_spun": 0,
        }

        total_obs_count = int(
            session.scalar(select(func.count()).select_from(MarketObservation).where(MarketObservation.mode == mode))
            or 0
        )
        total_sec_fails = int(
            session.scalar(
                select(func.count(func.distinct(SecurityAssessment.mint)))
                .where(SecurityAssessment.mode == mode, SecurityAssessment.classification == "fail")
            )
            or 0
        )
        active_bench_count = int(
            session.scalar(
                select(func.count()).select_from(BenchEntry).where(BenchEntry.mode == mode, BenchEntry.until_at > now)
            )
            or 0
        )

        tokens_all = session.scalars(select(Token).where(Token.mode == mode)).all()
        token_summaries: list[dict[str, Any]] = []
        for tok in tokens_all:
            obs = session.scalar(
                select(MarketObservation)
                .where(MarketObservation.mode == mode, MarketObservation.mint == tok.mint)
                .order_by(desc(MarketObservation.observed_at), desc(MarketObservation.discovery_score))
            )
            sec = session.scalar(
                select(SecurityAssessment)
                .where(SecurityAssessment.mode == mode, SecurityAssessment.mint == tok.mint)
                .order_by(desc(SecurityAssessment.observed_at))
            )
            prop = session.scalar(
                select(StrategyProposal)
                .where(StrategyProposal.mode == mode, StrategyProposal.mint == tok.mint)
                .order_by(desc(StrategyProposal.created_at))
            )
            mom_ev = session.scalar(
                select(AgentEvent)
                .where(AgentEvent.mode == mode, AgentEvent.mint == tok.mint, AgentEvent.event_type == "momentum.report")
                .order_by(desc(AgentEvent.created_at))
            )
            sec_ev = session.scalar(
                select(AgentEvent)
                .where(AgentEvent.mode == mode, AgentEvent.mint == tok.mint, AgentEvent.event_type == "security.report")
                .order_by(desc(AgentEvent.created_at))
            )
            soc_ev = session.scalar(
                select(AgentEvent)
                .where(AgentEvent.mode == mode, AgentEvent.mint == tok.mint, AgentEvent.event_type == "social.report")
                .order_by(desc(AgentEvent.created_at))
            )
            token_summaries.append(
                {
                    "mint": tok.mint,
                    "symbol": tok.symbol,
                    "name": tok.name,
                    "holder_count": tok.holder_count,
                    "promoted": tok.promoted,
                    "price": _s(obs.price_usd) if obs else None,
                    "liquidity": _s(obs.pool_liquidity_usd) if obs else None,
                    "market_cap": _s(obs.market_cap_usd) if obs else None,
                    "volume_m5": _s(obs.volume_m5) if obs else None,
                    "volume_h1": _s(obs.volume_h1) if obs else None,
                    "change_m5": _s(obs.price_change_m5) if obs else None,
                    "change_h1": _s(obs.price_change_h1) if obs else None,
                    "buys_m5": obs.buys_m5 if obs else None,
                    "sells_m5": obs.sells_m5 if obs else None,
                    "discovery_score": obs.discovery_score if obs else 0,
                    "momentum_score": (mom_ev.payload or {}).get("score") if mom_ev else None,
                    "security_status": sec.classification if sec else "unknown",
                    "security_score": sec.score if sec else None,
                    "critical_flags": sec.critical_flags if sec else [],
                    "top_holder_pct": (sec_ev.payload or {}).get("top_holder_pct") if sec_ev else None,
                    "x_handle": (soc_ev.payload or {}).get("x_handle") if soc_ev else None,
                    "proposal_status": prop.status if prop else "observed",
                    "provider": obs.provider if obs else None,
                    "observed_at": obs.observed_at.isoformat() if obs else None,
                }
            )
        token_summaries.sort(key=lambda r: int(r.get("discovery_score") or 0), reverse=True)

        # Pick featured token: open position first, else highest-scoring candidate.
        featured_mint = open_pos[0].mint if open_pos else (token_summaries[0]["mint"] if token_summaries else None)
        featured = next((t for t in token_summaries if t["mint"] == featured_mint), None)
        featured_candles: list[dict] = []
        featured_checks: list[dict] = []
        if featured:
            kline_history = session.scalars(
                select(KlineBar)
                .where(KlineBar.mode == mode, KlineBar.mint == featured["mint"])
                .order_by(KlineBar.bar_ts)
            ).all()
            if kline_history:
                featured_candles = _candles_from_klines(kline_history)
            else:
                obs_history = session.scalars(
                    select(MarketObservation)
                    .where(MarketObservation.mode == mode, MarketObservation.mint == featured["mint"])
                    .order_by(MarketObservation.observed_at)
                ).all()
                featured_candles = _candles_from_observations(obs_history)
            liq_val = Decimal(featured["liquidity"]) if featured.get("liquidity") else Decimal("0")
            liq_ok = liq_val >= Decimal(str(hard.get("min_liquidity_usd", 50000)))
            sec_stat = featured["security_status"]
            featured_checks = [
                {
                    "name": "liquidity depth",
                    "status": "PASS" if liq_ok else "DROP",
                    "detail": f"${float(liq_val):,.0f} vs ${float(hard.get('min_liquidity_usd', 50000)):,.0f}",
                },
                {
                    "name": "holder spread",
                    "status": "PASS" if (featured.get("top_holder_pct") and float(featured["top_holder_pct"]) <= float(hard.get("max_top_wallet", 0.25)) * 100) else ("UNKNOWN" if not featured.get("top_holder_pct") else "DROP"),
                    "detail": f"top {featured.get('top_holder_pct') or '?'}%",
                },
                {
                    "name": "authority safety",
                    "status": "PASS" if sec_stat == "pass" else ("DROP" if sec_stat == "fail" else "UNKNOWN"),
                    "detail": ", ".join(featured["critical_flags"]) if featured["critical_flags"] else "mint+freeze checked",
                },
                {
                    "name": "trade flow",
                    "status": "PASS" if (featured.get("buys_m5") or 0) > (featured.get("sells_m5") or 0) else "WAIT",
                    "detail": f"{featured.get('buys_m5') or 0}B / {featured.get('sells_m5') or 0}S (5m)",
                },
                {
                    "name": "social x handle",
                    "status": "VERIFIED" if featured.get("x_handle") else "NONE",
                    "detail": f"@{featured['x_handle']}" if featured.get("x_handle") else "no on-chain handle",
                },
                {
                    "name": "jev judgement",
                    "status": "CONNECTED" if swarm.jev.available else "OFFLINE",
                    "detail": swarm.jev.last_resolved_model or "JEV NOT CONNECTED",
                },
            ]

        # Build recent events and map each of the 8 seats to its latest genuine backend state.
        recent_events = session.scalars(
            select(AgentEvent).where(AgentEvent.mode == mode).order_by(desc(AgentEvent.created_at)).limit(80)
        ).all()
        health_map = {row.agent_name: row for row in session.scalars(select(AgentHealth)).all()}
        hb_cutoff = now.timestamp() - max(15, settings.grok_bot_freshness_seconds)
        swarm_hb_map: dict[str, SwarmAgentHeartbeat] = {}
        for hb in session.scalars(select(SwarmAgentHeartbeat)).all():
            if hb.last_seen_at and hb.last_seen_at.timestamp() >= hb_cutoff:
                swarm_hb_map[hb.seat] = hb

        latest_swarm_dec = session.scalar(
            select(SwarmStructuredDecision)
            .where(SwarmStructuredDecision.mode == mode)
            .order_by(desc(SwarmStructuredDecision.created_at))
        )
        last_order = session.scalar(
            select(PaperOrder).where(PaperOrder.mode == mode).order_by(desc(PaperOrder.created_at))
        )
        last_fill = (
            session.scalar(select(PaperFill).where(PaperFill.order_id == last_order.id).order_by(desc(PaperFill.filled_at)))
            if last_order
            else None
        )

        latest_by_seat: dict[str, AgentEvent] = {}
        for ev in recent_events:
            seat = (ev.payload or {}).get("seat")
            if seat in SEAT_ORDER and seat not in latest_by_seat:
                latest_by_seat[seat] = ev

        equity_val = book.get("equity") or Decimal("1000")
        max_ticket = min(
            q_usdc(equity_val * Decimal(str((runtime.get("risk") or {}).get("max_position_pct", "0.02")))),
            Decimal(str((runtime.get("risk") or {}).get("max_new_trade_usd", "20"))),
        )
        ticket_pct = (max_ticket / equity_val * Decimal("100")).quantize(Decimal("0.1")) if equity_val > 0 else Decimal("0")

        jev_ok = swarm.jev.available
        paused = bool(state.emergency_stop) if state else False
        seats = _build_seats(
            now=now,
            mode=mode,
            jev_ok=jev_ok,
            paused=paused,
            featured=featured,
            open_pos=open_pos,
            wins=wins,
            losses=losses,
            max_ticket=max_ticket,
            ticket_pct=ticket_pct,
            last_order=last_order,
            last_fill=last_fill,
            funnel_stats=funnel_stats,
            total_obs_count=total_obs_count,
            total_sec_fails=total_sec_fails,
            active_bench_count=active_bench_count,
            latest_by_seat=latest_by_seat,
            health_map=health_map,
            swarm_hb_map=swarm_hb_map,
            latest_swarm_dec=latest_swarm_dec,
            soft=soft,
        )

        # Build equity curve + delta bars from actual EquitySnapshots (never invented gains).
        snaps = session.scalars(
            select(EquitySnapshot).where(EquitySnapshot.mode == mode).order_by(EquitySnapshot.created_at).limit(60)
        ).all()
        curve: list[dict[str, Any]] = []
        for idx, snap in enumerate(snaps):
            eq_f = float(snap.equity)
            prev_f = float(snaps[idx - 1].equity) if idx > 0 else float(state.initial_equity if state else snap.equity)
            curve.append(
                {
                    "at": snap.created_at.isoformat(),
                    "time": snap.created_at.strftime("%H:%M:%S"),
                    "equity": eq_f,
                    "delta": round(eq_f - prev_f, 4),
                    "drawdown": float(snap.drawdown),
                }
            )

        # Audit log rows for JEV · ANALYSIS bottom table.
        shadows = session.scalars(
            select(ShadowDecision).where(ShadowDecision.mode == mode).order_by(desc(ShadowDecision.created_at)).limit(12)
        ).all()
        audit_rows = [
            {
                "seq": idx + 1,
                "mint": row.mint,
                "symbol": row.symbol,
                "stage": row.funnel_stage,
                "reason": (row.reasons[0] if row.reasons else "clean_pass"),
                "status": "PASS" if row.deterministic_outcome == "propose" else "DROP",
                "jev_model": row.jev_model,
                "disagreement": row.disagreement,
                "at": row.created_at.isoformat(),
            }
            for idx, row in enumerate(shadows)
        ]

        # Crawler investigation web — active only when a recent scan occurred within the last 90s.
        last_sweep = latest_by_seat.get("CRAWLER")
        sweep_fresh = bool(last_sweep and (now - last_sweep.created_at).total_seconds() <= 90)
        crawler_web = {
            "active": sweep_fresh and featured is not None,
            "source": "live_cycle_event" if mode == "live" else "demo_cycle_event",
            "symbol": featured["symbol"] if featured else None,
            "mint": featured["mint"] if featured else None,
            "callouts": [
                f"liq ${float(featured['liquidity'] or 0):,.0f}" if featured else "idle",
                f"security: {featured['security_status']}" if featured else "idle",
                f"x_handle: {'@' + featured['x_handle'] if featured and featured.get('x_handle') else 'none'}",
            ]
            if featured
            else [],
        }

        stage_counts = {
            "free_kill": sum((funnel_stats.get("free") or {}).values()),
            "trade_kill": sum((funnel_stats.get("trade") or {}).values()),
            "chain_kill": sum((funnel_stats.get("chain") or {}).values()),
            "soft_kill": sum((funnel_stats.get("soft") or {}).values()),
            "pick": 1 if funnel_stats.get("picked") else 0,
        }

        active_decision_source = (
            latest_swarm_dec.decision_source
            if latest_swarm_dec is not None
            else ("grok_bot" if any(h.decision_source == "grok_bot" for h in swarm_hb_map.values()) else "deterministic_fallback")
        )

        return {
            "mode": mode,
            "mode_badge": "DEMO" if mode == "demo" else "LIVE PAPER",
            "data_label": "DEMO FIXTURES (PAPER)" if mode == "demo" else "LIVE PAPER (REAL GMGN)",
            "decision_source": active_decision_source,
            "paper_trading": True,
            "live_execution_enabled": False,
            "emergency_stop": paused,
            "emergency_stop_reason": state.emergency_stop_reason if state else None,
            "utc_now": now.isoformat(),
            "day_label": f"DAY {day_num} · {now.strftime('%b %d').upper()}",
            "clock_utc": now.strftime("%H:%M UTC"),
            "jev": {
                "connected": jev_ok,
                "configured": bool(settings.enable_optional_jev and settings.typesafe_api_key),
                "status_label": f"JEV · {swarm.jev.last_resolved_model or settings.typesafe_model}" if jev_ok else "JEV NOT CONNECTED",
                "requested_model": settings.typesafe_model,
                "resolved_model": swarm.jev.last_resolved_model,
                "total_calls": swarm.jev.total_calls,
                "total_input_tokens": swarm.jev.total_input_tokens,
                "halted_reason": swarm.jev.halted_422_reason,
            },
            "gmgn": {
                "configured": bool(settings.gmgn_api_key),
                "status": swarm.gmgn.snapshot().status,
                "detail": swarm.gmgn.snapshot().detail,
                "venue_label": "GMGN PAPER QUOTE" if settings.gmgn_api_key else "GMGN KEY NOT SET (SECONDARY FEEDS)",
                "live_swaps_enabled": False,
            },
            "grok": {
                "configured": bool(settings.xai_api_key or any(h.decision_source == "grok_bot" for h in swarm_hb_map.values())),
                "status_label": (
                    "GROK BOT MCP LIVE"
                    if any(h.decision_source == "grok_bot" for h in swarm_hb_map.values())
                    else (f"GROK ({settings.xai_model})" if settings.xai_api_key else "DET FALLBACK ($0)")
                ),
            },
            "tailscale": tailscale_health(),
            "seats": seats,
            "featured_token": {
                **(featured or {}),
                "candles": featured_candles,
                "checks": featured_checks,
            }
            if featured
            else None,
            "candidates": token_summaries[:5],
            "crawler_web": crawler_web,
            "balance": {
                "equity": _s(book.get("equity")),
                "cash": _s(state.cash) if state else None,
                "initial_equity": _s(state.initial_equity) if state else "1000.000000",
                "daily_pnl": _s(book.get("daily_pnl")),
                "total_pnl": _s(book.get("total_pnl")),
                "open_exposure": _s(book.get("exposure")),
                "max_drawdown": _s(book.get("drawdown")),
                "open_positions": len(open_pos),
                "held_symbol": featured["symbol"] if (open_pos and featured) else (featured["symbol"] if featured else "NONE"),
                "wins": wins,
                "losses": losses,
                "curve": curve,
            },
            "jev_analysis": {
                "scanned_count": len(token_summaries),
                "observations_total": total_obs_count,
                "benched_count": active_bench_count,
                "audit_rows": audit_rows,
            },
            "thresholds": {
                "label": DEFAULT_THRESHOLDS["label"],
                "disclaimer": DEFAULT_THRESHOLDS["disclaimer"],
                "hard": hard,
                "soft": soft,
                "guide_inspiration": GUIDE_INSPIRATION_THRESHOLDS,
                "stage_counts": stage_counts,
                "stage_breakdown": {
                    "free_kill": funnel_stats.get("free") or {},
                    "trade_kill": funnel_stats.get("trade") or {},
                    "chain_kill": funnel_stats.get("chain") or {},
                    "soft_kill": funnel_stats.get("soft") or {},
                    "picked": funnel_stats.get("picked"),
                },
                "shadow_mode": bool(scanner_cfg.get("shadow_mode", False)),
                "one_position_at_a_time": bool(scanner_cfg.get("one_position_at_a_time", False)),
                "use_bench": bool(scanner_cfg.get("use_bench", False)),
            },
        }


def _build_seats(
    *,
    now: datetime,
    mode: str,
    jev_ok: bool,
    paused: bool,
    featured: dict | None,
    open_pos: list[Position],
    wins: int,
    losses: int,
    max_ticket: Decimal,
    ticket_pct: Decimal,
    last_order: PaperOrder | None,
    last_fill: PaperFill | None,
    funnel_stats: dict,
    total_obs_count: int,
    total_sec_fails: int,
    active_bench_count: int,
    latest_by_seat: dict[str, AgentEvent],
    health_map: dict[str, AgentHealth],
    swarm_hb_map: dict[str, SwarmAgentHeartbeat],
    latest_swarm_dec: SwarmStructuredDecision | None,
    soft: dict,
) -> list[dict[str, Any]]:
    sym = featured["symbol"] if featured else "NONE"
    picked = funnel_stats.get("picked")

    def _hb(agent_key: str) -> tuple[str | None, int, int, str]:
        h = health_map.get(agent_key)
        if not h:
            return None, 0, 0, "idle"
        return (
            h.last_heartbeat.isoformat() if h.last_heartbeat else None,
            h.processed_count,
            h.error_count,
            h.status,
        )

    def _seat_src(seat_key: str) -> str:
        hb = swarm_hb_map.get(seat_key)
        if hb and hb.decision_source == "grok_bot":
            return "grok_bot"
        if jev_ok and seat_key in {"CHIEF", "SCAN", "VET", "SOCIAL"}:
            return "optional_jev"
        if seat_key in {"SIZE", "FILLS", "RISK", "CRAWLER"}:
            return "deterministic_engine"
        return "deterministic_fallback"

    chief_src = _seat_src("CHIEF")
    if latest_swarm_dec and latest_swarm_dec.decision_source == "grok_bot":
        chief_src = "grok_bot"
    scan_src = _seat_src("SCAN")
    vet_src = _seat_src("VET")
    soc_src = _seat_src("SOCIAL")

    chief_ev = latest_by_seat.get("CHIEF")
    chief_hb, chief_proc, chief_err, chief_st = _hb("orchestrator")
    if "CHIEF" in swarm_hb_map and swarm_hb_map["CHIEF"].decision_source == "grok_bot":
        chief_hb = swarm_hb_map["CHIEF"].last_seen_at.isoformat()
        chief_st = swarm_hb_map["CHIEF"].status
    chief_pick_text = f"last pick {picked}" if picked else (f"holding {open_pos[0].mint}" if open_pos else "standing down · no pick")
    if open_pos and featured:
        chief_pick_text = f"last pick {featured['symbol']}"

    scan_ev = latest_by_seat.get("SCAN")
    scan_hb, scan_proc, scan_err, scan_st = _hb("momentum")
    if "SCAN" in swarm_hb_map and swarm_hb_map["SCAN"].decision_source == "grok_bot":
        scan_hb = swarm_hb_map["SCAN"].last_seen_at.isoformat()
        scan_st = swarm_hb_map["SCAN"].status
    mom_score = (featured.get("momentum_score") if featured else None)
    scan_norm = round(float(mom_score) / 100.0, 2) if mom_score is not None else None
    scan_jev = ((scan_ev.payload or {}).get("jev_shape") or {}) if scan_ev else {}
    if scan_jev and isinstance(scan_jev.get("probabilities"), dict):
        scan_val = f"{float(scan_jev['probabilities'].get('crowd', 0)):.2f}"
        scan_metric_lbl = f"shape.crowd · {sym}"
        scan_bar = float(scan_jev["probabilities"].get("crowd", 0))
    else:
        scan_val = f"{scan_norm:.2f}" if scan_norm is not None else "—"
        scan_metric_lbl = f"momentum.{'grok' if scan_src == 'grok_bot' else 'det'} · {sym}"
        scan_bar = scan_norm

    vet_ev = latest_by_seat.get("VET")
    vet_hb, vet_proc, vet_err, vet_st = _hb("security")
    if "VET" in swarm_hb_map and swarm_hb_map["VET"].decision_source == "grok_bot":
        vet_hb = swarm_hb_map["VET"].last_seen_at.isoformat()
        vet_st = swarm_hb_map["VET"].status
    vet_jev = ((vet_ev.payload or {}).get("jev_dev_loaded") or {}) if vet_ev else {}
    if vet_jev and "noul" in vet_jev:
        vet_val = f"{float(vet_jev['noul']):.2f}"
        vet_lbl = f"dev_still_loaded · {sym}"
        vet_bar = float(vet_jev["noul"])
    else:
        sec_score = featured.get("security_score") if featured else None
        vet_bar = round(float(sec_score) / 100.0, 2) if sec_score is not None else None
        vet_val = (featured.get("security_status") or "unknown").upper() if featured else "IDLE"
        vet_lbl = f"chain_safety · {sym}"

    soc_ev = latest_by_seat.get("SOCIAL")
    soc_payload = (soc_ev.payload or {}) if soc_ev else {}
    x_handle = soc_payload.get("x_handle") or (featured.get("x_handle") if featured else None)
    soc_val = f"@{x_handle}" if x_handle else "NO HANDLE"
    soc_lbl = f"x_account · {sym}"

    size_hb, size_proc, size_err, size_st = _hb("strategy")
    fills_ev = latest_by_seat.get("FILLS")
    fills_hb, fills_proc, fills_err, fills_st = _hb("execution")
    if last_order and last_fill:
        fp = float(last_fill.price)
        fp_str = f"{fp:.2f}" if fp >= 0.01 else f"{fp:.6g}"
        fill_val = f"{last_order.side} ${float(last_fill.notional):.2f} @ ${fp_str}"
        fill_log = f"fee ${float(last_fill.fee):.2f} ({last_fill.fee_source}) · paper only"
        fill_bar = 1.0
    elif last_order and last_order.status != "filled":
        fill_val = f"{last_order.status}"
        fill_log = last_order.rejection_reason or "quote unavailable"
        fill_bar = 0.2
    else:
        fill_val = "waiting on a pick"
        fill_log = "no live swap route enabled · paper quotes only"
        fill_bar = 0.0

    risk_ev = latest_by_seat.get("RISK")
    risk_hb, risk_proc, risk_err, risk_st = _hb("risk")
    risk_log = (
        "EMERGENCY STOP — new entries paused"
        if paused
        else ((risk_ev.payload or {}).get("explanation") if risk_ev else "independent deterministic veto armed")
    )

    crawl_ev = latest_by_seat.get("CRAWLER")
    crawl_hb, crawl_proc, crawl_err, crawl_st = _hb("discovery")
    evaluated = int(funnel_stats.get("evaluated") or 0)
    seen = int(funnel_stats.get("seen") or 0)

    return [
        {
            "seat": "CHIEF",
            "subtitle": "never takes a position",
            "badge": "GROK BOT" if chief_src == "grok_bot" else ("JEV PICK" if jev_ok else "DET FALLBACK"),
            "jev_connected": jev_ok or chief_src == "grok_bot",
            "decision_source": chief_src,
            "status": "paused" if paused else chief_st,
            "metric_label": (
                "source · grok_bot"
                if chief_src == "grok_bot"
                else ("question set · pick" if jev_ok else "source · det_fallback")
            ),
            "metric_value": chief_pick_text,
            "bar_ratio": 0.75 if picked else 0.25,
            "log_line": (
                latest_swarm_dec.thesis[:92]
                if (chief_src == "grok_bot" and latest_swarm_dec)
                else (
                    f"worth >= {soft.get('pick_min_worth', 0.60):.2f} · conf >= {soft.get('pick_min_conf', 0.55):.2f}"
                    if jev_ok
                    else "deterministic_fallback · $0 local strategy gate"
                )
            ),
            "speech_bubble": f"pick: {picked}" if picked else None,
            "target_station": "chief",
            "active_mint": featured["mint"] if featured else None,
            "active_symbol": sym,
            "last_heartbeat": chief_hb,
            "processed": chief_proc,
            "errors": chief_err,
        },
        {
            "seat": "SCAN",
            "subtitle": "finding what moves",
            "badge": "GROK BOT" if scan_src == "grok_bot" else ("ASKS JEV" if jev_ok else "DET FALLBACK"),
            "jev_connected": jev_ok or scan_src == "grok_bot",
            "decision_source": scan_src,
            "status": scan_st,
            "metric_label": scan_metric_lbl,
            "metric_value": scan_val,
            "bar_ratio": scan_bar,
            "log_line": (
                ((scan_ev.payload or {}).get("reasons") or ["scanning Solana pools"])[0]
                if scan_ev
                else "waiting for candidate sweep"
            ),
            "speech_bubble": f"scoring {sym} ({scan_val})" if featured else None,
            "target_station": "screen" if featured else "desk",
            "active_mint": featured["mint"] if featured else None,
            "active_symbol": sym,
            "last_heartbeat": scan_hb,
            "processed": scan_proc,
            "errors": scan_err,
        },
        {
            "seat": "VET",
            "subtitle": "chain risk, is it real",
            "badge": "GROK BOT" if vet_src == "grok_bot" else ("ASKS JEV" if jev_ok else "RPC + GMGN"),
            "jev_connected": jev_ok or vet_src == "grok_bot",
            "decision_source": vet_src,
            "status": vet_st,
            "metric_label": vet_lbl,
            "metric_value": vet_val,
            "bar_ratio": vet_bar,
            "log_line": (
                (vet_ev.payload or {}).get("explanation")
                if vet_ev
                else "checking mint & freeze authorities"
            ),
            "speech_bubble": (
                f"{total_sec_fails} rugs blocked" if total_sec_fails else f"{sym}: {vet_val}"
            ),
            "target_station": "center" if total_sec_fails else "desk",
            "active_mint": (vet_ev.mint if vet_ev else None) or (featured["mint"] if featured else None),
            "active_symbol": sym,
            "last_heartbeat": vet_hb,
            "processed": vet_proc,
            "errors": vet_err,
        },
        {
            "seat": "SOCIAL",
            "subtitle": "the project's X account",
            "badge": "GROK BOT" if soc_src == "grok_bot" else ("ASKS JEV" if jev_ok else "HANDLE ONLY"),
            "jev_connected": jev_ok or soc_src == "grok_bot",
            "decision_source": soc_src,
            "status": "online" if soc_src == "grok_bot" else ("unconfigured" if not jev_ok else "idle"),
            "metric_label": soc_lbl,
            "metric_value": soc_val,
            "bar_ratio": 0.75 if soc_src == "grok_bot" else (0.5 if x_handle else 0.0),
            "log_line": (
                swarm_hb_map["SOCIAL"].note
                if ("SOCIAL" in swarm_hb_map and swarm_hb_map["SOCIAL"].decision_source == "grok_bot" and swarm_hb_map["SOCIAL"].note)
                else (soc_payload.get("note") or "no X feed plugin · never guesses fan accounts")
            ),
            "speech_bubble": f"handle @{x_handle}" if x_handle else None,
            "target_station": "desk",
            "active_mint": featured["mint"] if featured else None,
            "active_symbol": sym,
            "last_heartbeat": (
                swarm_hb_map["SOCIAL"].last_seen_at.isoformat()
                if "SOCIAL" in swarm_hb_map
                else (soc_ev.created_at.isoformat() if soc_ev else None)
            ),
            "processed": crawl_proc,
            "errors": 0,
        },
        {
            "seat": "SIZE",
            "subtitle": "how much",
            "badge": "MATH",
            "jev_connected": False,
            "decision_source": "deterministic_engine",
            "status": size_st,
            "metric_label": f"ticket {ticket_pct}%",
            "metric_value": f"${float(max_ticket):,.2f}",
            "bar_ratio": min(1.0, float(ticket_pct) / 6.0),
            "log_line": f"max ${float(max_ticket):.0f} · <=2% bank · <=1% pool",
            "speech_bubble": f"ticket ${float(max_ticket):.0f}" if picked else None,
            "target_station": "desk",
            "active_mint": featured["mint"] if featured else None,
            "active_symbol": sym,
            "last_heartbeat": size_hb,
            "processed": size_proc,
            "errors": size_err,
        },
        {
            "seat": "FILLS",
            "subtitle": "execution (paper)",
            "badge": "GMGN PAPER",
            "jev_connected": False,
            "decision_source": "deterministic_engine",
            "status": fills_st,
            "metric_label": "last paper fill",
            "metric_value": fill_val,
            "bar_ratio": fill_bar,
            "log_line": fill_log,
            "speech_bubble": fill_val if last_fill else None,
            "target_station": "center" if last_fill else "desk",
            "active_mint": last_order.mint if last_order else None,
            "active_symbol": sym,
            "last_heartbeat": fills_hb,
            "processed": fills_proc,
            "errors": fills_err,
        },
        {
            "seat": "RISK",
            "subtitle": "closing & final veto",
            "badge": "FINAL VETO",
            "jev_connected": False,
            "decision_source": "deterministic_engine",
            "status": "paused" if paused else risk_st,
            "metric_label": f"open {len(open_pos)} · closed",
            "metric_value": f"{wins}W {losses}L",
            "bar_ratio": 1.0 if paused else (0.65 if open_pos else 0.25),
            "log_line": str(risk_log)[:96],
            "speech_bubble": "PAUSED — positions kept" if paused else (f"holding {len(open_pos)} open" if open_pos else None),
            "target_station": "screen" if (paused or open_pos) else "desk",
            "active_mint": open_pos[0].mint if open_pos else None,
            "active_symbol": sym,
            "last_heartbeat": risk_hb,
            "processed": risk_proc,
            "errors": risk_err,
        },
        {
            "seat": "CRAWLER",
            "subtitle": "reads first, then the desk trades",
            "badge": f"TAPS {evaluated}/{max(seen, evaluated, 1)}",
            "jev_connected": False,
            "decision_source": "deterministic_engine",
            "status": crawl_st,
            "metric_label": "observations",
            "metric_value": str(total_obs_count),
            "bar_ratio": min(1.0, evaluated / 8.0) if evaluated else 0.15,
            "stats": {
                "pages": total_obs_count,
                "posts_read": 0,
                "webs_spun": int(funnel_stats.get("webs_spun") or 0),
                "rugs_killed": total_sec_fails,
                "benched": active_bench_count,
            },
            "log_line": (
                f"swept {funnel_stats.get('pages_read', 0)} pools ({(crawl_ev.payload or {}).get('source', mode)})"
                if crawl_ev
                else "idle · awaiting scan cycle"
            ),
            "speech_bubble": f"swept {seen} mints",
            "target_station": "screen",
            "active_mint": featured["mint"] if featured else None,
            "active_symbol": sym,
            "last_heartbeat": crawl_hb,
            "processed": crawl_proc,
            "errors": crawl_err,
        },
    ]


def seat_inspector(seat_name: str, settings: Settings, swarm: Swarm) -> dict[str, Any] | None:
    seat_upper = seat_name.strip().upper()
    if seat_upper not in SEAT_ORDER:
        return None
    desk = desk_state(settings, swarm)
    seat_card = next((s for s in desk["seats"] if s["seat"] == seat_upper), None)
    agent_key = SEAT_AGENT_MAP[seat_upper]
    now = datetime.now(timezone.utc)

    with session_scope() as session:
        mode = _mode(session)
        health = session.get(AgentHealth, agent_key)
        all_recent = session.scalars(
            select(AgentEvent).where(AgentEvent.mode == mode).order_by(desc(AgentEvent.created_at)).limit(120)
        ).all()
        seat_events = [
            _event(row)
            for row in all_recent
            if (row.payload or {}).get("seat") == seat_upper or row.source_agent == agent_key
        ][:25]
        vetoes = [
            {
                "at": r.created_at.isoformat(),
                "mint": r.mint,
                "approved": r.approved,
                "reason_codes": r.reason_codes,
                "explanation": r.explanation,
            }
            for r in session.scalars(
                select(RiskDecision).where(RiskDecision.mode == mode).order_by(desc(RiskDecision.created_at)).limit(10)
            ).all()
        ]
        heartbeat_age = (
            round((now - health.last_heartbeat).total_seconds(), 1)
            if (health and health.last_heartbeat)
            else None
        )

    return {
        "seat": seat_upper,
        "logical_agent": agent_key,
        "card": seat_card,
        "heartbeat_age_seconds": heartbeat_age,
        "last_duration_ms": health.last_duration_ms if health else None,
        "last_error": health.last_error if health else None,
        "data_sources": {
            "mode": desk["mode"],
            "gmgn": desk["gmgn"],
            "jev": desk["jev"],
            "grok": desk["grok"],
        },
        "thresholds": {
            "hard": desk["thresholds"]["hard"],
            "soft": desk["thresholds"]["soft"],
        },
        "recent_vetoes": vetoes,
        "events": seat_events,
    }


def bench_list() -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        mode = _mode(session)
        rows = session.scalars(
            select(BenchEntry).where(BenchEntry.mode == mode).order_by(desc(BenchEntry.benched_at)).limit(50)
        ).all()
        return [
            {
                "mint": r.mint,
                "symbol": r.symbol,
                "reason": r.reason,
                "stage": r.stage,
                "ttl_minutes": r.ttl_minutes,
                "active": r.until_at > now,
                "benched_at": r.benched_at.isoformat(),
                "until_at": r.until_at.isoformat(),
                "details": r.details,
            }
            for r in rows
        ]


def shadow_list() -> dict[str, Any]:
    with session_scope() as session:
        mode = _mode(session)
        rows = session.scalars(
            select(ShadowDecision).where(ShadowDecision.mode == mode).order_by(desc(ShadowDecision.created_at)).limit(50)
        ).all()
        disagreements = sum(1 for r in rows if r.disagreement)
        return {
            "mode": mode,
            "total_logged": len(rows),
            "disagreements": disagreements,
            "rows": [
                {
                    "id": r.id,
                    "cycle_id": r.cycle_id,
                    "at": r.created_at.isoformat(),
                    "mint": r.mint,
                    "symbol": r.symbol,
                    "funnel_stage": r.funnel_stage,
                    "funnel_outcome": r.funnel_outcome,
                    "deterministic_outcome": r.deterministic_outcome,
                    "disagreement": r.disagreement,
                    "jev_model": r.jev_model,
                    "jev_answers": r.jev_answers,
                    "reasons": r.reasons,
                    "paper_notional": _s(r.paper_notional),
                }
                for r in rows
            ],
        }


def demo_playback() -> dict[str, Any]:
    return demo_playback_frames()


def portfolio(mode_override: str | None = None) -> dict:
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        mode = mode_override or _mode(session)
        book = _equity(session, mode, now)
        state = book.get("state")
        ledger = session.scalars(
            select(LedgerEntry).where(LedgerEntry.mode == mode).order_by(desc(LedgerEntry.id)).limit(30)
        ).all()
        return {
            "mode": mode,
            "cash": _s(state.cash) if state else None,
            "reserved": _s(state.reserved) if state else None,
            "equity": _s(book.get("equity")),
            "initial_equity": _s(state.initial_equity) if state else None,
            "peak_equity": _s(state.peak_equity) if state else None,
            "day_start_equity": _s(state.day_start_equity) if state else None,
            "day_utc": state.day_utc if state else None,
            "emergency_stop": bool(state.emergency_stop) if state else False,
            "ledger": [
                {
                    "id": row.id,
                    "at": row.created_at.isoformat(),
                    "kind": row.kind,
                    "amount_usdc": _s(row.amount_usdc),
                    "cash_after": _s(row.cash_after),
                    "memo": row.memo,
                }
                for row in ledger
            ],
        }


def positions(mode: str | None = None) -> list[dict]:
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        mode = mode or _mode(session)
        limits = limits_from_config(session.get(AppSetting, "runtime").value)
        rows = session.scalars(select(Position).where(Position.mode == mode).order_by(desc(Position.opened_at))).all()
        result = []
        for pos in rows:
            obs = session.scalar(
                select(MarketObservation)
                .where(MarketObservation.mode == mode, MarketObservation.mint == pos.mint)
                .order_by(desc(MarketObservation.observed_at))
            )
            token = session.scalar(select(Token).where(Token.mode == mode, Token.mint == pos.mint))
            mark = obs.price_usd if obs else None
            fresh = bool(
                obs and mark is not None and (now - obs.observed_at).total_seconds() <= limits.market_max_age_seconds
            )
            unrealized = None
            if pos.status == "open" and fresh and mark is not None:
                unrealized = q_usdc(mark * pos.qty - pos.cost_basis)
            result.append(
                {
                    "id": pos.id,
                    "mint": pos.mint,
                    "symbol": token.symbol if token else pos.mint,
                    "name": token.name if token else "",
                    "pool": pos.pool_address,
                    "status": pos.status,
                    "qty": _s(pos.qty),
                    "entry_price": _s(pos.avg_entry_price),
                    "mark_price": _s(mark) if fresh else None,
                    "mark_stale": not fresh,
                    "last_observed_price": _s(mark),
                    "cost_basis": _s(pos.cost_basis),
                    "unrealized_pnl": _s(unrealized),
                    "unrealized_available": unrealized is not None,
                    "realized_pnl": _s(pos.realized_pnl),
                    "fees": _s(pos.fees),
                    "opened_at": pos.opened_at.isoformat(),
                    "exit_conditions": pos.exit_conditions,
                    "exit_reason": pos.exit_reason,
                    "unsellable_reason": pos.unsellable_reason,
                    "data_label": obs.provider if obs else None,
                }
            )
        return result


def orders(mode: str | None = None) -> list[dict]:
    with session_scope() as session:
        mode = mode or _mode(session)
        rows = session.scalars(
            select(PaperOrder).where(PaperOrder.mode == mode).order_by(desc(PaperOrder.created_at)).limit(100)
        ).all()
        payload = []
        for row in rows:
            fills = session.scalars(select(PaperFill).where(PaperFill.order_id == row.id)).all()
            payload.append(
                {
                    "id": row.id,
                    "mint": row.mint,
                    "pool": row.pool_address,
                    "side": row.side,
                    "status": row.status,
                    "requested_notional": _s(row.requested_notional),
                    "filled_notional": _s(row.filled_notional),
                    "remaining_notional": _s(row.remaining_notional),
                    "reason": row.rejection_reason,
                    "created_at": row.created_at.isoformat(),
                    "quote": row.quote,
                    "fills": [
                        {
                            "qty": _s(fill.qty),
                            "price": _s(fill.price),
                            "notional": _s(fill.notional),
                            "fee": _s(fill.fee),
                            "fee_source": fill.fee_source,
                            "price_impact_bps": _s(fill.price_impact_bps),
                            "filled_at": fill.filled_at.isoformat(),
                        }
                        for fill in fills
                    ],
                }
            )
        return payload


def tokens(mode: str | None = None) -> list[dict]:
    with session_scope() as session:
        mode = mode or _mode(session)
        found = session.scalars(select(Token).where(Token.mode == mode)).all()
        rows = []
        for token in found:
            obs = session.scalar(
                select(MarketObservation)
                .where(MarketObservation.mode == mode, MarketObservation.mint == token.mint)
                .order_by(desc(MarketObservation.observed_at), desc(MarketObservation.discovery_score))
            )
            sec = session.scalar(
                select(SecurityAssessment)
                .where(SecurityAssessment.mode == mode, SecurityAssessment.mint == token.mint)
                .order_by(desc(SecurityAssessment.observed_at))
            )
            mom = session.scalar(
                select(AgentEvent)
                .where(AgentEvent.mode == mode, AgentEvent.mint == token.mint, AgentEvent.event_type == "momentum.report")
                .order_by(desc(AgentEvent.created_at))
            )
            proposal = session.scalar(
                select(StrategyProposal)
                .where(StrategyProposal.mode == mode, StrategyProposal.mint == token.mint)
                .order_by(desc(StrategyProposal.created_at))
            )
            rows.append(
                {
                    "mint": token.mint,
                    "symbol": token.symbol,
                    "name": token.name,
                    "promoted": token.promoted,
                    "liquidity": _s(obs.pool_liquidity_usd) if obs else None,
                    "market_cap": _s(obs.market_cap_usd) if obs else None,
                    "volume_m5": _s(obs.volume_m5) if obs else None,
                    "price": _s(obs.price_usd) if obs else None,
                    "price_change_m5": _s(obs.price_change_m5) if obs else None,
                    "discovery_score": obs.discovery_score if obs else None,
                    "momentum_score": (mom.payload or {}).get("score") if mom else None,
                    "security_status": sec.classification if sec else "unknown",
                    "candidate_status": proposal.status if proposal else "observed",
                    "provider": obs.provider if obs else None,
                    "observed_at": obs.observed_at.isoformat() if obs else None,
                    "data_label": obs.provider if obs else None,
                }
            )
        return rows


def token_detail(mint: str) -> dict | None:
    with session_scope() as session:
        mode = _mode(session)
        token = session.scalar(select(Token).where(Token.mode == mode, Token.mint == mint))
        if token is None:
            return None
        observations = session.scalars(
            select(MarketObservation)
            .where(MarketObservation.mode == mode, MarketObservation.mint == mint)
            .order_by(MarketObservation.observed_at)
        ).all()
        security = session.scalars(
            select(SecurityAssessment)
            .where(SecurityAssessment.mode == mode, SecurityAssessment.mint == mint)
            .order_by(desc(SecurityAssessment.observed_at))
        ).all()
        events_rows = session.scalars(
            select(AgentEvent).where(AgentEvent.mode == mode, AgentEvent.mint == mint).order_by(AgentEvent.created_at)
        ).all()
        decisions_rows = session.scalars(
            select(RiskDecision)
            .where(RiskDecision.mode == mode, RiskDecision.mint == mint)
            .order_by(desc(RiskDecision.created_at))
        ).all()
        proposals = session.scalars(
            select(StrategyProposal)
            .where(StrategyProposal.mode == mode, StrategyProposal.mint == mint)
            .order_by(desc(StrategyProposal.created_at))
        ).all()
        klines = session.scalars(
            select(KlineBar)
            .where(KlineBar.mode == mode, KlineBar.mint == mint)
            .order_by(KlineBar.bar_ts)
        ).all()
        latest = observations[-1] if observations else None
        return {
            "mint": token.mint,
            "symbol": token.symbol,
            "name": token.name,
            "decimals": token.decimals,
            "promoted": token.promoted,
            "mode": mode,
            "data_label": latest.provider if latest else None,
            "market": {
                "price": _s(latest.price_usd) if latest else None,
                "liquidity": _s(latest.pool_liquidity_usd) if latest else None,
                "token_liquidity": _s(latest.token_liquidity_usd) if latest else None,
                "market_cap": _s(latest.market_cap_usd) if latest else None,
                "fdv": _s(latest.fdv_usd) if latest else None,
                "volume_m5": _s(latest.volume_m5) if latest else None,
                "price_change_m5": _s(latest.price_change_m5) if latest else None,
                "provider": latest.provider if latest else None,
                "provider_reported_at": (
                    latest.provider_reported_at.isoformat() if latest and latest.provider_reported_at else None
                ),
                "observed_at": latest.observed_at.isoformat() if latest else None,
                "provenance": latest.provenance if latest else None,
            },
            "chart": [
                {
                    "at": row.observed_at.isoformat(),
                    "price": _s(row.price_usd),
                    "provider": row.provider,
                }
                for row in observations
                if row.price_usd is not None
            ],
            "candles": _candles_from_klines(klines) if klines else _candles_from_observations(observations),
            "security": [
                {
                    "at": row.observed_at.isoformat(),
                    "classification": row.classification,
                    "score": row.score,
                    "critical_flags": row.critical_flags,
                    "warning_flags": row.warning_flags,
                    "explanation": row.explanation,
                    "evidence": row.evidence,
                }
                for row in security
            ],
            "momentum": [row.payload for row in events_rows if row.event_type == "momentum.report"][-3:],
            "proposals": [
                {
                    "id": row.id,
                    "status": row.status,
                    "thesis": row.thesis,
                    "notional": _s(row.requested_notional),
                    "strategy": row.strategy_version,
                    "at": row.created_at.isoformat(),
                    "evidence": row.evidence,
                    "exit_conditions": row.exit_conditions,
                }
                for row in proposals
            ],
            "decisions": [
                {
                    "approved": row.approved,
                    "reason_codes": row.reason_codes,
                    "explanation": row.explanation,
                    "at": row.created_at.isoformat(),
                }
                for row in decisions_rows
            ],
            "events": [_event(row) for row in events_rows],
        }


def _event(row: AgentEvent) -> dict:
    return {
        "id": row.id,
        "at": row.created_at.isoformat(),
        "source": row.source_agent,
        "seat": (row.payload or {}).get("seat"),
        "type": row.event_type,
        "mint": row.mint,
        "correlation_id": row.correlation_id,
        "sequence": row.sequence,
        "status": row.status,
        "payload": row.payload,
    }


def agents() -> list[dict]:
    with session_scope() as session:
        rows = session.scalars(select(AgentHealth).order_by(AgentHealth.agent_name)).all()
        return [
            {
                "name": row.agent_name,
                "status": row.status,
                "last_heartbeat": row.last_heartbeat.isoformat() if row.last_heartbeat else None,
                "last_task": row.last_task,
                "last_duration_ms": row.last_duration_ms,
                "processed": row.processed_count,
                "errors": row.error_count,
                "last_error": row.last_error,
                "last_decision": row.last_decision,
            }
            for row in rows
        ]


def events(limit: int = 80) -> list[dict]:
    with session_scope() as session:
        mode = _mode(session)
        rows = session.scalars(
            select(AgentEvent).where(AgentEvent.mode == mode).order_by(desc(AgentEvent.created_at)).limit(limit)
        ).all()
        return [_event(row) for row in reversed(rows)]


def decisions() -> list[dict]:
    with session_scope() as session:
        mode = _mode(session)
        rows = session.scalars(
            select(RiskDecision).where(RiskDecision.mode == mode).order_by(desc(RiskDecision.created_at)).limit(100)
        ).all()
        return [
            {
                "id": row.id,
                "mint": row.mint,
                "approved": row.approved,
                "reason_codes": row.reason_codes,
                "explanation": row.explanation,
                "at": row.created_at.isoformat(),
                "correlation_id": row.correlation_id,
            }
            for row in rows
        ]


def analytics() -> dict:
    with session_scope() as session:
        mode = _mode(session)
        snaps = session.scalars(
            select(EquitySnapshot).where(EquitySnapshot.mode == mode).order_by(EquitySnapshot.created_at)
        ).all()
        closed = session.scalars(select(Position).where(Position.mode == mode, Position.status == "closed")).all()
        fills = session.scalars(select(PaperFill).where(PaperFill.mode == mode)).all()
        rejected = session.scalars(
            select(RiskDecision).where(RiskDecision.mode == mode, RiskDecision.approved == False)  # noqa: E712
        ).all()
        gains = [row.realized_pnl for row in closed if row.realized_pnl > 0]
        losses = [row.realized_pnl for row in closed if row.realized_pnl < 0]
        fees = [row.fee for row in fills]
        return {
            "mode": mode,
            "data_label": "demo fixture" if mode == "demo" else "provider data",
            "equity_curve": [
                {"at": row.created_at.isoformat(), "equity": _s(row.equity), "drawdown": _s(row.drawdown)}
                for row in snaps
            ],
            "equity_curve_available": bool(snaps),
            "win_rate": _s(Decimal(len(gains)) / Decimal(len(closed))) if closed else None,
            "win_rate_available": bool(closed),
            "profit_factor": _s(sum(gains, Decimal("0")) / abs(sum(losses, Decimal("0")))) if gains and losses else None,
            "profit_factor_available": bool(gains and losses),
            "average_gain": _s(sum(gains, Decimal("0")) / Decimal(len(gains))) if gains else None,
            "average_gain_available": bool(gains),
            "average_loss": _s(sum(losses, Decimal("0")) / Decimal(len(losses))) if losses else None,
            "average_loss_available": bool(losses),
            "fee_impact": _s(sum(fees, Decimal("0"))) if fees else None,
            "fee_impact_available": bool(fees),
            "slippage_bps": [_s(row.price_impact_bps) for row in fills if row.price_impact_bps is not None],
            "rejected": [
                {"mint": row.mint, "reasons": row.reason_codes, "at": row.created_at.isoformat()}
                for row in rejected
            ],
            "closed_trades": len(closed),
        }


def settings_view(settings: Settings) -> dict:
    with session_scope() as session:
        config = session.get(AppSetting, "runtime").value
        history = session.scalars(select(ConfigVersion).order_by(desc(ConfigVersion.id)).limit(10)).all()
        public = {
            "mode": config.get("mode"),
            "scanner": config.get("scanner"),
            "strategy": config.get("strategy"),
            "risk": config.get("risk"),
            "thresholds": config.get("thresholds") or {
                "hard": DEFAULT_THRESHOLDS["hard"],
                "soft": DEFAULT_THRESHOLDS["soft"],
            },
            "simulation": config.get("simulation"),
            "ai": {
                "hourly_budget": (config.get("ai") or {}).get("hourly_budget"),
                "configured": bool(settings.xai_api_key),
                "model": settings.xai_model if settings.xai_api_key else None,
                "jev_configured": bool(settings.typesafe_api_key),
                "jev_model": settings.typesafe_model if settings.typesafe_api_key else None,
            },
            "providers": {
                "gmgn_configured": bool(settings.gmgn_api_key),
                "supabase_configured": settings.supabase_configured,
                "typesafe_jev_configured": bool(settings.enable_optional_jev and settings.typesafe_api_key),
                "jupiter_configured": bool(settings.jupiter_api_key),
                "birdeye_configured": bool(settings.birdeye_api_key),
                "solana_rpc_configured": bool(settings.solana_rpc_url),
                "xai_configured": bool(settings.xai_api_key),
            },
            "history": [
                {"at": row.created_at.isoformat(), "actor": row.actor, "note": row.note}
                for row in history
            ],
        }
        return public


def providers() -> list[dict]:
    with session_scope() as session:
        return [_provider(row) for row in session.scalars(select(ProviderHealth)).all()]


def alerts() -> list[dict]:
    with session_scope() as session:
        mode = _mode(session)
        rows = session.scalars(
            select(Alert).where(Alert.mode == mode).order_by(desc(Alert.created_at)).limit(50)
        ).all()
        return [
            {
                "id": row.id,
                "at": row.created_at.isoformat(),
                "severity": row.severity,
                "code": row.code,
                "message": row.message,
                "mint": row.mint,
                "acknowledged": row.acknowledged,
            }
            for row in rows
        ]


def backtests() -> list[dict]:
    with session_scope() as session:
        rows = session.scalars(select(BacktestRun).order_by(desc(BacktestRun.created_at)).limit(20)).all()
        return [
            {
                "id": row.id,
                "at": row.created_at.isoformat(),
                "status": row.status,
                "strategy": row.strategy_version,
                "disclosure": row.disclosure,
                "ending_cash": (row.results or {}).get("ending_cash"),
                "point_in_time_security": (row.results or {}).get("point_in_time_security"),
            }
            for row in rows
        ]


def backtest_detail(run_id: str) -> dict | None:
    with session_scope() as session:
        row = session.get(BacktestRun, run_id)
        if row is None:
            return None
        return {
            "id": row.id,
            "at": row.created_at.isoformat(),
            "status": row.status,
            "params": row.params,
            "disclosure": row.disclosure,
            "results": row.results,
            "strategy": row.strategy_version,
        }


def coordination_state(swarm: Swarm) -> dict[str, Any]:
    with session_scope() as session:
        mode = _mode(session)
        findings = session.scalars(
            select(SwarmAgentFinding).where(SwarmAgentFinding.mode == mode).order_by(desc(SwarmAgentFinding.created_at)).limit(40)
        ).all()
        decisions_rows = session.scalars(
            select(SwarmStructuredDecision).where(SwarmStructuredDecision.mode == mode).order_by(desc(SwarmStructuredDecision.created_at)).limit(40)
        ).all()
        hbs = session.scalars(select(SwarmAgentHeartbeat).order_by(SwarmAgentHeartbeat.seat)).all()
    return {
        "mode": mode,
        "tasks": swarm.coord.list_pending_tasks(mode=mode, limit=30),
        "heartbeats": [
            {
                "seat": h.seat,
                "status": h.status,
                "decision_source": h.decision_source,
                "active_mint": h.active_mint,
                "active_symbol": h.active_symbol,
                "agent_id": h.agent_id,
                "note": h.note,
                "last_seen_at": h.last_seen_at.isoformat(),
                "expires_at": h.expires_at.isoformat(),
            }
            for h in hbs
        ],
        "findings": [
            {
                "id": f.id,
                "seat": f.seat,
                "mint": f.mint,
                "symbol": f.symbol,
                "verdict": f.verdict,
                "decision_source": f.decision_source,
                "confidence": _s(f.confidence),
                "score": f.score,
                "summary": f.summary,
                "agent_id": f.agent_id,
                "created_at": f.created_at.isoformat(),
            }
            for f in findings
        ],
        "decisions": [
            {
                "id": d.id,
                "cycle_id": d.cycle_id,
                "mint": d.mint,
                "symbol": d.symbol,
                "action": d.action,
                "decision_source": d.decision_source,
                "confidence": _s(d.confidence),
                "size_factor": _s(d.size_factor),
                "thesis": d.thesis,
                "reasons": d.reasons,
                "risk_approved": d.risk_approved,
                "risk_reason_codes": d.risk_reason_codes,
                "paper_order_id": d.paper_order_id,
                "created_at": d.created_at.isoformat(),
            }
            for d in decisions_rows
        ],
    }
