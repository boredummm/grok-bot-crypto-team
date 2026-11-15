"""Paper fills from quotes. Displayed mark price is never used as the fill price."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.accounting import BuyFillResult, SellFillResult, buy_fill, sell_fill
from app.money import q_usdc
from app.risk import QuoteView, RiskLimits, capped_notional


@dataclass
class BuyPlan:
    status: str
    fill: BuyFillResult | None
    filled_notional: Decimal
    remaining_notional: Decimal
    reason: str | None
    price_impact_bps: Decimal | None
    fee_source: str


@dataclass
class SellPlan:
    status: str
    fill: SellFillResult | None
    reason: str | None
    price_impact_bps: Decimal | None
    fee_source: str


def _scaled_fee(quote: QuoteView, notional: Decimal) -> Decimal:
    if quote.notional_usd <= 0:
        return q_usdc(quote.fee_usd)
    return q_usdc(quote.fee_usd * (notional / quote.notional_usd))


def plan_buy(
    *,
    cash: Decimal,
    requested: Decimal,
    quote: QuoteView | None,
    liquidity: Decimal | None,
    limits: RiskLimits,
    decimals: int,
) -> BuyPlan:
    if quote is None:
        return BuyPlan("rejected", None, Decimal("0"), requested, "MISSING_QUOTE", None, "unavailable")
    if quote.fail or quote.price_usd <= 0:
        return BuyPlan(
            "failed",
            None,
            Decimal("0"),
            requested,
            quote.fail_reason or "QUOTE_FAILED",
            quote.price_impact_bps,
            quote.source,
        )
    notional = capped_notional(requested, quote, limits, liquidity)
    if notional <= 0:
        return BuyPlan("rejected", None, Decimal("0"), requested, "NO_CAPACITY", quote.price_impact_bps, quote.source)
    fee = _scaled_fee(quote, notional)
    try:
        fill = buy_fill(cash=cash, notional=notional, fee=fee, price=quote.price_usd, decimals=decimals)
    except Exception as exc:
        return BuyPlan("failed", None, Decimal("0"), requested, str(exc), quote.price_impact_bps, quote.source)
    remaining = q_usdc(requested - fill.notional)
    status = "partial" if remaining > 0 else "filled"
    return BuyPlan(status, fill, fill.notional, remaining, None, quote.price_impact_bps, quote.source)


def plan_sell(
    *,
    cash: Decimal,
    qty: Decimal,
    cost: Decimal,
    quote: QuoteView | None,
) -> SellPlan:
    if quote is None or quote.fail or quote.price_usd <= 0:
        return SellPlan("unsellable", None, "EXIT_QUOTE_UNAVAILABLE", None, "unavailable")
    fee = quote.fee_usd
    try:
        fill = sell_fill(
            cash=cash,
            position_qty=qty,
            position_cost=cost,
            qty=qty,
            price=quote.price_usd,
            fee=fee,
        )
    except Exception as exc:
        return SellPlan("failed", None, str(exc), quote.price_impact_bps, quote.source)
    return SellPlan("filled", fill, None, quote.price_impact_bps, quote.source)
