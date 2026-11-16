"""Exact decimal helpers. Financial quantities never use binary floats."""

from __future__ import annotations

from decimal import Decimal, ROUND_DOWN, ROUND_HALF_EVEN
from typing import Any


USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
SOL_MINT = "So11111111111111111111111111111111111111112"
USDT_MINT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
MAJOR_MINTS = frozenset({USDC_MINT, SOL_MINT, USDT_MINT})

USDC_QUANT = Decimal("0.000001")
PRICE_QUANT = Decimal("0.000000000001")


def D(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value is None:
        raise ValueError("missing decimal value")
    if isinstance(value, float):
        raise TypeError("refusing to coerce float into a monetary decimal")
    return Decimal(str(value))


def optional_d(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, float):
        # Provider JSON numbers arrive as floats. str() is the boundary; accounting still rejects floats.
        return Decimal(str(value))
    return D(value)


def q_usdc(value: Decimal) -> Decimal:
    return D(value).quantize(USDC_QUANT, rounding=ROUND_HALF_EVEN)


def q_qty(value: Decimal, decimals: int) -> Decimal:
    quantum = Decimal("1").scaleb(-int(decimals))
    return D(value).quantize(quantum, rounding=ROUND_DOWN)


def q_price(value: Decimal) -> Decimal:
    return D(value).quantize(PRICE_QUANT, rounding=ROUND_HALF_EVEN)


def money_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(q_usdc(value), "f")


def decimal_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")
