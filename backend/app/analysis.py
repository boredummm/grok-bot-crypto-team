"""Pure discovery, momentum, security, and strategy scoring.

Scores use only fields that are present. Missing metrics stay missing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from app.money import D, q_usdc

STRATEGY_VERSION = "momentum-confirmation-v1"

BENIGN_EXTENSIONS = {
    "metadataPointer",
    "tokenMetadata",
    "groupPointer",
    "groupMemberPointer",
    "mintCloseAuthority",
}
RESTRICTIVE_EXTENSIONS = {
    "permanentDelegate",
    "transferHook",
    "nonTransferable",
    "defaultAccountState",
    "pausableConfig",
}


@dataclass
class Observation:
    mode: str
    mint: str
    name: str
    symbol: str
    decimals: int
    pool_address: str
    dex: str | None
    quote_mint: str | None
    pool_created_at: datetime | None
    token_created_at: datetime | None
    price_usd: Decimal | None
    market_cap_usd: Decimal | None
    fdv_usd: Decimal | None
    pool_liquidity_usd: Decimal | None
    token_liquidity_usd: Decimal | None
    volume_m5: Decimal | None
    volume_h1: Decimal | None
    price_change_m5: Decimal | None
    price_change_h1: Decimal | None
    buys_m5: int | None
    sells_m5: int | None
    buys_h1: int | None
    sells_h1: int | None
    unique_buyers_m5: int | None
    holder_count: int | None
    provider: str
    provider_reported_at: datetime | None
    observed_at: datetime
    promoted: bool = False
    pools: list[str] = field(default_factory=list)


@dataclass
class AuthorityFacts:
    """On-chain or fixture facts. None means unknown, which is not a pass."""

    source: str
    observed_at: datetime
    token_program: str | None = None
    mint_authority_active: bool | None = None
    freeze_authority_active: bool | None = None
    extensions: list[str] | None = None
    transfer_fee_bps: Decimal | None = None
    top_holder_pct: Decimal | None = None
    malicious_flag: bool | None = None
    parsed: bool = False


@dataclass
class SecurityReport:
    classification: str
    score: int | None
    critical_flags: list[str]
    warning_flags: list[str]
    evidence: list[dict]
    explanation: str


@dataclass
class MomentumReport:
    score: int | None
    direction: str
    indicators: dict
    completeness: float
    reasons: list[str]
    confidence: float
    observed_at: datetime


@dataclass
class StrategyDecision:
    action: str
    thesis: str
    notional: Decimal | None
    evidence: dict
    exit_conditions: dict
    expected_costs: dict


def dedupe_by_mint(rows: list[Observation]) -> list[Observation]:
    """One candidate per mint. Every pool address is retained on the candidate."""
    chosen: dict[str, Observation] = {}
    for row in rows:
        current = chosen.get(row.mint)
        if current is None:
            row.pools = list(dict.fromkeys([row.pool_address, *row.pools]))
            chosen[row.mint] = row
            continue
        pools = list(dict.fromkeys([*current.pools, row.pool_address, *row.pools]))
        current.pools = pools
        current_liq = current.pool_liquidity_usd or Decimal("-1")
        new_liq = row.pool_liquidity_usd or Decimal("-1")
        if new_liq > current_liq:
            row.pools = pools
            chosen[row.mint] = row
    return list(chosen.values())


def discovery_score(row: Observation, now: datetime) -> int:
    """0-100 organic activity score. Promoted listings do not add points."""
    points = 0.0
    liq = row.pool_liquidity_usd
    if liq is not None and liq > 0:
        if liq >= Decimal("250000"):
            points += 30
        elif liq >= Decimal("50000"):
            points += 24
        elif liq >= Decimal("10000"):
            points += 14
        else:
            points += 6
    vol = row.volume_m5
    if vol is not None and vol > 0:
        if vol >= Decimal("20000"):
            points += 25
        elif vol >= Decimal("5000"):
            points += 18
        elif vol >= Decimal("1000"):
            points += 10
        else:
            points += 4
    buys = row.buys_m5 or 0
    sells = row.sells_m5 or 0
    if row.buys_m5 is not None and row.sells_m5 is not None and (buys + sells) > 0:
        imbalance = (buys - sells) / (buys + sells)
        if imbalance > 0.15 and buys >= 5:
            points += 20
        elif imbalance > 0:
            points += 10
        else:
            points += 4
    if row.pool_created_at is not None:
        age_min = (now - row.pool_created_at).total_seconds() / 60
        if 15 <= age_min <= 360:
            points += 15
        elif age_min > 360:
            points += 8
        else:
            points += 2
    if not row.promoted:
        points += 10
    return max(0, min(100, int(round(points))))


def _pct(part: Decimal | None, whole: Decimal | None) -> Decimal | None:
    if part is None or whole is None or whole == 0:
        return None
    return (part / whole) * Decimal("100")


def momentum_from_observations(
    latest: Observation,
    previous: Observation | None,
    now: datetime,
) -> MomentumReport:
    indicators: dict = {}
    reasons: list[str] = []
    available = 0
    expected = 6

    indicators["price_change_m5_pct"] = (
        format(latest.price_change_m5, "f") if latest.price_change_m5 is not None else None
    )
    if latest.price_change_m5 is not None:
        available += 1
    else:
        reasons.append("5-minute price change is unavailable.")

    m1 = None
    if (
        previous is not None
        and previous.price_usd is not None
        and latest.price_usd is not None
        and previous.price_usd > 0
    ):
        delta = (latest.observed_at - previous.observed_at).total_seconds()
        if 20 <= delta <= 180:
            m1 = ((latest.price_usd - previous.price_usd) / previous.price_usd) * Decimal("100")
            indicators["price_change_m1_pct"] = format(m1, "f")
            available += 1
        else:
            indicators["price_change_m1_pct"] = None
            reasons.append("No observation about one minute earlier, so 1-minute change is unavailable.")
    else:
        indicators["price_change_m1_pct"] = None
        reasons.append("1-minute price change is unavailable.")

    accel = None
    if latest.volume_m5 is not None and latest.volume_h1 is not None and latest.volume_h1 > 0:
        baseline = latest.volume_h1 / Decimal("12")
        if baseline > 0:
            accel = latest.volume_m5 / baseline
            indicators["volume_acceleration"] = format(accel, "f")
            available += 1
    else:
        indicators["volume_acceleration"] = None
        reasons.append("Volume acceleration is unavailable.")

    imbalance = None
    if latest.buys_m5 is not None and latest.sells_m5 is not None and (latest.buys_m5 + latest.sells_m5) > 0:
        imbalance = Decimal(latest.buys_m5 - latest.sells_m5) / Decimal(latest.buys_m5 + latest.sells_m5)
        indicators["buy_sell_imbalance_m5"] = format(imbalance, "f")
        available += 1
    else:
        indicators["buy_sell_imbalance_m5"] = None
        reasons.append("Buy/sell counts are unavailable.")

    if latest.unique_buyers_m5 is not None:
        indicators["unique_buyers_m5"] = latest.unique_buyers_m5
        available += 1
    else:
        indicators["unique_buyers_m5"] = None
        reasons.append("Unique buyer count is unavailable.")

    liq_growth = None
    if (
        previous is not None
        and previous.pool_liquidity_usd is not None
        and latest.pool_liquidity_usd is not None
        and previous.pool_liquidity_usd > 0
    ):
        liq_growth = (latest.pool_liquidity_usd - previous.pool_liquidity_usd) / previous.pool_liquidity_usd
        indicators["liquidity_growth"] = format(liq_growth, "f")
        available += 1
    else:
        indicators["liquidity_growth"] = None
        reasons.append("Liquidity change is unavailable without a prior observation.")

    completeness = available / expected
    bullish_votes = 0
    bearish_votes = 0
    if latest.price_change_m5 is not None:
        if latest.price_change_m5 >= Decimal("1.5"):
            bullish_votes += 1
            reasons.append("5-minute price is higher.")
        elif latest.price_change_m5 <= Decimal("-1.5"):
            bearish_votes += 1
            reasons.append("5-minute price is lower.")
    if accel is not None:
        if accel >= Decimal("1.3"):
            bullish_votes += 1
            reasons.append("5-minute volume is above the trailing hourly pace.")
        elif accel < Decimal("0.7"):
            bearish_votes += 1
    if imbalance is not None:
        if imbalance >= Decimal("0.15"):
            bullish_votes += 1
            reasons.append("Buy count exceeds sell count over 5 minutes.")
        elif imbalance <= Decimal("-0.15"):
            bearish_votes += 1
            reasons.append("Sell count exceeds buy count over 5 minutes.")

    single_spike = (
        latest.price_change_m5 is not None
        and latest.price_change_m5 >= Decimal("8")
        and (accel is None or accel < Decimal("1.1"))
        and (imbalance is None or imbalance < Decimal("0.05"))
    )
    if single_spike:
        reasons.append("Price spike is not confirmed by volume or trade imbalance.")
        bullish_votes = max(0, bullish_votes - 1)

    persistence = False
    if (
        latest.price_change_m5 is not None
        and latest.price_change_h1 is not None
        and latest.price_change_m5 > 0
        and latest.price_change_h1 > 0
    ):
        persistence = True
        reasons.append("Positive 5-minute change agrees with the 1-hour change.")
        bullish_votes += 1
    indicators["persistence"] = persistence
    indicators["breakout_confirmed"] = bool(
        bullish_votes >= 3 and not single_spike and latest.price_change_m5 is not None and latest.price_change_m5 > 0
    )

    if available == 0 or latest.price_usd is None:
        return MomentumReport(
            score=None,
            direction="unknown",
            indicators=indicators,
            completeness=completeness,
            reasons=reasons or ["No usable market observations."],
            confidence=0.0,
            observed_at=now,
        )

    raw = 50 + bullish_votes * 12 - bearish_votes * 12
    if single_spike:
        raw -= 10
    score = max(0, min(100, int(raw)))
    if bullish_votes > bearish_votes and bullish_votes >= 2 and not single_spike:
        direction = "bullish"
    elif bearish_votes > bullish_votes:
        direction = "bearish"
    else:
        direction = "neutral"
    return MomentumReport(
        score=score,
        direction=direction,
        indicators=indicators,
        completeness=round(completeness, 4),
        reasons=reasons,
        confidence=round(completeness, 4),
        observed_at=now,
    )


def classify_security(
    facts: AuthorityFacts | None,
    *,
    liquidity_usd: Decimal | None,
    min_liquidity: Decimal,
    exit_route: bool | None,
    observed_at: datetime,
    max_age_seconds: int,
    allow_active_mint: bool = False,
    concentration_critical_pct: Decimal = Decimal("60"),
) -> SecurityReport:
    evidence: list[dict] = []
    critical: list[str] = []
    warning: list[str] = []
    if facts is None:
        return SecurityReport(
            classification="unknown",
            score=None,
            critical_flags=["security_data_missing"],
            warning_flags=[],
            evidence=[],
            explanation="No authority data was collected. Missing data is not a pass.",
        )
    evidence.append(
        {
            "source": facts.source,
            "observed_at": facts.observed_at.isoformat(),
            "parsed": facts.parsed,
            "token_program": facts.token_program,
            "mint_authority_active": facts.mint_authority_active,
            "freeze_authority_active": facts.freeze_authority_active,
            "extensions": facts.extensions,
            "transfer_fee_bps": format(facts.transfer_fee_bps, "f") if facts.transfer_fee_bps is not None else None,
            "top_holder_pct": format(facts.top_holder_pct, "f") if facts.top_holder_pct is not None else None,
            "malicious_flag": facts.malicious_flag,
            "exit_route": exit_route,
            "liquidity_usd": format(liquidity_usd, "f") if liquidity_usd is not None else None,
        }
    )
    age = (observed_at - facts.observed_at).total_seconds()
    if age > max_age_seconds or age < -1:
        critical.append("stale_security_data")

    if not facts.parsed:
        critical.append("unparsed_mint_account")
    if facts.token_program is None:
        critical.append("unknown_token_program")
    if facts.freeze_authority_active is None:
        critical.append("freeze_authority_unknown")
    elif facts.freeze_authority_active:
        critical.append("active_freeze_authority")
    if facts.mint_authority_active is None:
        critical.append("mint_authority_unknown")
    elif facts.mint_authority_active and not allow_active_mint:
        critical.append("active_mint_authority")
    elif facts.mint_authority_active and allow_active_mint:
        warning.append("active_mint_authority_permitted_by_policy")

    if facts.extensions is None:
        critical.append("extensions_unknown")
    else:
        for name in facts.extensions:
            if name == "transferFeeConfig":
                if facts.transfer_fee_bps is None:
                    critical.append("transfer_fee_unknown")
                elif facts.transfer_fee_bps > 0:
                    critical.append("transfer_fee")
            elif name in RESTRICTIVE_EXTENSIONS:
                critical.append(f"restrictive_extension:{name}")
            elif name not in BENIGN_EXTENSIONS:
                critical.append(f"unknown_extension:{name}")
            elif name == "mintCloseAuthority":
                warning.append("mint_close_authority")

    if facts.top_holder_pct is None:
        critical.append("holder_concentration_unknown")
    elif facts.top_holder_pct >= concentration_critical_pct:
        critical.append("holder_concentration")
    elif facts.top_holder_pct >= Decimal("25"):
        warning.append("elevated_holder_concentration")

    if liquidity_usd is None:
        critical.append("liquidity_unknown")
    elif liquidity_usd < min_liquidity:
        critical.append("insufficient_liquidity")

    if exit_route is None:
        critical.append("exit_route_unknown")
    elif exit_route is False:
        critical.append("missing_exit_route")

    if facts.malicious_flag is True:
        critical.append("provider_malicious_flag")
    elif facts.malicious_flag is None:
        warning.append("malicious_flag_not_provided")

    unique_critical = list(dict.fromkeys(critical))
    unique_warning = list(dict.fromkeys(warning))
    if unique_critical:
        hard_fail = [
            flag
            for flag in unique_critical
            if flag
            in {
                "active_freeze_authority",
                "active_mint_authority",
                "transfer_fee",
                "holder_concentration",
                "insufficient_liquidity",
                "missing_exit_route",
                "provider_malicious_flag",
            }
            or flag.startswith("restrictive_extension:")
        ]
        if hard_fail:
            classification = "fail"
            explanation = "Confirmed risk flags block entry. This is not a claim that every scam can be detected."
        else:
            classification = "unknown"
            explanation = "Critical checks are missing or stale. Unknown is not treated as safe."
        score = max(0, 40 - 8 * len(unique_critical))
        return SecurityReport(classification, score, unique_critical, unique_warning, evidence, explanation)

    score = max(0, 100 - 5 * len(unique_warning))
    return SecurityReport(
        classification="pass",
        score=score,
        critical_flags=[],
        warning_flags=unique_warning,
        evidence=evidence,
        explanation="Checked authorities, restrictions, concentration, liquidity, and an exit route. This does not mean the token cannot still lose value or be manipulated.",
    )


def propose_entry(
    *,
    observation: Observation,
    momentum: MomentumReport,
    security: SecurityReport,
    quote_ok: bool,
    quote_fee_bps: Decimal | None,
    now: datetime,
    equity: Decimal,
    limits_max_trade: Decimal,
    limits_position_pct: Decimal,
    min_liquidity: Decimal,
    min_pool_age: int,
    momentum_threshold: int,
    max_fee_bps: Decimal,
    allow_promoted: bool = False,
) -> StrategyDecision:
    exit_conditions = {
        "adverse_price_pct": "8",
        "take_profit_pct": "20",
        "trailing_stop_pct": "10",
        "max_holding_minutes": 24 * 60,
        "liquidity_drop_pct": "50",
    }
    evidence = {
        "discovery_inputs_present": observation.price_usd is not None,
        "momentum_score": momentum.score,
        "momentum_completeness": momentum.completeness,
        "security_classification": security.classification,
        "promoted": observation.promoted,
        "quote_ok": quote_ok,
    }
    failures: list[str] = []
    if observation.price_usd is None or observation.observed_at is None:
        failures.append("market data missing")
    age = (now - observation.observed_at).total_seconds()
    if age > 90:
        failures.append("market data stale")
    pool_age = None
    if observation.pool_created_at is None:
        failures.append("pool age unknown")
    else:
        pool_age = int((now - observation.pool_created_at).total_seconds() // 60)
        if pool_age < min_pool_age:
            failures.append("pool younger than minimum")
    if observation.pool_liquidity_usd is None or observation.pool_liquidity_usd < min_liquidity:
        failures.append("liquidity below minimum")
    if momentum.score is None or momentum.completeness < 0.5:
        failures.append("momentum data incomplete")
    elif momentum.score < momentum_threshold or momentum.direction != "bullish":
        failures.append("momentum below confirmation threshold")
    if not momentum.indicators.get("breakout_confirmed"):
        failures.append("breakout not confirmed")
    if momentum.indicators.get("volume_acceleration") is None:
        failures.append("volume confirmation missing")
    if security.classification != "pass":
        failures.append(f"security {security.classification}")
    if observation.promoted and not allow_promoted:
        failures.append("promoted listing is not treated as demand")
    if not quote_ok:
        failures.append("executable quote missing")
    if quote_fee_bps is None:
        failures.append("fee estimate missing")
    elif quote_fee_bps > max_fee_bps:
        failures.append("expected fee above limit")

    if failures:
        return StrategyDecision(
            action="hold",
            thesis="Momentum Confirmation v1 withheld an entry: " + "; ".join(failures),
            notional=None,
            evidence={**evidence, "failures": failures, "pool_age_minutes": pool_age},
            exit_conditions=exit_conditions,
            expected_costs={"fee_bps": format(quote_fee_bps, "f") if quote_fee_bps is not None else None},
        )

    by_equity = q_usdc(equity * limits_position_pct)
    notional = min(q_usdc(limits_max_trade), by_equity)
    return StrategyDecision(
        action="propose",
        thesis=(
            "Momentum Confirmation v1 sees confirmed short-term activity, "
            "minimum liquidity and pool age, a passing security check, and an executable quote. "
            "Promoted placement was not used as demand."
        ),
        notional=notional,
        evidence={**evidence, "failures": [], "pool_age_minutes": pool_age},
        exit_conditions=exit_conditions,
        expected_costs={"fee_bps": format(quote_fee_bps, "f") if quote_fee_bps is not None else None},
    )


def exit_reason(
    *,
    entry_price: Decimal,
    market_price: Decimal | None,
    market_fresh: bool,
    quote_price: Decimal | None,
    liquidity_now: Decimal | None,
    liquidity_entry: Decimal | None,
    opened_at: datetime,
    now: datetime,
    high_water: Decimal | None,
    conditions: dict,
    security_failed: bool,
) -> str | None:
    """Decide whether an exit should be attempted. A missing quote is handled by the caller."""
    if security_failed:
        return "security_escalation"
    max_hold = int(conditions.get("max_holding_minutes") or 1440)
    if (now - opened_at) >= timedelta(minutes=max_hold):
        return "max_holding_time"
    if liquidity_now is not None and liquidity_entry is not None and liquidity_entry > 0:
        drop = (liquidity_entry - liquidity_now) / liquidity_entry
        if drop >= D(conditions.get("liquidity_drop_pct") or "50") / Decimal("100"):
            return "liquidity_deterioration"
    price = quote_price if quote_price is not None else (market_price if market_fresh else None)
    if price is None or entry_price <= 0:
        return None
    change = (price - entry_price) / entry_price
    adverse = D(conditions.get("adverse_price_pct") or "8") / Decimal("100")
    take = D(conditions.get("take_profit_pct") or "20") / Decimal("100")
    trail = D(conditions.get("trailing_stop_pct") or "10") / Decimal("100")
    if change <= -adverse:
        return "adverse_price"
    if change >= take:
        return "take_profit"
    if high_water is not None and high_water > 0 and price <= high_water * (Decimal("1") - trail) and high_water > entry_price:
        return "trailing_stop"
    return None
