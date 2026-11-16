"""Funnel stages, thresholds, rejection bench TTLs, and 8-seat desk projections.

Follows 'code fetches, model judges, code decides':
- Stage arithmetic (`free_kill`, `trade_kill`, `chain_kill`) runs deterministically in code.
- `soft_kill` evaluates typed JEV answers ONLY when JEV is genuinely connected.
- Independent `RiskEngine` retains absolute final veto on every entry.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.analysis import AuthorityFacts, Observation
from app.money import q_usdc

SEAT_ORDER = (
    "CHIEF",
    "SCAN",
    "VET",
    "SOCIAL",
    "SIZE",
    "FILLS",
    "RISK",
    "CRAWLER",
)

# Active experimental paper thresholds (aligned with SOL SWARM's $1,000 paper book).
DEFAULT_THRESHOLDS: dict[str, Any] = {
    "label": "experimental_paper_defaults",
    "disclaimer": "Experimental paper-trading gates for a $1,000 virtual book. Not validated for profitability.",
    "hard": {
        "min_age_minutes": 15,
        "max_age_hours": 72,
        "min_liquidity_usd": 50000,
        "min_volume_h24": 12000,
        "min_mcap_usd": 40000,
        "max_mcap_usd": 8000000,
        "min_trades_h24": 40,
        "max_top_wallet": 0.25,
        "max_top_10": 0.60,
        "min_holders": 50,
    },
    "soft": {
        "concentration_is_exit_risk": {"dir": "max", "limit": 0.55},
        "momentum_already_spent": {"dir": "max", "limit": 0.60},
        "liquidity_fits_ticket": {"dir": "min", "limit": 0.60},
        "account_is_the_project": {"dir": "min", "limit": 0.70},
        "recycled_account": {"dir": "max", "limit": 0.50},
        "audience_is_real": {"dir": "min", "limit": 0.45},
        "effort": {"dir": "min", "limit": 1.0},
        "dev_still_loaded": {"dir": "max", "limit": 0.55},
        "shape_min_crowd": 0.55,
        "pick_min_worth": 0.60,
        "pick_min_conf": 0.55,
        "dark_ticket_cut": 0.40,
        "no_social_cut": 0.60,
    },
}

# Raw constants from the author's setup guide — exposed strictly as labeled inspiration for shadow comparison.
GUIDE_INSPIRATION_THRESHOLDS: dict[str, Any] = {
    "label": "guide_reference_unvalidated",
    "disclaimer": "Author's raw Day-1 guide constants. UNVALIDATED for our strategy and $1,000 paper bank; shown for shadow comparison only.",
    "hard": {
        "min_age_minutes": 15,
        "max_age_hours": 72,
        "min_liquidity_usd": 12000,
        "min_volume_h24": 40000,
        "min_mcap_usd": 60000,
        "max_mcap_usd": 8000000,
        "min_trades_h24": 150,
        "max_top_wallet": 0.05,
        "max_top_10": 0.60,
        "min_holders": 80,
    },
}

# Reason-specific rejection bench durations (in minutes) adapted from guide `book.py`.
BENCH_MINUTES: dict[str, int] = {
    "honeypot": 100_000,
    "authority_open": 100_000,
    "active_freeze_authority": 100_000,
    "active_mint_authority": 100_000,
    "top_wallet": 100_000,
    "sell_side": 100_000,
    "provider_malicious_flag": 100_000,
    "recycled_account": 360,
    "account_is_the_project": 360,
    "top_10": 90,
    "holder_concentration": 90,
    "holders": 90,
    "dev_still_loaded": 90,
    "concentration_is_exit_risk": 90,
    "promoted_listing": 60,
    "shape": 25,
    "shape_weak": 25,
    "momentum_already_spent": 25,
    "liquidity_fits_ticket": 25,
    "liquidity": 25,
    "volume": 25,
    "trades": 25,
    "no_sells": 45,
    "mcap": 25,
    "age": 20,
    "stale_market": 15,
    "dossier_failed": 30,
}
DEFAULT_BENCH_MINUTES = 45


def bench_ttl_minutes(reason: str) -> int:
    return BENCH_MINUTES.get(reason, DEFAULT_BENCH_MINUTES)


def free_kill(obs: Observation, now: datetime, hard: dict[str, Any]) -> str | None:
    """Stage 1: zero-extra-network arithmetic filter on age, liquidity, volume, mcap."""
    if obs.pool_created_at is None:
        return "age"
    age_min = (now - obs.pool_created_at).total_seconds() / 60.0
    min_age = float(hard.get("min_age_minutes", 15))
    max_age = float(hard.get("max_age_hours", 72)) * 60.0
    if not (min_age <= age_min <= max_age):
        return "age"
    liq = obs.pool_liquidity_usd
    if liq is None or liq < Decimal(str(hard.get("min_liquidity_usd", 50000))):
        return "liquidity"
    # Estimate 24h volume pace from available h1 / m5 windows without inventing numbers.
    vol_est = None
    if obs.volume_h1 is not None:
        vol_est = obs.volume_h1 * Decimal("24")
    elif obs.volume_m5 is not None:
        vol_est = obs.volume_m5 * Decimal("288")
    if vol_est is not None and vol_est < Decimal(str(hard.get("min_volume_h24", 12000))):
        return "volume"
    if obs.market_cap_usd is not None:
        min_mc = Decimal(str(hard.get("min_mcap_usd", 40000)))
        max_mc = Decimal(str(hard.get("max_mcap_usd", 8000000)))
        if not (min_mc <= obs.market_cap_usd <= max_mc):
            return "mcap"
    return None


def trade_kill(obs: Observation, hard: dict[str, Any]) -> str | None:
    """Stage 2: trade counts & one-way honeypot/wash check."""
    buys_h1 = obs.buys_h1 if obs.buys_h1 is not None else (obs.buys_m5 * 4 if obs.buys_m5 is not None else None)
    sells_h1 = obs.sells_h1 if obs.sells_h1 is not None else (obs.sells_m5 * 4 if obs.sells_m5 is not None else None)
    if buys_h1 is None or sells_h1 is None:
        return "no_pair"
    if sells_h1 == 0 and buys_h1 > 20:
        return "no_sells"
    trades_est_24h = (buys_h1 + sells_h1) * 24
    if trades_est_24h < int(hard.get("min_trades_h24", 40)):
        return "trades"
    return None


def chain_kill(
    obs: Observation,
    facts: AuthorityFacts | None,
    hard: dict[str, Any],
    *,
    top_10_pct: Decimal | None = None,
    allow_active_mint: bool = False,
) -> str | None:
    """Stage 3: on-chain / security facts before any model judgement."""
    if facts is None or not facts.parsed:
        return "dossier_failed"
    if facts.freeze_authority_active is True:
        return "authority_open"
    if facts.mint_authority_active is True and not allow_active_mint:
        return "authority_open"
    if facts.malicious_flag is True:
        return "provider_malicious_flag"
    if facts.top_holder_pct is not None:
        max_top_wallet_pct = Decimal(str(hard.get("max_top_wallet", 0.25))) * Decimal("100")
        if facts.top_holder_pct > max_top_wallet_pct:
            return "top_wallet"
    if top_10_pct is not None:
        max_top_10_pct = Decimal(str(hard.get("max_top_10", 0.60))) * Decimal("100")
        if top_10_pct > max_top_10_pct:
            return "top_10"
    if obs.holder_count is not None and obs.holder_count < int(hard.get("min_holders", 50)):
        return "holders"
    return None


def soft_kill(answers: dict[str, dict[str, Any]], soft: dict[str, Any]) -> str | None:
    """Stage 4: compare JEV typed answers against SOFT limits. Never called on fake answers."""
    if not answers:
        return None
    for name, rule in soft.items():
        if not isinstance(rule, dict) or "dir" not in rule:
            continue
        ans = answers.get(name)
        if not isinstance(ans, dict):
            continue
        val = ans.get("noul") if "noul" in ans else ans.get("score")
        if val is None:
            continue
        limit = float(rule["limit"])
        if rule["dir"] == "max" and float(val) > limit:
            return name
        if rule["dir"] == "min" and float(val) < limit:
            return name

    shape = answers.get("shape")
    if isinstance(shape, dict):
        if shape.get("choice") in {"fading", "one_buyer"}:
            return "shape"
        probs = shape.get("probabilities") or {}
        if float(probs.get("crowd", 0.0)) < float(soft.get("shape_min_crowd", 0.55)):
            return "shape_weak"
    return None


@dataclass
class SizeResult:
    ticket_usd: Decimal
    pct_of_bank: Decimal
    size_factor: Decimal
    viable: bool
    reason: str


def compute_size_ticket(
    *,
    equity: Decimal,
    free_cash: Decimal,
    open_exposure: Decimal,
    max_position_pct: Decimal,
    max_exposure_pct: Decimal,
    max_trade_usd: Decimal,
    pool_liquidity_usd: Decimal | None,
    size_factor: Decimal = Decimal("1.0"),
    expected_fee_usd: Decimal = Decimal("0.06"),
    max_fee_bps: Decimal = Decimal("300"),
) -> SizeResult:
    """Deterministic ticket sizing. Never invents Kelly edge without tested win rates."""
    if equity <= 0 or free_cash <= 0:
        return SizeResult(Decimal("0"), Decimal("0"), size_factor, False, "no_free_cash")
    base_ticket = min(q_usdc(equity * max_position_pct), q_usdc(max_trade_usd))
    exposure_room = max(Decimal("0"), q_usdc(equity * max_exposure_pct - open_exposure))
    ticket = min(base_ticket, exposure_room, q_usdc(free_cash))
    ticket = q_usdc(ticket * size_factor)
    if pool_liquidity_usd is not None and pool_liquidity_usd > 0:
        pool_cap = q_usdc(pool_liquidity_usd * Decimal("0.02"))
        ticket = min(ticket, pool_cap)
    if ticket <= Decimal("0"):
        return SizeResult(Decimal("0"), Decimal("0"), size_factor, False, "exposure_or_pool_cap")
    eff_fee_bps = (expected_fee_usd / ticket) * Decimal("10000") if ticket > 0 else Decimal("99999")
    if eff_fee_bps > max_fee_bps:
        return SizeResult(
            Decimal("0"),
            Decimal("0"),
            size_factor,
            False,
            f"fee_floor_exceeded ({eff_fee_bps:.0f} bps > {max_fee_bps} bps)",
        )
    pct = (ticket / equity) * Decimal("100")
    return SizeResult(
        ticket_usd=ticket,
        pct_of_bank=pct.quantize(Decimal("0.1")),
        size_factor=size_factor,
        viable=True,
        reason=f"{pct.quantize(Decimal('0.1'))}% of ${equity:.0f} paper bank · pool/fee checked",
    )


def check_volume_decay_exit(volume_h6: Decimal | None, volume_h24: Decimal | None) -> bool:
    """Guide RISK rule: close when 6h volume drops below 20% of the 6h average over 24h."""
    if volume_h6 is None or volume_h24 is None or volume_h24 <= 0:
        return False
    avg_6h = volume_h24 / Decimal("4")
    if avg_6h <= 0:
        return False
    return (volume_h6 / avg_6h) < Decimal("0.20")


def demo_playback_frames() -> dict[str, Any]:
    """Explicitly labeled pre-recorded choreography for testing Office animations on demand."""
    return {
        "is_demo_playback": True,
        "label": "DEMO PLAYBACK — DETERMINISTIC STORYBOARD (DOES NOT MODIFY PAPER PORTFOLIO)",
        "steps": [
            {
                "step": 1,
                "phase": "crawl",
                "clock": "00:01 DEMO",
                "active_seat": "CRAWLER",
                "target": "screen",
                "mint": "demo:momentum",
                "symbol": "DEMO-MOM",
                "bubble": "reading 5 demo pools + Solana authority fixtures",
                "crawler_web": True,
                "funnel_stage": "free_kill",
            },
            {
                "step": 2,
                "phase": "scan",
                "clock": "00:05 DEMO",
                "active_seat": "SCAN",
                "target": "center",
                "mint": "demo:momentum",
                "symbol": "DEMO-MOM",
                "bubble": "DEMO-THIN dropped on liquidity ($1K < $50K)",
                "crawler_web": True,
                "funnel_stage": "trade_kill",
            },
            {
                "step": 3,
                "phase": "vet",
                "clock": "00:09 DEMO",
                "active_seat": "VET",
                "target": "screen",
                "mint": "demo:freeze",
                "symbol": "DEMO-FRZ",
                "bubble": "active_freeze_authority killed DEMO-FRZ -> benched",
                "crawler_web": True,
                "funnel_stage": "chain_kill",
            },
            {
                "step": 4,
                "phase": "social",
                "clock": "00:13 DEMO",
                "active_seat": "SOCIAL",
                "target": "desk",
                "mint": "demo:momentum",
                "symbol": "DEMO-MOM",
                "bubble": "no verified on-chain X handle · no fan-account guessing",
                "crawler_web": False,
                "funnel_stage": "soft_kill",
            },
            {
                "step": 5,
                "phase": "pick",
                "clock": "00:16 DEMO",
                "active_seat": "CHIEF",
                "target": "chief",
                "mint": "demo:momentum",
                "symbol": "DEMO-MOM",
                "bubble": "survivor DEMO-MOM handed to SIZE -> FILLS -> RISK",
                "crawler_web": False,
                "funnel_stage": "pick",
            },
            {
                "step": 6,
                "phase": "size_and_fill",
                "clock": "00:20 DEMO",
                "active_seat": "FILLS",
                "target": "center",
                "mint": "demo:momentum",
                "symbol": "DEMO-MOM",
                "bubble": "paper buy $20.00 @ $1.00 · fee $0.06 (demo_fixture)",
                "crawler_web": False,
                "funnel_stage": "pick",
            },
            {
                "step": 7,
                "phase": "monitor",
                "clock": "00:24 DEMO",
                "active_seat": "RISK",
                "target": "screen",
                "mint": "demo:momentum",
                "symbol": "DEMO-MOM",
                "bubble": "watching adverse -8% stop & exit quote route",
                "crawler_web": False,
                "funnel_stage": "pick",
            },
        ],
    }
