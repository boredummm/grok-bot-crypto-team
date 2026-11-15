"""Deterministic risk veto. This module does not place orders or call providers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum

from app.money import D, q_usdc


class Reason(str, Enum):
    EMERGENCY_STOP = "EMERGENCY_STOP"
    DAILY_LOSS_CUTOFF = "DAILY_LOSS_CUTOFF"
    DRAWDOWN_CUTOFF = "DRAWDOWN_CUTOFF"
    MAX_POSITIONS = "MAX_POSITIONS"
    MAX_EXPOSURE = "MAX_EXPOSURE"
    POSITION_SIZE = "POSITION_SIZE"
    INSUFFICIENT_CASH = "INSUFFICIENT_CASH"
    STALE_QUOTE = "STALE_QUOTE"
    MISSING_QUOTE = "MISSING_QUOTE"
    STALE_MARKET_DATA = "STALE_MARKET_DATA"
    MISSING_MARKET_DATA = "MISSING_MARKET_DATA"
    SECURITY_FAIL = "SECURITY_FAIL"
    SECURITY_UNKNOWN = "SECURITY_UNKNOWN"
    STALE_SECURITY = "STALE_SECURITY"
    LIQUIDITY = "LIQUIDITY"
    POOL_AGE = "POOL_AGE"
    PRICE_IMPACT = "PRICE_IMPACT"
    SLIPPAGE = "SLIPPAGE"
    FEES = "FEES"
    COOLDOWN = "COOLDOWN"
    DUPLICATE_ORDER = "DUPLICATE_ORDER"
    PROVIDER_UNHEALTHY = "PROVIDER_UNHEALTHY"
    SHORT_NOT_ALLOWED = "SHORT_NOT_ALLOWED"
    EXPIRED_PROPOSAL = "EXPIRED_PROPOSAL"
    MODE_MISMATCH = "MODE_MISMATCH"
    MISSING_RISK_INPUT = "MISSING_RISK_INPUT"


@dataclass(frozen=True)
class RiskLimits:
    max_new_trade_usd: Decimal = Decimal("20")
    max_concurrent_positions: int = 3
    max_exposure_pct: Decimal = Decimal("0.06")
    daily_loss_pct: Decimal = Decimal("0.03")
    drawdown_pct: Decimal = Decimal("0.10")
    max_position_pct: Decimal = Decimal("0.02")
    min_pool_liquidity_usd: Decimal = Decimal("50000")
    min_pool_age_minutes: int = 15
    max_slippage_bps: Decimal = Decimal("150")
    max_price_impact_bps: Decimal = Decimal("200")
    max_fee_bps: Decimal = Decimal("300")
    quote_max_age_seconds: int = 20
    market_max_age_seconds: int = 90
    security_max_age_seconds: int = 300
    cooldown_seconds: int = 600
    max_pool_participation: Decimal = Decimal("0.01")

    def snapshot(self) -> dict:
        return {
            "max_new_trade_usd": format(self.max_new_trade_usd, "f"),
            "max_concurrent_positions": self.max_concurrent_positions,
            "max_exposure_pct": format(self.max_exposure_pct, "f"),
            "daily_loss_pct": format(self.daily_loss_pct, "f"),
            "drawdown_pct": format(self.drawdown_pct, "f"),
            "max_position_pct": format(self.max_position_pct, "f"),
            "min_pool_liquidity_usd": format(self.min_pool_liquidity_usd, "f"),
            "min_pool_age_minutes": self.min_pool_age_minutes,
            "max_slippage_bps": format(self.max_slippage_bps, "f"),
            "max_price_impact_bps": format(self.max_price_impact_bps, "f"),
            "max_fee_bps": format(self.max_fee_bps, "f"),
            "quote_max_age_seconds": self.quote_max_age_seconds,
            "market_max_age_seconds": self.market_max_age_seconds,
            "security_max_age_seconds": self.security_max_age_seconds,
            "cooldown_seconds": self.cooldown_seconds,
        }


@dataclass
class QuoteView:
    price_usd: Decimal
    notional_usd: Decimal
    fee_usd: Decimal
    fee_bps: Decimal
    slippage_bps: Decimal
    price_impact_bps: Decimal
    observed_at: datetime
    side: str
    input_mint: str
    output_mint: str
    source: str
    available_notional_usd: Decimal | None = None
    fail: bool = False
    fail_reason: str | None = None


@dataclass
class SecurityView:
    classification: str
    observed_at: datetime | None
    critical_flags: list[str] = field(default_factory=list)


@dataclass
class ProposalView:
    proposal_id: str
    mode: str
    mint: str
    side: str
    requested_notional: Decimal
    expires_at: datetime
    pool_liquidity_usd: Decimal | None
    pool_age_minutes: int | None
    market_observed_at: datetime | None


@dataclass
class PortfolioView:
    mode: str
    cash: Decimal
    equity: Decimal
    peak_equity: Decimal
    day_start_equity: Decimal
    open_positions: int
    open_exposure: Decimal
    emergency_stop: bool


@dataclass
class RiskResult:
    approved: bool
    reason_codes: list[str]
    explanation: str

    @property
    def size_cap(self) -> Decimal | None:
        return None


def _age_seconds(now: datetime, then: datetime | None) -> float | None:
    if then is None:
        return None
    return (now - then).total_seconds()


def evaluate_entry(
    *,
    now: datetime,
    limits: RiskLimits,
    portfolio: PortfolioView | None,
    proposal: ProposalView | None,
    quote: QuoteView | None,
    security: SecurityView | None,
    provider_ok: bool,
    duplicate: bool,
    last_order_at: datetime | None,
) -> RiskResult:
    """Return a veto decision. Approval requires every check to pass."""
    reasons: list[str] = []
    if portfolio is None or proposal is None:
        return RiskResult(False, [Reason.MISSING_RISK_INPUT.value], "Risk inputs are incomplete.")
    if proposal.side != "buy":
        reasons.append(Reason.SHORT_NOT_ALLOWED.value)
    if proposal.mode != portfolio.mode:
        reasons.append(Reason.MODE_MISMATCH.value)
    if portfolio.emergency_stop:
        reasons.append(Reason.EMERGENCY_STOP.value)
    if now >= proposal.expires_at:
        reasons.append(Reason.EXPIRED_PROPOSAL.value)
    if not provider_ok:
        reasons.append(Reason.PROVIDER_UNHEALTHY.value)
    if duplicate:
        reasons.append(Reason.DUPLICATE_ORDER.value)
    if last_order_at is not None and (now - last_order_at) < timedelta(seconds=limits.cooldown_seconds):
        reasons.append(Reason.COOLDOWN.value)

    if portfolio.day_start_equity > 0:
        loss = (portfolio.day_start_equity - portfolio.equity) / portfolio.day_start_equity
        if loss >= limits.daily_loss_pct:
            reasons.append(Reason.DAILY_LOSS_CUTOFF.value)
    else:
        reasons.append(Reason.MISSING_RISK_INPUT.value)
    if portfolio.peak_equity > 0:
        drawdown = (portfolio.peak_equity - portfolio.equity) / portfolio.peak_equity
        if drawdown >= limits.drawdown_pct:
            reasons.append(Reason.DRAWDOWN_CUTOFF.value)
    else:
        reasons.append(Reason.MISSING_RISK_INPUT.value)

    if portfolio.open_positions >= limits.max_concurrent_positions:
        reasons.append(Reason.MAX_POSITIONS.value)

    notional = q_usdc(proposal.requested_notional)
    if notional <= 0:
        reasons.append(Reason.POSITION_SIZE.value)
    max_by_equity = q_usdc(portfolio.equity * limits.max_position_pct) if portfolio.equity > 0 else Decimal("0")
    if notional > limits.max_new_trade_usd or notional > max_by_equity:
        reasons.append(Reason.POSITION_SIZE.value)
    room = q_usdc(portfolio.equity * limits.max_exposure_pct - portfolio.open_exposure)
    if notional > room:
        reasons.append(Reason.MAX_EXPOSURE.value)
    if notional > portfolio.cash:
        reasons.append(Reason.INSUFFICIENT_CASH.value)

    market_age = _age_seconds(now, proposal.market_observed_at)
    if market_age is None:
        reasons.append(Reason.MISSING_MARKET_DATA.value)
    elif market_age > limits.market_max_age_seconds or market_age < -1:
        reasons.append(Reason.STALE_MARKET_DATA.value)

    if proposal.pool_liquidity_usd is None:
        reasons.append(Reason.LIQUIDITY.value)
    elif proposal.pool_liquidity_usd < limits.min_pool_liquidity_usd:
        reasons.append(Reason.LIQUIDITY.value)
    if proposal.pool_age_minutes is None:
        reasons.append(Reason.POOL_AGE.value)
    elif proposal.pool_age_minutes < limits.min_pool_age_minutes:
        reasons.append(Reason.POOL_AGE.value)

    if security is None or security.classification not in {"pass", "fail", "unknown"}:
        reasons.append(Reason.SECURITY_UNKNOWN.value)
    else:
        sec_age = _age_seconds(now, security.observed_at)
        if sec_age is None or sec_age > limits.security_max_age_seconds:
            reasons.append(Reason.STALE_SECURITY.value)
        if security.classification == "fail":
            reasons.append(Reason.SECURITY_FAIL.value)
        elif security.classification != "pass":
            reasons.append(Reason.SECURITY_UNKNOWN.value)

    if quote is None:
        reasons.append(Reason.MISSING_QUOTE.value)
    else:
        quote_age = _age_seconds(now, quote.observed_at)
        if quote_age is None or quote_age > limits.quote_max_age_seconds or quote_age < -1:
            reasons.append(Reason.STALE_QUOTE.value)
        if quote.side != "buy":
            reasons.append(Reason.MISSING_QUOTE.value)
        if quote.price_impact_bps > limits.max_price_impact_bps:
            reasons.append(Reason.PRICE_IMPACT.value)
        if quote.slippage_bps > limits.max_slippage_bps:
            reasons.append(Reason.SLIPPAGE.value)
        if quote.fee_bps > limits.max_fee_bps:
            reasons.append(Reason.FEES.value)
        if quote.fail:
            reasons.append(Reason.MISSING_QUOTE.value)

    unique = list(dict.fromkeys(reasons))
    if unique:
        return RiskResult(False, unique, "Entry blocked: " + ", ".join(unique))
    return RiskResult(True, [], "Entry approved within paper risk limits.")


def capped_notional(requested: Decimal, quote: QuoteView | None, limits: RiskLimits, liquidity: Decimal | None) -> Decimal:
    """Size the fill to quoted capacity. Never increase the request."""
    amount = q_usdc(requested)
    if quote and quote.available_notional_usd is not None:
        amount = min(amount, q_usdc(quote.available_notional_usd))
    if liquidity is not None:
        amount = min(amount, q_usdc(liquidity * limits.max_pool_participation))
    if amount < 0:
        return Decimal("0")
    return amount
