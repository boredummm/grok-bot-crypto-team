from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.accounting import AccountingError, buy_fill, sell_fill
from app.execution import plan_buy, plan_sell
from app.money import D
from app.risk import (
    PortfolioView,
    ProposalView,
    QuoteView,
    RiskLimits,
    SecurityView,
    evaluate_entry,
)

NOW = datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc)
LIMITS = RiskLimits()


def portfolio(**kwargs) -> PortfolioView:
    base = dict(
        mode="demo",
        cash=Decimal("1000"),
        equity=Decimal("1000"),
        peak_equity=Decimal("1000"),
        day_start_equity=Decimal("1000"),
        open_positions=0,
        open_exposure=Decimal("0"),
        emergency_stop=False,
    )
    base.update(kwargs)
    return PortfolioView(**base)


def proposal(**kwargs) -> ProposalView:
    base = dict(
        proposal_id="p1",
        mode="demo",
        mint="demo:momentum",
        side="buy",
        requested_notional=Decimal("20"),
        expires_at=NOW + timedelta(seconds=15),
        pool_liquidity_usd=Decimal("80000"),
        pool_age_minutes=45,
        market_observed_at=NOW,
    )
    base.update(kwargs)
    return ProposalView(**base)


def quote(**kwargs) -> QuoteView:
    base = dict(
        price_usd=Decimal("1"),
        notional_usd=Decimal("20"),
        fee_usd=Decimal("0.06"),
        fee_bps=Decimal("30"),
        slippage_bps=Decimal("40"),
        price_impact_bps=Decimal("20"),
        observed_at=NOW,
        side="buy",
        input_mint="usdc",
        output_mint="demo:momentum",
        source="demo_fixture",
        available_notional_usd=Decimal("800"),
    )
    base.update(kwargs)
    return QuoteView(**base)


def security(**kwargs) -> SecurityView:
    base = dict(classification="pass", observed_at=NOW, critical_flags=[])
    base.update(kwargs)
    return SecurityView(**base)


def decide(**kwargs):
    args = dict(
        now=NOW,
        limits=LIMITS,
        portfolio=portfolio(),
        proposal=proposal(),
        quote=quote(),
        security=security(),
        provider_ok=True,
        duplicate=False,
        last_order_at=None,
    )
    args.update(kwargs)
    return evaluate_entry(**args)


def test_entry_approves_inside_default_limits():
    result = decide()
    assert result.approved
    assert result.reason_codes == []


def test_missing_market_data():
    result = decide(proposal=proposal(market_observed_at=None))
    assert not result.approved
    assert "MISSING_MARKET_DATA" in result.reason_codes


def test_stale_quote():
    result = decide(quote=quote(observed_at=NOW - timedelta(seconds=60)))
    assert "STALE_QUOTE" in result.reason_codes


def test_security_rejection_and_unknown():
    failed = decide(security=security(classification="fail", critical_flags=["active_freeze_authority"]))
    assert "SECURITY_FAIL" in failed.reason_codes
    unknown = decide(security=security(classification="unknown"))
    assert "SECURITY_UNKNOWN" in unknown.reason_codes
    assert decide(security=None).reason_codes.count("SECURITY_UNKNOWN") == 1


def test_missing_risk_inputs():
    result = decide(portfolio=None)
    assert result.reason_codes == ["MISSING_RISK_INPUT"]


def test_position_size_exposure_and_cash():
    assert "POSITION_SIZE" in decide(proposal=proposal(requested_notional=Decimal("21"))).reason_codes
    small_equity = decide(portfolio=portfolio(equity=Decimal("500"), cash=Decimal("500"), day_start_equity=Decimal("500"), peak_equity=Decimal("500")))
    assert "POSITION_SIZE" in small_equity.reason_codes
    crowded = decide(portfolio=portfolio(open_exposure=Decimal("50")))
    assert "MAX_EXPOSURE" in crowded.reason_codes
    assert "MAX_POSITIONS" in decide(portfolio=portfolio(open_positions=3)).reason_codes
    assert "INSUFFICIENT_CASH" in decide(portfolio=portfolio(cash=Decimal("10"))).reason_codes


def test_daily_loss_and_drawdown_cutoffs():
    daily = decide(portfolio=portfolio(equity=Decimal("970"), day_start_equity=Decimal("1000")))
    assert "DAILY_LOSS_CUTOFF" in daily.reason_codes
    near = decide(portfolio=portfolio(equity=Decimal("971"), day_start_equity=Decimal("1000")))
    assert "DAILY_LOSS_CUTOFF" not in near.reason_codes
    draw = decide(portfolio=portfolio(equity=Decimal("900"), peak_equity=Decimal("1000")))
    assert "DRAWDOWN_CUTOFF" in draw.reason_codes


def test_emergency_stop_duplicate_and_short():
    assert "EMERGENCY_STOP" in decide(portfolio=portfolio(emergency_stop=True)).reason_codes
    assert "DUPLICATE_ORDER" in decide(duplicate=True).reason_codes
    assert "SHORT_NOT_ALLOWED" in decide(proposal=proposal(side="sell")).reason_codes
    assert "COOLDOWN" in decide(last_order_at=NOW - timedelta(seconds=30)).reason_codes


def test_buy_accounting_rejects_negative_cash_and_partial_fill():
    with pytest.raises(AccountingError):
        buy_fill(cash=Decimal("10"), notional=Decimal("20"), fee=Decimal("0.1"), price=Decimal("1"), decimals=6)
    partial = plan_buy(
        cash=Decimal("1000"),
        requested=Decimal("20"),
        quote=quote(available_notional_usd=Decimal("10")),
        liquidity=Decimal("80000"),
        limits=LIMITS,
        decimals=6,
    )
    assert partial.status == "partial"
    assert partial.fill is not None
    assert partial.fill.notional == Decimal("10.000000")
    assert partial.remaining_notional == Decimal("10.000000")
    assert partial.fill.cash_after == Decimal("989.970000")
    failed = plan_buy(
        cash=Decimal("1000"),
        requested=Decimal("20"),
        quote=quote(fail=True, fail_reason="route down"),
        liquidity=Decimal("80000"),
        limits=LIMITS,
        decimals=6,
    )
    assert failed.status == "failed"
    assert failed.fill is None


def test_sell_and_unavailable_exit():
    sold = sell_fill(
        cash=Decimal("980"),
        position_qty=Decimal("20"),
        position_cost=Decimal("20.06"),
        qty=Decimal("20"),
        price=Decimal("1.1"),
        fee=Decimal("0.05"),
    )
    assert sold.cash_after > Decimal("980")
    assert sold.realized_pnl == sold.proceeds - sold.cost_released
    unsellable = plan_sell(cash=Decimal("980"), qty=Decimal("20"), cost=Decimal("20"), quote=None)
    assert unsellable.status == "unsellable"
    assert unsellable.fill is None


def test_decimal_refuses_float():
    with pytest.raises(TypeError):
        D(1.2)
