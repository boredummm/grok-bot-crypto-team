"""Portfolio transitions. Callers persist the returned numbers inside one transaction."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.money import q_price, q_qty, q_usdc


class AccountingError(Exception):
    pass


@dataclass
class BuyFillResult:
    cash_after: Decimal
    qty: Decimal
    spend: Decimal
    fee: Decimal
    price: Decimal
    notional: Decimal


@dataclass
class SellFillResult:
    cash_after: Decimal
    qty_sold: Decimal
    proceeds: Decimal
    fee: Decimal
    price: Decimal
    cost_released: Decimal
    realized_pnl: Decimal


def buy_fill(
    *,
    cash: Decimal,
    notional: Decimal,
    fee: Decimal,
    price: Decimal,
    decimals: int,
) -> BuyFillResult:
    if notional <= 0 or price <= 0 or fee < 0:
        raise AccountingError("buy fill terms are invalid")
    notional_q = q_usdc(notional)
    fee_q = q_usdc(fee)
    spend = q_usdc(notional_q + fee_q)
    if spend > cash:
        raise AccountingError("cash would go negative")
    qty = q_qty(notional_q / price, decimals)
    if qty <= 0:
        raise AccountingError("quantity rounded to zero")
    return BuyFillResult(
        cash_after=q_usdc(cash - spend),
        qty=qty,
        spend=spend,
        fee=fee_q,
        price=q_price(price),
        notional=notional_q,
    )


def sell_fill(
    *,
    cash: Decimal,
    position_qty: Decimal,
    position_cost: Decimal,
    qty: Decimal,
    price: Decimal,
    fee: Decimal,
) -> SellFillResult:
    if qty <= 0 or price <= 0 or fee < 0:
        raise AccountingError("sell fill terms are invalid")
    if qty > position_qty:
        raise AccountingError("sell quantity exceeds position")
    fee_q = q_usdc(fee)
    gross = q_usdc(qty * price)
    proceeds = q_usdc(gross - fee_q)
    if proceeds < 0:
        raise AccountingError("fees exceed sale proceeds")
    cost_released = q_usdc(position_cost * (qty / position_qty))
    realized = q_usdc(proceeds - cost_released)
    return SellFillResult(
        cash_after=q_usdc(cash + proceeds),
        qty_sold=qty,
        proceeds=proceeds,
        fee=fee_q,
        price=q_price(price),
        cost_released=cost_released,
        realized_pnl=realized,
    )


def position_mark_value(
    *,
    cost_basis: Decimal,
    qty: Decimal,
    mark: Decimal | None,
    mark_fresh: bool,
) -> tuple[Decimal, bool]:
    """Return (value, fresh). Stale marks cannot increase equity above cost."""
    if mark is None or qty <= 0:
        return q_usdc(cost_basis), False
    marked = q_usdc(mark * qty)
    if not mark_fresh:
        return min(q_usdc(cost_basis), marked), False
    return marked, True
