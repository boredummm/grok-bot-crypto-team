from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.analysis import AuthorityFacts, classify_security, dedupe_by_mint
from app.db import init_engine, session_scope
from app.demo import demo_observations
from app.models import (
    AgentEvent,
    LiquidityPool,
    PaperOrder,
    PortfolioState,
    Position,
    RiskDecision,
)
from app.replay import run_replay
import json
from pathlib import Path


NOW = datetime(2026, 3, 1, 15, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_demo_cycle_dedupes_and_trades_only_eligible_token(swarm):
    result = await swarm.run_cycle(now=NOW)
    assert result["candidates"] >= 4
    with session_scope() as session:
        pools = session.scalars(select(LiquidityPool).where(LiquidityPool.mint == "demo:momentum")).all()
        assert {row.pool_address for row in pools} == {"demo-pool-demo-mom", "demo-pool-demo-mom-b"}
        orders = session.scalars(select(PaperOrder).where(PaperOrder.mode == "demo")).all()
        assert len(orders) == 1
        assert orders[0].mint == "demo:momentum"
        assert orders[0].status == "filled"
        assert orders[0].quote["source"] == "demo_fixture"
        position = session.scalars(select(Position)).one()
        assert position.status == "open"
        assert position.qty > 0
        state = session.get(PortfolioState, "demo")
        assert state.cash < state.initial_equity
        assert state.cash > 0
        freeze = session.scalar(
            select(RiskDecision).where(RiskDecision.mint == "demo:freeze")
        )
        assert freeze is None or freeze.approved is False
        events = session.scalars(
            select(AgentEvent).where(AgentEvent.mint == "demo:momentum").order_by(AgentEvent.sequence)
        ).all()
        sequences = [row.sequence for row in events]
        assert sequences == sorted(sequences)
        assert "discovery.candidate" in {row.event_type for row in events}
        assert "execution.fill" in {row.event_type for row in events}


@pytest.mark.asyncio
async def test_second_cycle_is_blocked_by_risk_and_restart_keeps_state(swarm, settings, tmp_path):
    await swarm.run_cycle(now=NOW)
    await swarm.run_cycle(now=NOW + timedelta(seconds=5))
    with session_scope() as session:
        decisions = session.scalars(select(RiskDecision).where(RiskDecision.mint == "demo:momentum")).all()
        assert any(row.approved for row in decisions)
        assert any((not row.approved) and "DUPLICATE_ORDER" in row.reason_codes for row in decisions)
        state = session.get(PortfolioState, "demo")
        cash = state.cash
        day = state.day_utc
        peak = state.peak_equity
    stopped = swarm.emergency_stop("test pause", actor="test")
    assert stopped["positions_liquidated"] is False or stopped["open_positions"] == 1
    with session_scope() as session:
        state = session.get(PortfolioState, "demo")
        assert state.emergency_stop is True
        open_positions = session.scalars(select(Position).where(Position.status == "open")).all()
        assert len(open_positions) == 1
    init_engine(settings)
    swarm.bootstrap()
    with session_scope() as session:
        state = session.get(PortfolioState, "demo")
        assert state.emergency_stop is True
        assert state.cash == cash
        assert state.day_utc == day
        assert state.peak_equity == peak
        assert session.scalar(select(func.count()).select_from(Position).where(Position.status == "open")) == 1


@pytest.mark.asyncio
async def test_agent_timeout_holds(swarm):
    swarm.momentum_delay = 1.5
    swarm.settings.agent_timeout_seconds = 0.05
    await swarm.run_cycle(now=NOW)
    with session_scope() as session:
        holds = session.scalars(select(AgentEvent).where(AgentEvent.event_type == "orchestrator.hold")).all()
        assert holds
        assert all(row.payload.get("reason") == "agent_timeout" for row in holds)
        assert session.scalar(select(func.count()).select_from(PaperOrder)) == 0


def test_security_fail_closed_and_discovery_dedupe():
    rows = demo_observations(NOW)
    deduped = dedupe_by_mint(rows)
    assert len([row for row in deduped if row.mint == "demo:momentum"]) == 1
    assert len(deduped) == len({row.mint for row in rows})
    report = classify_security(
        AuthorityFacts(
            source="unit",
            observed_at=NOW,
            token_program="TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
            mint_authority_active=False,
            freeze_authority_active=True,
            extensions=[],
            top_holder_pct=Decimal("4"),
            malicious_flag=False,
            parsed=True,
        ),
        liquidity_usd=Decimal("80000"),
        min_liquidity=Decimal("50000"),
        exit_route=True,
        observed_at=NOW,
        max_age_seconds=300,
    )
    assert report.classification == "fail"
    assert "active_freeze_authority" in report.critical_flags
    unknown = classify_security(
        None,
        liquidity_usd=Decimal("80000"),
        min_liquidity=Decimal("50000"),
        exit_route=True,
        observed_at=NOW,
        max_age_seconds=300,
    )
    assert unknown.classification == "unknown"


def test_replay_does_not_leak_future_bars():
    dataset = json.loads((Path(__file__).resolve().parents[1] / "app" / "data" / "replay_demo.json").read_text())
    result = run_replay(dataset, {})
    assert result["point_in_time_security"] is False
    assert result["unverified_decisions"] >= 1
    used = [row for row in result["decisions"] if row["mint"] == "demo:replay" and row["ts"].startswith("2026-01-01T00:20")]
    assert used
    assert used[0]["visible_closes"] == ["1.00", "1.05"]
    assert "1.30" not in used[0]["visible_closes"]
    assert used[0]["hidden_future_count"] == 1


@pytest.mark.asyncio
async def test_unsellable_when_exit_quote_missing(swarm):
    from app.models import MarketObservation

    await swarm.run_cycle(now=NOW)
    with session_scope() as session:
        position = session.scalars(select(Position)).one()
        position.mode = "live"
        state_demo = session.get(PortfolioState, "demo")
        cash_before = state_demo.cash
        session.add(
            MarketObservation(
                mode="live",
                mint=position.mint,
                pool_address=position.pool_address,
                provider="unit",
                provider_reported_at=NOW,
                observed_at=NOW + timedelta(minutes=1),
                price_usd=Decimal("0.90"),
                pool_liquidity_usd=Decimal("80000"),
                promoted=False,
            )
        )
        position_id = position.id
        qty = position.qty
    swarm.update_settings({"mode": "live"}, actor="test")
    from app.swarm import limits_from_config

    with session_scope() as session:
        limits = limits_from_config(swarm._config(session))
    await swarm._monitor_exits(NOW + timedelta(minutes=1), "live", limits)
    with session_scope() as session:
        current = session.get(Position, position_id)
        assert current.status == "unsellable"
        assert current.qty == qty
        assert "not marked closed" in (current.unsellable_reason or "")
        assert session.get(PortfolioState, "demo").cash == cash_before
