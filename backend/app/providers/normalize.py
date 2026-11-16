"""Normalize provider payloads into Observation records. Untrusted text is not copied into prompts."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.analysis import Observation
from app.money import MAJOR_MINTS, optional_d


def _dt(value) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        # DexScreener pairCreatedAt is milliseconds.
        seconds = float(value) / 1000.0 if float(value) > 10_000_000_000 else float(value)
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    if isinstance(value, str):
        text = value.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None


def _int(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_geckoterminal_pools(payload: dict, *, mode: str, observed_at: datetime) -> list[Observation]:
    included = {item.get("id"): item for item in payload.get("included") or [] if isinstance(item, dict)}
    rows: list[Observation] = []
    for pool in payload.get("data") or []:
        if not isinstance(pool, dict):
            continue
        pool_id = str(pool.get("id") or "")
        if pool_id and not pool_id.startswith("solana_"):
            continue
        attr = pool.get("attributes") or {}
        rel = pool.get("relationships") or {}
        base_ref = ((rel.get("base_token") or {}).get("data") or {}).get("id")
        quote_ref = ((rel.get("quote_token") or {}).get("data") or {}).get("id")
        base = (included.get(base_ref) or {}).get("attributes") or {}
        quote = (included.get(quote_ref) or {}).get("attributes") or {}
        mint = base.get("address")
        if not mint or mint in MAJOR_MINTS:
            continue
        tx = attr.get("transactions") or {}
        m5 = tx.get("m5") or {}
        h1 = tx.get("h1") or {}
        volume = attr.get("volume_usd") or {}
        change = attr.get("price_change_percentage") or {}
        dex = None
        dex_rel = ((rel.get("dex") or {}).get("data") or {}).get("id")
        if isinstance(dex_rel, str):
            dex = dex_rel.split("_", 1)[-1]
        rows.append(
            Observation(
                mode=mode,
                mint=str(mint),
                name=str(base.get("name") or "unknown")[:160],
                symbol=str(base.get("symbol") or "unknown")[:32],
                decimals=int(base.get("decimals") or 6),
                pool_address=str(attr.get("address") or ""),
                dex=dex,
                quote_mint=quote.get("address"),
                pool_created_at=_dt(attr.get("pool_created_at")),
                token_created_at=None,
                price_usd=optional_d(attr.get("base_token_price_usd")),
                market_cap_usd=optional_d(attr.get("market_cap_usd")),
                fdv_usd=optional_d(attr.get("fdv_usd")),
                pool_liquidity_usd=optional_d(attr.get("reserve_in_usd")),
                token_liquidity_usd=None,
                volume_m5=optional_d(volume.get("m5")),
                volume_h1=optional_d(volume.get("h1")),
                price_change_m5=optional_d(change.get("m5")),
                price_change_h1=optional_d(change.get("h1")),
                buys_m5=_int(m5.get("buys")),
                sells_m5=_int(m5.get("sells")),
                buys_h1=_int(h1.get("buys")),
                sells_h1=_int(h1.get("sells")),
                unique_buyers_m5=_int(m5.get("buyers")),
                holder_count=None,
                provider="geckoterminal",
                provider_reported_at=None,
                observed_at=observed_at,
                promoted=False,
            )
        )
    return rows


def parse_dexscreener_pairs(payload, *, mode: str, observed_at: datetime, promoted_mints: set[str]) -> list[Observation]:
    if isinstance(payload, dict):
        pairs = payload.get("pairs") or payload.get("data") or []
    else:
        pairs = payload or []
    rows: list[Observation] = []
    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        if pair.get("chainId") not in (None, "solana"):
            continue
        base = pair.get("baseToken") or {}
        quote = pair.get("quoteToken") or {}
        mint = base.get("address")
        if not mint or mint in MAJOR_MINTS:
            continue
        txns = pair.get("txns") or {}
        m5 = txns.get("m5") or {}
        h1 = txns.get("h1") or {}
        volume = pair.get("volume") or {}
        change = pair.get("priceChange") or {}
        liquidity = pair.get("liquidity") or {}
        rows.append(
            Observation(
                mode=mode,
                mint=str(mint),
                name=str(base.get("name") or "unknown")[:160],
                symbol=str(base.get("symbol") or "unknown")[:32],
                decimals=6,
                pool_address=str(pair.get("pairAddress") or ""),
                dex=pair.get("dexId"),
                quote_mint=quote.get("address"),
                pool_created_at=_dt(pair.get("pairCreatedAt")),
                token_created_at=None,
                price_usd=optional_d(pair.get("priceUsd")),
                market_cap_usd=optional_d(pair.get("marketCap")),
                fdv_usd=optional_d(pair.get("fdv")),
                pool_liquidity_usd=optional_d(liquidity.get("usd")),
                token_liquidity_usd=None,
                volume_m5=optional_d(volume.get("m5")),
                volume_h1=optional_d(volume.get("h1")),
                price_change_m5=optional_d(change.get("m5")),
                price_change_h1=optional_d(change.get("h1")),
                buys_m5=_int(m5.get("buys")),
                sells_m5=_int(m5.get("sells")),
                buys_h1=_int(h1.get("buys")),
                sells_h1=_int(h1.get("sells")),
                unique_buyers_m5=None,
                holder_count=None,
                provider="dexscreener",
                provider_reported_at=None,
                observed_at=observed_at,
                promoted=str(mint) in promoted_mints or bool((pair.get("boosts") or {}).get("active")),
            )
        )
    return rows


def parse_boosted_mints(payload) -> set[str]:
    """Boosted listings are promotional. They are flags, not demand."""
    rows = payload if isinstance(payload, list) else []
    mints = set()
    for row in rows:
        if isinstance(row, dict) and row.get("chainId") == "solana" and row.get("tokenAddress"):
            mints.add(str(row["tokenAddress"]))
    return mints


def parse_ohlcv(payload: dict) -> list[dict]:
    attributes = ((payload.get("data") or {}).get("attributes") or {})
    candles = []
    for row in attributes.get("ohlcv_list") or []:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            continue
        candles.append(
            {
                "timestamp": int(row[0]),
                "open": str(row[1]),
                "high": str(row[2]),
                "low": str(row[3]),
                "close": str(row[4]),
                "volume": str(row[5]),
                "provider": "geckoterminal",
            }
        )
    candles.sort(key=lambda item: item["timestamp"])
    return candles
