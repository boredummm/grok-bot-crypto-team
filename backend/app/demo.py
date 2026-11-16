"""Labeled demo fixtures. These are not market events."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from app.analysis import AuthorityFacts, Observation
from app.money import USDC_MINT
from app.risk import QuoteView

SOURCE = "demo_fixture"


def _obs(
    now: datetime,
    *,
    mint: str,
    symbol: str,
    name: str,
    liquidity: str,
    price: str,
    age_minutes: int,
    change_m5: str,
    change_h1: str,
    volume_m5: str,
    volume_h1: str,
    buys: int,
    sells: int,
    buyers: int | None,
    stale_seconds: int = 0,
    promoted: bool = False,
) -> Observation:
    observed = now - timedelta(seconds=stale_seconds)
    return Observation(
        mode="demo",
        mint=mint,
        name=name,
        symbol=symbol,
        decimals=6,
        pool_address=f"demo-pool-{symbol.lower()}",
        dex="demo-amm",
        quote_mint=USDC_MINT,
        pool_created_at=now - timedelta(minutes=age_minutes),
        token_created_at=now - timedelta(minutes=age_minutes + 5),
        price_usd=Decimal(price),
        market_cap_usd=Decimal("150000"),
        fdv_usd=Decimal("200000"),
        pool_liquidity_usd=Decimal(liquidity),
        token_liquidity_usd=Decimal(liquidity) / Decimal("2"),
        volume_m5=Decimal(volume_m5),
        volume_h1=Decimal(volume_h1),
        price_change_m5=Decimal(change_m5),
        price_change_h1=Decimal(change_h1),
        buys_m5=buys,
        sells_m5=sells,
        buys_h1=buys * 4,
        sells_h1=sells * 4,
        unique_buyers_m5=buyers,
        holder_count=420,
        provider=SOURCE,
        provider_reported_at=observed,
        observed_at=observed,
        promoted=promoted,
    )


def demo_observations(now: datetime) -> list[Observation]:
    good = _obs(
        now,
        mint="demo:momentum",
        symbol="DEMO-MOM",
        name="Demo Momentum",
        liquidity="80000",
        price="1.00",
        age_minutes=45,
        change_m5="4.0",
        change_h1="6.0",
        volume_m5="8000",
        volume_h1="20000",
        buys=40,
        sells=10,
        buyers=22,
    )
    # A second pool for the same mint must collapse to one candidate.
    extra_pool = _obs(
        now,
        mint="demo:momentum",
        symbol="DEMO-MOM",
        name="Demo Momentum",
        liquidity="20000",
        price="1.00",
        age_minutes=40,
        change_m5="4.0",
        change_h1="6.0",
        volume_m5="1000",
        volume_h1="4000",
        buys=8,
        sells=6,
        buyers=5,
    )
    extra_pool.pool_address = "demo-pool-demo-mom-b"
    freeze = _obs(
        now,
        mint="demo:freeze",
        symbol="DEMO-FRZ",
        name="Demo Freeze Authority",
        liquidity="90000",
        price="0.50",
        age_minutes=90,
        change_m5="5",
        change_h1="7",
        volume_m5="9000",
        volume_h1="22000",
        buys=30,
        sells=8,
        buyers=18,
    )
    thin = _obs(
        now,
        mint="demo:thin",
        symbol="DEMO-THIN",
        name="Demo Thin Liquidity",
        liquidity="1000",
        price="0.20",
        age_minutes=120,
        change_m5="3",
        change_h1="4",
        volume_m5="500",
        volume_h1="1000",
        buys=12,
        sells=4,
        buyers=7,
    )
    stale = _obs(
        now,
        mint="demo:stale",
        symbol="DEMO-STALE",
        name="Demo Stale Print",
        liquidity="70000",
        price="0.80",
        age_minutes=200,
        change_m5="4",
        change_h1="5",
        volume_m5="7000",
        volume_h1="18000",
        buys=25,
        sells=6,
        buyers=14,
        stale_seconds=600,
    )
    promoted = _obs(
        now,
        mint="demo:promoted",
        symbol="DEMO-PROMO",
        name="Demo Promoted Listing",
        liquidity="120000",
        price="1.20",
        age_minutes=60,
        change_m5="9",
        change_h1="12",
        volume_m5="15000",
        volume_h1="30000",
        buys=50,
        sells=10,
        buyers=30,
        promoted=True,
    )
    return [good, extra_pool, freeze, thin, stale, promoted]


def demo_facts(now: datetime) -> dict[str, AuthorityFacts]:
    fresh = now
    base = dict(
        source=SOURCE,
        observed_at=fresh,
        token_program="TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
        mint_authority_active=False,
        freeze_authority_active=False,
        extensions=[],
        transfer_fee_bps=Decimal("0"),
        top_holder_pct=Decimal("8"),
        malicious_flag=False,
        parsed=True,
    )
    facts = {
        "demo:momentum": AuthorityFacts(**base),
        "demo:thin": AuthorityFacts(**base),
        "demo:stale": AuthorityFacts(**{**base, "observed_at": now - timedelta(seconds=30)}),
        "demo:promoted": AuthorityFacts(**base),
        "demo:freeze": AuthorityFacts(**{**base, "freeze_authority_active": True}),
    }
    return facts


def demo_quote(now: datetime, mint: str, side: str, price: Decimal, notional: Decimal = Decimal("20")) -> QuoteView:
    fee = (notional * Decimal("30")) / Decimal("10000")
    return QuoteView(
        price_usd=price,
        notional_usd=notional,
        fee_usd=fee,
        fee_bps=Decimal("30"),
        slippage_bps=Decimal("50"),
        price_impact_bps=Decimal("20"),
        observed_at=now,
        side=side,
        input_mint=USDC_MINT if side == "buy" else mint,
        output_mint=mint if side == "buy" else USDC_MINT,
        source=SOURCE,
        available_notional_usd=Decimal("800"),
        fail=False,
    )


def demo_exit_routes() -> dict[str, bool]:
    return {
        "demo:momentum": True,
        "demo:freeze": True,
        "demo:thin": True,
        "demo:stale": True,
        "demo:promoted": True,
    }
