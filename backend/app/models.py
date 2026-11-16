"""Persistent records. Trading state is mode-partitioned so demo never mixes into live."""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, DecimalString, JSONText, UTCDateTime


class SchemaMigration(Base):
    __tablename__ = "schema_migrations"
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    applied_at: Mapped[object] = mapped_column(UTCDateTime)


class Token(Base):
    __tablename__ = "tokens"
    __table_args__ = (UniqueConstraint("mode", "mint", name="uq_token_mode_mint"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mode: Mapped[str] = mapped_column(String(8), index=True)
    mint: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(160))
    symbol: Mapped[str] = mapped_column(String(32))
    decimals: Mapped[int] = mapped_column(Integer, default=6)
    token_program: Mapped[str | None] = mapped_column(String(64), nullable=True)
    holder_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    promoted: Mapped[bool] = mapped_column(Boolean, default=False)
    first_seen_at: Mapped[object] = mapped_column(UTCDateTime)
    token_created_at: Mapped[object | None] = mapped_column(UTCDateTime, nullable=True)


class LiquidityPool(Base):
    __tablename__ = "liquidity_pools"
    __table_args__ = (UniqueConstraint("mode", "pool_address", name="uq_pool_mode_address"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mode: Mapped[str] = mapped_column(String(8), index=True)
    pool_address: Mapped[str] = mapped_column(String(80), index=True)
    mint: Mapped[str] = mapped_column(String(64), index=True)
    quote_mint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dex: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pool_created_at: Mapped[object | None] = mapped_column(UTCDateTime, nullable=True)
    first_seen_at: Mapped[object] = mapped_column(UTCDateTime)


class MarketObservation(Base):
    __tablename__ = "market_observations"
    __table_args__ = (Index("ix_obs_mint_time", "mode", "mint", "observed_at"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mode: Mapped[str] = mapped_column(String(8), index=True)
    mint: Mapped[str] = mapped_column(String(64), index=True)
    pool_address: Mapped[str] = mapped_column(String(80))
    provider: Mapped[str] = mapped_column(String(40))
    provider_reported_at: Mapped[object | None] = mapped_column(UTCDateTime, nullable=True)
    observed_at: Mapped[object] = mapped_column(UTCDateTime, index=True)
    price_usd: Mapped[object | None] = mapped_column(DecimalString, nullable=True)
    market_cap_usd: Mapped[object | None] = mapped_column(DecimalString, nullable=True)
    fdv_usd: Mapped[object | None] = mapped_column(DecimalString, nullable=True)
    pool_liquidity_usd: Mapped[object | None] = mapped_column(DecimalString, nullable=True)
    token_liquidity_usd: Mapped[object | None] = mapped_column(DecimalString, nullable=True)
    volume_m5: Mapped[object | None] = mapped_column(DecimalString, nullable=True)
    volume_h1: Mapped[object | None] = mapped_column(DecimalString, nullable=True)
    price_change_m5: Mapped[object | None] = mapped_column(DecimalString, nullable=True)
    price_change_h1: Mapped[object | None] = mapped_column(DecimalString, nullable=True)
    buys_m5: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sells_m5: Mapped[int | None] = mapped_column(Integer, nullable=True)
    buys_h1: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sells_h1: Mapped[int | None] = mapped_column(Integer, nullable=True)
    unique_buyers_m5: Mapped[int | None] = mapped_column(Integer, nullable=True)
    discovery_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    promoted: Mapped[bool] = mapped_column(Boolean, default=False)
    provenance: Mapped[dict | None] = mapped_column(JSONText, nullable=True)


class SecurityAssessment(Base):
    __tablename__ = "security_assessments"
    __table_args__ = (Index("ix_sec_mint_time", "mode", "mint", "observed_at"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mode: Mapped[str] = mapped_column(String(8), index=True)
    mint: Mapped[str] = mapped_column(String(64), index=True)
    observed_at: Mapped[object] = mapped_column(UTCDateTime)
    classification: Mapped[str] = mapped_column(String(16))
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    critical_flags: Mapped[list] = mapped_column(JSONText)
    warning_flags: Mapped[list] = mapped_column(JSONText)
    evidence: Mapped[list] = mapped_column(JSONText)
    explanation: Mapped[str] = mapped_column(Text)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class AgentEvent(Base):
    __tablename__ = "agent_events"
    __table_args__ = (Index("ix_event_corr", "correlation_id", "created_at"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[object] = mapped_column(UTCDateTime, index=True)
    source_agent: Mapped[str] = mapped_column(String(40), index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    mint: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    payload: Mapped[dict] = mapped_column(JSONText)
    correlation_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    mode: Mapped[str] = mapped_column(String(8), index=True)
    sequence: Mapped[int] = mapped_column(Integer, default=0)


class StrategyProposal(Base):
    __tablename__ = "strategy_proposals"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    mode: Mapped[str] = mapped_column(String(8), index=True)
    mint: Mapped[str] = mapped_column(String(64), index=True)
    pool_address: Mapped[str] = mapped_column(String(80))
    side: Mapped[str] = mapped_column(String(8))
    requested_notional: Mapped[object] = mapped_column(DecimalString)
    strategy_version: Mapped[str] = mapped_column(String(64))
    thesis: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict] = mapped_column(JSONText)
    expected_costs: Mapped[dict] = mapped_column(JSONText)
    exit_conditions: Mapped[dict] = mapped_column(JSONText)
    created_at: Mapped[object] = mapped_column(UTCDateTime)
    expires_at: Mapped[object] = mapped_column(UTCDateTime)
    status: Mapped[str] = mapped_column(String(16), index=True)
    correlation_id: Mapped[str] = mapped_column(String(64), index=True)


class RiskDecision(Base):
    __tablename__ = "risk_decisions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    proposal_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("strategy_proposals.id"), nullable=True)
    mode: Mapped[str] = mapped_column(String(8), index=True)
    mint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved: Mapped[bool] = mapped_column(Boolean)
    reason_codes: Mapped[list] = mapped_column(JSONText)
    explanation: Mapped[str] = mapped_column(Text)
    limits_snapshot: Mapped[dict] = mapped_column(JSONText)
    created_at: Mapped[object] = mapped_column(UTCDateTime, index=True)
    correlation_id: Mapped[str] = mapped_column(String(64), index=True)


class PaperOrder(Base):
    __tablename__ = "paper_orders"
    __table_args__ = (UniqueConstraint("client_order_id", name="uq_client_order"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_order_id: Mapped[str] = mapped_column(String(80))
    mode: Mapped[str] = mapped_column(String(8), index=True)
    mint: Mapped[str] = mapped_column(String(64), index=True)
    pool_address: Mapped[str] = mapped_column(String(80))
    side: Mapped[str] = mapped_column(String(8))
    requested_notional: Mapped[object] = mapped_column(DecimalString)
    status: Mapped[str] = mapped_column(String(16), index=True)
    proposal_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    risk_decision_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    quote: Mapped[dict | None] = mapped_column(JSONText, nullable=True)
    filled_notional: Mapped[object] = mapped_column(DecimalString, default="0")
    remaining_notional: Mapped[object] = mapped_column(DecimalString, default="0")
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(UTCDateTime, index=True)
    updated_at: Mapped[object] = mapped_column(UTCDateTime)
    correlation_id: Mapped[str] = mapped_column(String(64))
    position_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class PaperFill(Base):
    __tablename__ = "paper_fills"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    order_id: Mapped[str] = mapped_column(String(64), ForeignKey("paper_orders.id"), index=True)
    mode: Mapped[str] = mapped_column(String(8), index=True)
    qty: Mapped[object] = mapped_column(DecimalString)
    price: Mapped[object] = mapped_column(DecimalString)
    notional: Mapped[object] = mapped_column(DecimalString)
    fee: Mapped[object] = mapped_column(DecimalString)
    fee_source: Mapped[str] = mapped_column(String(24))
    price_impact_bps: Mapped[object | None] = mapped_column(DecimalString, nullable=True)
    filled_at: Mapped[object] = mapped_column(UTCDateTime)


class Position(Base):
    __tablename__ = "positions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    mode: Mapped[str] = mapped_column(String(8), index=True)
    mint: Mapped[str] = mapped_column(String(64), index=True)
    pool_address: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(16), index=True)
    qty: Mapped[object] = mapped_column(DecimalString)
    avg_entry_price: Mapped[object] = mapped_column(DecimalString)
    cost_basis: Mapped[object] = mapped_column(DecimalString)
    realized_pnl: Mapped[object] = mapped_column(DecimalString, default="0")
    fees: Mapped[object] = mapped_column(DecimalString, default="0")
    opened_at: Mapped[object] = mapped_column(UTCDateTime)
    closed_at: Mapped[object | None] = mapped_column(UTCDateTime, nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    unsellable_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    strategy_version: Mapped[str] = mapped_column(String(64))
    exit_conditions: Mapped[dict] = mapped_column(JSONText)
    high_water_price: Mapped[object | None] = mapped_column(DecimalString, nullable=True)


class LedgerEntry(Base):
    __tablename__ = "portfolio_ledger"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mode: Mapped[str] = mapped_column(String(8), index=True)
    created_at: Mapped[object] = mapped_column(UTCDateTime, index=True)
    kind: Mapped[str] = mapped_column(String(32))
    amount_usdc: Mapped[object] = mapped_column(DecimalString)
    cash_after: Mapped[object] = mapped_column(DecimalString)
    ref: Mapped[str | None] = mapped_column(String(80), nullable=True)
    memo: Mapped[str] = mapped_column(Text)


class EquitySnapshot(Base):
    __tablename__ = "equity_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mode: Mapped[str] = mapped_column(String(8), index=True)
    created_at: Mapped[object] = mapped_column(UTCDateTime, index=True)
    equity: Mapped[object] = mapped_column(DecimalString)
    cash: Mapped[object] = mapped_column(DecimalString)
    exposure: Mapped[object] = mapped_column(DecimalString)
    drawdown: Mapped[object] = mapped_column(DecimalString)
    marks_fresh: Mapped[bool] = mapped_column(Boolean, default=True)


class PortfolioState(Base):
    __tablename__ = "portfolio_state"
    mode: Mapped[str] = mapped_column(String(8), primary_key=True)
    cash: Mapped[object] = mapped_column(DecimalString)
    reserved: Mapped[object] = mapped_column(DecimalString, default="0")
    peak_equity: Mapped[object] = mapped_column(DecimalString)
    day_start_equity: Mapped[object] = mapped_column(DecimalString)
    day_utc: Mapped[str] = mapped_column(String(10))
    emergency_stop: Mapped[bool] = mapped_column(Boolean, default=False)
    emergency_stop_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    initial_equity: Mapped[object] = mapped_column(DecimalString)
    updated_at: Mapped[object] = mapped_column(UTCDateTime)


class AgentHealth(Base):
    __tablename__ = "agent_health"
    agent_name: Mapped[str] = mapped_column(String(40), primary_key=True)
    status: Mapped[str] = mapped_column(String(16))
    last_heartbeat: Mapped[object | None] = mapped_column(UTCDateTime, nullable=True)
    last_task: Mapped[str | None] = mapped_column(String(160), nullable=True)
    last_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    processed_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_decision: Mapped[str | None] = mapped_column(Text, nullable=True)


class ConfigVersion(Base):
    __tablename__ = "configuration_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[object] = mapped_column(UTCDateTime, index=True)
    actor: Mapped[str] = mapped_column(String(40))
    document: Mapped[dict] = mapped_column(JSONText)
    note: Mapped[str] = mapped_column(Text, default="")


class AppSetting(Base):
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONText)
    updated_at: Mapped[object] = mapped_column(UTCDateTime)


class ProviderHealth(Base):
    __tablename__ = "provider_health"
    provider: Mapped[str] = mapped_column(String(40), primary_key=True)
    status: Mapped[str] = mapped_column(String(24))
    configured: Mapped[bool] = mapped_column(Boolean, default=False)
    last_ok_at: Mapped[object | None] = mapped_column(UTCDateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    circuit: Mapped[str] = mapped_column(String(16), default="closed")
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)


class Alert(Base):
    __tablename__ = "system_alerts"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[object] = mapped_column(UTCDateTime, index=True)
    severity: Mapped[str] = mapped_column(String(16))
    code: Mapped[str] = mapped_column(String(64), index=True)
    message: Mapped[str] = mapped_column(Text)
    mint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mode: Mapped[str] = mapped_column(String(8))
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)


class BacktestRun(Base):
    __tablename__ = "backtest_runs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[object] = mapped_column(UTCDateTime, index=True)
    status: Mapped[str] = mapped_column(String(16))
    params: Mapped[dict] = mapped_column(JSONText)
    disclosure: Mapped[dict] = mapped_column(JSONText)
    results: Mapped[dict] = mapped_column(JSONText)
    strategy_version: Mapped[str] = mapped_column(String(64))


class BenchEntry(Base):
    """Reason-TTL rejection bench so failed checks are remembered across cycles."""

    __tablename__ = "bench_entries"
    __table_args__ = (UniqueConstraint("mode", "mint", name="uq_bench_mode_mint"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mode: Mapped[str] = mapped_column(String(8), index=True)
    mint: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(String(64), index=True)
    stage: Mapped[str] = mapped_column(String(32), index=True)
    ttl_minutes: Mapped[int] = mapped_column(Integer)
    benched_at: Mapped[object] = mapped_column(UTCDateTime)
    until_at: Mapped[object] = mapped_column(UTCDateTime, index=True)
    details: Mapped[dict | None] = mapped_column(JSONText, nullable=True)


class FunnelRun(Base):
    """Per-cycle funnel counters (`free_kill`, `trade_kill`, `chain_kill`, `soft_kill`, `pick`)."""

    __tablename__ = "funnel_runs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    mode: Mapped[str] = mapped_column(String(8), index=True)
    created_at: Mapped[object] = mapped_column(UTCDateTime, index=True)
    held_symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    shadow_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    jev_connected: Mapped[bool] = mapped_column(Boolean, default=False)
    jev_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    gmgn_configured: Mapped[bool] = mapped_column(Boolean, default=False)
    stats: Mapped[dict] = mapped_column(JSONText)
    thresholds_snapshot: Mapped[dict] = mapped_column(JSONText)


class ShadowDecision(Base):
    """Shadow comparison record logging funnel/JEV judgement next to deterministic rules."""

    __tablename__ = "shadow_decisions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    mode: Mapped[str] = mapped_column(String(8), index=True)
    cycle_id: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[object] = mapped_column(UTCDateTime, index=True)
    mint: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(32))
    funnel_stage: Mapped[str] = mapped_column(String(32))
    funnel_outcome: Mapped[str] = mapped_column(String(32))
    deterministic_outcome: Mapped[str] = mapped_column(String(32))
    disagreement: Mapped[bool] = mapped_column(Boolean, default=False)
    jev_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    jev_answers: Mapped[dict | None] = mapped_column(JSONText, nullable=True)
    reasons: Mapped[list] = mapped_column(JSONText)
    paper_notional: Mapped[object | None] = mapped_column(DecimalString, nullable=True)
