"""Read models for the dashboard and the monitor API. Secrets are never included."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import desc, func, select

from app.accounting import position_mark_value
from app.config import Settings
from app.db import session_scope
from app.models import (
    AgentEvent,
    AgentHealth,
    Alert,
    AppSetting,
    ConfigVersion,
    EquitySnapshot,
    LedgerEntry,
    MarketObservation,
    PaperFill,
    PaperOrder,
    PortfolioState,
    Position,
    ProviderHealth,
    RiskDecision,
    SecurityAssessment,
    StrategyProposal,
    Token,
)
from app.money import decimal_str, q_usdc
from app.risk import RiskLimits
from app.swarm import Swarm, limits_from_config
from app.tailscale_status import tailscale_health


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
    positions = session.scalars(select(Position).where(Position.mode == mode, Position.status.in_(("open", "unsellable")))).all()
    exposure = Decimal("0")
    fresh = True
    for pos in positions:
        obs = session.scalar(
            select(MarketObservation)
            .where(MarketObservation.mode == mode, MarketObservation.mint == pos.mint)
            .order_by(desc(MarketObservation.observed_at))
        )
        mark = obs.price_usd if obs else None
        mark_fresh = bool(obs and mark is not None and (now - obs.observed_at).total_seconds() <= limits.market_max_age_seconds)
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
            profit_factor = (sum((row.realized_pnl for row in wins), Decimal("0")) / abs(sum((row.realized_pnl for row in losses), Decimal("0")))) if wins else Decimal("0")
        agents = session.scalars(select(AgentHealth)).all()
        active = sum(1 for row in agents if row.status in {"ok", "idle", "running"})
        providers = session.scalars(select(ProviderHealth)).all()
        last_scan = session.scalar(select(func.max(MarketObservation.observed_at)).where(MarketObservation.mode == mode))
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
                session.scalars(select(Position).where(Position.mode == mode, Position.status.in_(("open", "unsellable")))).all()
            ),
            "active_agents": active,
            "provider_health": [_provider(row) for row in providers],
            "tailscale": tailscale_health(),
            "last_scan_at": last_scan.isoformat() if last_scan else None,
            "alerts_open": int(open_alerts or 0),
            "ai_commentary": "unavailable" if not settings.xai_api_key else "configured",
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
            fresh = bool(obs and mark is not None and (now - obs.observed_at).total_seconds() <= limits.market_max_age_seconds)
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
        rows = session.scalars(select(PaperOrder).where(PaperOrder.mode == mode).order_by(desc(PaperOrder.created_at)).limit(100)).all()
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
                .order_by(desc(MarketObservation.observed_at))
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
            select(SecurityAssessment).where(SecurityAssessment.mode == mode, SecurityAssessment.mint == mint).order_by(desc(SecurityAssessment.observed_at))
        ).all()
        events = session.scalars(
            select(AgentEvent).where(AgentEvent.mode == mode, AgentEvent.mint == mint).order_by(AgentEvent.created_at)
        ).all()
        decisions = session.scalars(
            select(RiskDecision).where(RiskDecision.mode == mode, RiskDecision.mint == mint).order_by(desc(RiskDecision.created_at))
        ).all()
        proposals = session.scalars(
            select(StrategyProposal).where(StrategyProposal.mode == mode, StrategyProposal.mint == mint).order_by(desc(StrategyProposal.created_at))
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
                "provider_reported_at": latest.provider_reported_at.isoformat() if latest and latest.provider_reported_at else None,
                "observed_at": latest.observed_at.isoformat() if latest else None,
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
            "momentum": [row.payload for row in events if row.event_type == "momentum.report"][-3:],
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
                for row in decisions
            ],
            "events": [_event(row) for row in events],
        }


def _event(row: AgentEvent) -> dict:
    return {
        "id": row.id,
        "at": row.created_at.isoformat(),
        "source": row.source_agent,
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
        rows = session.scalars(select(RiskDecision).where(RiskDecision.mode == mode).order_by(desc(RiskDecision.created_at)).limit(100)).all()
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
        rejected = session.scalars(select(RiskDecision).where(RiskDecision.mode == mode, RiskDecision.approved == False)).all()  # noqa: E712
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
            "simulation": config.get("simulation"),
            "ai": {
                "hourly_budget": (config.get("ai") or {}).get("hourly_budget"),
                "configured": bool(settings.xai_api_key),
                "model": settings.xai_model if settings.xai_api_key else None,
            },
            "providers": {
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
        rows = session.scalars(select(Alert).where(Alert.mode == mode).order_by(desc(Alert.created_at)).limit(50)).all()
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
    from app.models import BacktestRun

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
    from app.models import BacktestRun

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
