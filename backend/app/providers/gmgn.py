"""Read-only GMGN OpenAPI adapter (https://openapi.gmgn.ai).

Implements GMGN's documented 'Exist Auth' mode (`X-APIKEY` header + `timestamp`
Unix seconds + fresh `client_id` UUID per request) for market, token, security,
kline, and paper quote lookups.

Write/trading endpoints and signed headers (`X-Signature`) are structurally
blocked at the service boundary. All fills in SOL SWARM remain paper-only.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.analysis import AuthorityFacts, Observation
from app.money import MAJOR_MINTS, USDC_MINT, optional_d
from app.providers.http import ProviderClient, ProviderError, ProviderSnapshot
from app.risk import QuoteView

FORBIDDEN_GMGN_ROUTES = (
    "/v1/trade/swap",
    "/v1/trade/multi_swap",
    "/v1/trade/strategy/create",
    "/v1/trade/strategy/cancel",
    "/v1/cooking/create_token",
    "/v1/trade/follow_wallet",
    "/v1/user/wallet_holdings",
)

ALLOWED_READ_ROUTES = {
    "/v1/market/rank",
    "/v1/market/token_kline",
    "/v1/market/search",
    "/v1/token/info",
    "/v1/token/security",
    "/v1/token/pool_info",
    "/v1/trade/quote",
    "/v1/trenches",
}

INJECTION_PATTERNS = (
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.IGNORECASE),
    re.compile(r"system\s*prompt", re.IGNORECASE),
    re.compile(r"drain\s+wallet", re.IGNORECASE),
    re.compile(r"execute\s+swap", re.IGNORECASE),
    re.compile(r"transfer\s+all\s+sol", re.IGNORECASE),
)

PUBLIC_DEMO_TEST_KEY = "gmgn_solbscbaseethmonadtron"


def clean_x_handle(raw: str | None) -> str | None:
    """Normalize an on-chain/dossier X handle. Never guess or search fallback handles."""
    if not raw or not isinstance(raw, str):
        return None
    handle = raw.strip()
    for prefix in ("https://x.com/", "https://twitter.com/", "http://x.com/", "http://twitter.com/"):
        if handle.lower().startswith(prefix):
            handle = handle[len(prefix) :]
            break
    handle = handle.lstrip("@").split("?", 1)[0].split("/", 1)[0].strip()
    if not handle or len(handle) > 15:
        return None
    if not handle.replace("_", "").isalnum():
        return None
    return handle


def sanitize_untrusted_text(value: str | None, *, max_len: int = 240) -> tuple[str, bool]:
    """Treat token names, symbols, and descriptions strictly as untrusted display text."""
    if not value:
        return "", False
    cleaned = str(value).replace("\x00", " ").strip()
    suspicious = any(pattern.search(cleaned) for pattern in INJECTION_PATTERNS)
    if suspicious:
        for pattern in INJECTION_PATTERNS:
            cleaned = pattern.sub("[neutralized-untrusted-text]", cleaned)
    return cleaned[:max_len], suspicious


def strip_gmgn_tx_fields(payload: dict) -> dict:
    cleaned = dict(payload)
    for key in ("raw_tx", "transaction", "tx", "swapTransaction", "signed_tx", "signature"):
        cleaned.pop(key, None)
    return cleaned


class GmgnRankItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    address: str
    symbol: str | None = None
    name: str | None = None
    decimals: int | None = 6
    price: str | float | int | None = None
    liquidity: str | float | int | None = None
    market_cap: str | float | int | None = None
    volume: str | float | int | None = None
    swaps: int | None = None
    buys: int | None = None
    sells: int | None = None
    holder_count: int | None = None
    price_change_percent5m: str | float | int | None = None
    price_change_percent1h: str | float | int | None = None
    open_timestamp: int | float | None = None
    creation_timestamp: int | float | None = None
    exchange: str | None = None
    pool_address: str | None = None
    renounced_mint: int | bool | None = None
    renounced_freeze_account: int | bool | None = None
    smart_degen_count: int | None = None
    renowned_count: int | None = None


class GmgnSecurityPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    renounced_mint: int | bool | None = None
    renounced_freeze_account: int | bool | None = None
    buy_tax: str | float | int | None = None
    sell_tax: str | float | int | None = None
    top_10_holder_rate: str | float | int | None = None
    dev_team_hold_rate: str | float | int | None = None
    creator_balance_rate: str | float | int | None = None
    creator_token_status: str | None = None
    suspected_insider_hold_rate: str | float | int | None = None
    rug_ratio: str | float | int | None = None
    is_wash_trading: bool | int | str | None = None
    rat_trader_amount_rate: str | float | int | None = None
    bundler_trader_amount_rate: str | float | int | None = None
    sniper_count: int | None = None
    burn_status: str | None = None


@dataclass
class GmgnDossier:
    mint: str
    observation: Observation | None
    facts: AuthorityFacts | None
    x_handle: str | None
    description: str
    suspicious_metadata: bool
    top_10_percent: Decimal | None
    dev_hold_percent: Decimal | None
    rug_ratio: Decimal | None
    wash_trading: bool | None
    rat_trader_pct: Decimal | None
    bundler_pct: Decimal | None
    smart_degen_count: int | None
    sniper_count: int | None
    creator_status: str | None


@dataclass
class EvidenceComparison:
    mint: str
    gmgn_observed_at: str | None
    secondary_provider: str | None
    secondary_observed_at: str | None
    price_diff_pct: str | None
    liquidity_diff_pct: str | None
    conflicts: list[str]
    stale: bool


def _unix_dt(value: int | float | None) -> datetime | None:
    if value is None:
        return None
    ts = float(value)
    if ts <= 0:
        return None
    if ts > 10_000_000_000:
        ts /= 1000.0
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def compare_provider_evidence(
    gmgn_obs: Observation | None,
    secondary_obs: Observation | None,
    *,
    now: datetime,
    max_age_seconds: int = 90,
) -> EvidenceComparison:
    mint = (gmgn_obs.mint if gmgn_obs else None) or (secondary_obs.mint if secondary_obs else "")
    conflicts: list[str] = []
    stale = False
    price_diff_pct: Decimal | None = None
    liq_diff_pct: Decimal | None = None

    if gmgn_obs is not None:
        age = (now - gmgn_obs.observed_at).total_seconds()
        if age > max_age_seconds:
            stale = True
            conflicts.append("gmgn_stale_observation")
    if secondary_obs is not None:
        sec_age = (now - secondary_obs.observed_at).total_seconds()
        if sec_age > max_age_seconds:
            stale = True
            conflicts.append(f"{secondary_obs.provider}_stale_observation")

    if (
        gmgn_obs is not None
        and secondary_obs is not None
        and gmgn_obs.price_usd is not None
        and secondary_obs.price_usd is not None
        and secondary_obs.price_usd > 0
    ):
        price_diff_pct = abs(gmgn_obs.price_usd - secondary_obs.price_usd) / secondary_obs.price_usd * Decimal("100")
        if price_diff_pct > Decimal("15"):
            conflicts.append("price_divergence_gt_15pct")

    if (
        gmgn_obs is not None
        and secondary_obs is not None
        and gmgn_obs.pool_liquidity_usd is not None
        and secondary_obs.pool_liquidity_usd is not None
        and secondary_obs.pool_liquidity_usd > 0
    ):
        liq_diff_pct = (
            abs(gmgn_obs.pool_liquidity_usd - secondary_obs.pool_liquidity_usd)
            / secondary_obs.pool_liquidity_usd
            * Decimal("100")
        )
        if liq_diff_pct > Decimal("25"):
            conflicts.append("liquidity_divergence_gt_25pct")

    return EvidenceComparison(
        mint=mint,
        gmgn_observed_at=gmgn_obs.observed_at.isoformat() if gmgn_obs else None,
        secondary_provider=secondary_obs.provider if secondary_obs else None,
        secondary_observed_at=secondary_obs.observed_at.isoformat() if secondary_obs else None,
        price_diff_pct=format(price_diff_pct.quantize(Decimal("0.01")), "f") if price_diff_pct is not None else None,
        liquidity_diff_pct=format(liq_diff_pct.quantize(Decimal("0.01")), "f") if liq_diff_pct is not None else None,
        conflicts=conflicts,
        stale=stale,
    )


class GmgnAdapter:
    """Isolated read-only GMGN OpenAPI client."""

    def __init__(self, client: ProviderClient, api_key: str, base_url: str = "https://openapi.gmgn.ai") -> None:
        self.client = client
        self.api_key = (api_key or "").strip()
        self.base_url = base_url.rstrip("/")
        self.client.name = "gmgn"
        self.client.configured = bool(self.api_key)
        self.banned_until_unix: int = 0
        self.plan_upgrade_required: bool = False
        self.is_demo_public_key: bool = self.api_key == PUBLIC_DEMO_TEST_KEY

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def snapshot(self) -> ProviderSnapshot:
        snap = self.client.snapshot()
        now_unix = int(time.time())
        if not self.configured:
            snap.status = "unconfigured"
            snap.detail = "GMGN_API_KEY not set. Read-only GMGN feeds inactive; paper safety unaffected."
        elif self.banned_until_unix > now_unix:
            snap.status = "rate_limited"
            snap.detail = f"GMGN cooldown active until unix {self.banned_until_unix}."
        elif self.plan_upgrade_required:
            snap.status = "plan_limited"
            snap.detail = "Endpoint weight exceeds current GMGN plan tier."
        elif self.is_demo_public_key:
            snap.detail = "Using GMGN public test key (rate-limited, verification only)."
        else:
            snap.detail = "Read-only Exist Auth active. Swap signing routes are permanently disabled."
        return snap

    def _guard_route(self, sub_path: str, headers: dict[str, str]) -> None:
        if any(sub_path.startswith(route) for route in FORBIDDEN_GMGN_ROUTES):
            raise ProviderError("gmgn", f"refusing write or trading route {sub_path}")
        if sub_path not in ALLOWED_READ_ROUTES:
            raise ProviderError("gmgn", f"route {sub_path} is not in the read-only allowlist")
        if any(k.lower() == "x-signature" for k in headers):
            raise ProviderError("gmgn", "signed trading requests are forbidden in SOL SWARM")

    def _auth_query(self, extra: dict[str, Any]) -> dict[str, Any]:
        return {
            **extra,
            "timestamp": int(time.time()),
            "client_id": str(uuid.uuid4()),
        }

    def _headers(self) -> dict[str, str]:
        return {
            "X-APIKEY": self.api_key,
            "Content-Type": "application/json",
            "User-Agent": "sol-swarm-paper/0.2",
        }

    async def request_read(
        self,
        method: str,
        sub_path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        if not self.configured:
            raise ProviderError("gmgn", "GMGN_API_KEY is not configured")
        headers = self._headers()
        self._guard_route(sub_path, headers)
        now_unix = int(time.time())
        if self.banned_until_unix > now_unix:
            raise ProviderError(
                "gmgn",
                f"GMGN rate-limit ban active until {self.banned_until_unix}; skipping to avoid ban extension",
            )
        if not self.client.circuit.allow():
            raise ProviderError("gmgn", "circuit open")

        await self.client.limiter.acquire()
        query = self._auth_query(params or {})
        url = f"{self.base_url}{sub_path}"
        started = time.perf_counter()
        try:
            http = self.client._http()
            if method.upper() == "POST":
                res = await http.post(url, params=query, headers=headers, json=json_body or {})
            else:
                res = await http.get(url, params=query, headers=headers)
            self.client.last_latency_ms = int((time.perf_counter() - started) * 1000)
        except Exception as exc:
            self.client.circuit.failure()
            self.client.last_error = str(exc)[:300]
            raise ProviderError("gmgn", str(exc)) from exc

        reset_header = res.headers.get("x-ratelimit-reset")
        reset_unix = int(reset_header) if reset_header and reset_header.isdigit() else None

        try:
            envelope = res.json()
        except Exception as exc:
            self.client.circuit.failure()
            self.client.last_error = f"HTTP {res.status_code} non-JSON response"
            raise ProviderError("gmgn", self.client.last_error) from exc

        if not isinstance(envelope, dict):
            self.client.circuit.failure()
            raise ProviderError("gmgn", "unexpected GMGN envelope")

        code = envelope.get("code")
        api_error = str(envelope.get("error") or "")
        body_reset = envelope.get("reset_at")
        if isinstance(body_reset, (int, float)) and int(body_reset) > 0:
            reset_unix = int(body_reset)

        if res.status_code == 403 and api_error in {"PLAN_UPGRADE_REQUIRED", "PRO_PLAN_REQUIRED"}:
            self.plan_upgrade_required = True
            msg = f"GMGN {api_error}: endpoint unavailable on current plan"
            self.client.last_error = msg
            raise ProviderError("gmgn", msg)

        if res.status_code == 429 or api_error in {
            "RATE_LIMIT_EXCEEDED",
            "RATE_LIMIT_BANNED",
            "RATE_LIMIT_BANNED_MAX",
        }:
            self.banned_until_unix = reset_unix or (int(time.time()) + (300 if api_error == "RATE_LIMIT_BANNED_MAX" else 30))
            msg = f"GMGN {api_error or '429'} until {self.banned_until_unix}"
            self.client.last_error = msg
            raise ProviderError("gmgn", msg)

        if res.status_code >= 400 or (code is not None and code != 0):
            self.client.circuit.failure()
            msg = f"GMGN error code={code} status={res.status_code} {envelope.get('message') or api_error}".strip()
            self.client.last_error = msg[:300]
            raise ProviderError("gmgn", self.client.last_error)

        self.client.circuit.success()
        self.client.last_error = None
        return envelope.get("data")

    async def trending_sol(
        self,
        *,
        observed_at: datetime,
        mode: str = "live",
        interval: str = "5m",
        limit: int = 20,
    ) -> list[Observation]:
        data = await self.request_read(
            "GET",
            "/v1/market/rank",
            params={"chain": "sol", "interval": interval, "limit": min(max(limit, 1), 100), "order_by": "volume"},
        )
        items = (data or {}).get("rank") if isinstance(data, dict) else []
        rows: list[Observation] = []
        for raw in items or []:
            if not isinstance(raw, dict):
                continue
            item = GmgnRankItem.model_validate(raw)
            if not item.address or item.address in MAJOR_MINTS:
                continue
            name, _ = sanitize_untrusted_text(item.name or "unknown", max_len=160)
            symbol, _ = sanitize_untrusted_text(item.symbol or "unknown", max_len=32)
            rows.append(
                Observation(
                    mode=mode,
                    mint=item.address,
                    name=name or "unknown",
                    symbol=symbol or "unknown",
                    decimals=int(item.decimals or 6),
                    pool_address=str(item.pool_address or f"gmgn:{item.address}"),
                    dex=item.exchange or "gmgn",
                    quote_mint=USDC_MINT,
                    pool_created_at=_unix_dt(item.open_timestamp or item.creation_timestamp),
                    token_created_at=_unix_dt(item.creation_timestamp),
                    price_usd=optional_d(item.price),
                    market_cap_usd=optional_d(item.market_cap),
                    fdv_usd=optional_d(item.market_cap),
                    pool_liquidity_usd=optional_d(item.liquidity),
                    token_liquidity_usd=optional_d(item.liquidity),
                    volume_m5=optional_d(item.volume) if interval == "5m" else None,
                    volume_h1=optional_d(item.volume) if interval == "1h" else optional_d(item.volume),
                    price_change_m5=optional_d(item.price_change_percent5m),
                    price_change_h1=optional_d(item.price_change_percent1h),
                    buys_m5=item.buys if interval == "5m" else None,
                    sells_m5=item.sells if interval == "5m" else None,
                    buys_h1=item.buys,
                    sells_h1=item.sells,
                    unique_buyers_m5=None,
                    holder_count=item.holder_count,
                    provider="gmgn",
                    provider_reported_at=observed_at,
                    observed_at=observed_at,
                    promoted=False,
                )
            )
        return rows

    async def token_dossier(self, address: str, *, mode: str, observed_at: datetime) -> GmgnDossier:
        info = await self.request_read("GET", "/v1/token/info", params={"chain": "sol", "address": address})
        sec_raw = await self.request_read("GET", "/v1/token/security", params={"chain": "sol", "address": address})
        info_dict = info if isinstance(info, dict) else {}
        sec = GmgnSecurityPayload.model_validate(sec_raw if isinstance(sec_raw, dict) else {})

        name, susp_name = sanitize_untrusted_text(info_dict.get("name"), max_len=160)
        symbol, susp_sym = sanitize_untrusted_text(info_dict.get("symbol"), max_len=32)
        link = info_dict.get("link") if isinstance(info_dict.get("link"), dict) else {}
        desc, susp_desc = sanitize_untrusted_text(link.get("description"), max_len=240)
        x_handle = clean_x_handle(link.get("twitter_username"))

        price_obj = info_dict.get("price") if isinstance(info_dict.get("price"), dict) else {}
        pool_obj = info_dict.get("pool") if isinstance(info_dict.get("pool"), dict) else {}
        stat_obj = info_dict.get("stat") if isinstance(info_dict.get("stat"), dict) else {}
        dev_obj = info_dict.get("dev") if isinstance(info_dict.get("dev"), dict) else {}
        tags_obj = info_dict.get("wallet_tags_stat") if isinstance(info_dict.get("wallet_tags_stat"), dict) else {}

        current_price = optional_d(price_obj.get("price"))
        circ = optional_d(info_dict.get("circulating_supply") or info_dict.get("total_supply"))
        mcap = (current_price * circ) if (current_price is not None and circ is not None) else None
        p5m = optional_d(price_obj.get("price_5m"))
        p1h = optional_d(price_obj.get("price_1h"))
        chg_m5 = ((current_price - p5m) / p5m * Decimal("100")) if (current_price and p5m and p5m > 0) else None
        chg_h1 = ((current_price - p1h) / p1h * Decimal("100")) if (current_price and p1h and p1h > 0) else None

        promoted = bool(dev_obj.get("dexscr_ad") or dev_obj.get("dexscr_boost_fee"))
        obs = Observation(
            mode=mode,
            mint=address,
            name=name or "unknown",
            symbol=symbol or "unknown",
            decimals=int(info_dict.get("decimals") or 6),
            pool_address=str(pool_obj.get("pool_address") or info_dict.get("biggest_pool_address") or ""),
            dex=pool_obj.get("exchange") or "gmgn",
            quote_mint=pool_obj.get("quote_address") or USDC_MINT,
            pool_created_at=_unix_dt(pool_obj.get("creation_timestamp") or info_dict.get("open_timestamp")),
            token_created_at=_unix_dt(info_dict.get("creation_timestamp")),
            price_usd=current_price,
            market_cap_usd=mcap,
            fdv_usd=mcap,
            pool_liquidity_usd=optional_d(pool_obj.get("liquidity") or info_dict.get("liquidity")),
            token_liquidity_usd=optional_d(info_dict.get("liquidity")),
            volume_m5=optional_d(price_obj.get("volume_5m")),
            volume_h1=optional_d(price_obj.get("volume_1h")),
            price_change_m5=chg_m5,
            price_change_h1=chg_h1,
            buys_m5=int(price_obj["buys_5m"]) if price_obj.get("buys_5m") is not None else None,
            sells_m5=int(price_obj["sells_5m"]) if price_obj.get("sells_5m") is not None else None,
            buys_h1=int(price_obj["buys_1h"]) if price_obj.get("buys_1h") is not None else None,
            sells_h1=int(price_obj["sells_1h"]) if price_obj.get("sells_1h") is not None else None,
            unique_buyers_m5=None,
            holder_count=int(info_dict["holder_count"]) if info_dict.get("holder_count") is not None else None,
            provider="gmgn",
            provider_reported_at=observed_at,
            observed_at=observed_at,
            promoted=promoted,
        )

        top10_rate = optional_d(sec.top_10_holder_rate or stat_obj.get("top_10_holder_rate"))
        top10_pct = (top10_rate * Decimal("100")) if top10_rate is not None and top10_rate <= 1 else top10_rate
        dev_rate = optional_d(sec.creator_balance_rate or sec.dev_team_hold_rate or stat_obj.get("creator_hold_rate"))
        dev_pct = (dev_rate * Decimal("100")) if dev_rate is not None and dev_rate <= 1 else dev_rate
        rug_ratio = optional_d(sec.rug_ratio)
        wash = bool(sec.is_wash_trading) if sec.is_wash_trading is not None else None
        sell_tax = optional_d(sec.sell_tax) or Decimal("0")
        buy_tax = optional_d(sec.buy_tax) or Decimal("0")
        fee_bps = max(sell_tax, buy_tax) * Decimal("10000")

        mint_active = (not bool(sec.renounced_mint)) if sec.renounced_mint is not None else None
        freeze_active = (not bool(sec.renounced_freeze_account)) if sec.renounced_freeze_account is not None else None
        malicious = bool((rug_ratio is not None and rug_ratio >= Decimal("0.75")) or wash is True or (susp_name or susp_sym or susp_desc))

        facts = AuthorityFacts(
            source="gmgn_security",
            observed_at=observed_at,
            token_program="TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
            mint_authority_active=mint_active,
            freeze_authority_active=freeze_active,
            extensions=["transferFeeConfig"] if fee_bps > 0 else [],
            transfer_fee_bps=fee_bps,
            top_holder_pct=top10_pct,
            malicious_flag=malicious,
            parsed=mint_active is not None and freeze_active is not None,
        )

        rat_rate = optional_d(sec.rat_trader_amount_rate or stat_obj.get("top_rat_trader_percentage"))
        bundler_rate = optional_d(sec.bundler_trader_amount_rate or stat_obj.get("top_bundler_trader_percentage"))

        return GmgnDossier(
            mint=address,
            observation=obs,
            facts=facts,
            x_handle=x_handle,
            description=desc,
            suspicious_metadata=bool(susp_name or susp_sym or susp_desc),
            top_10_percent=top10_pct,
            dev_hold_percent=dev_pct,
            rug_ratio=rug_ratio,
            wash_trading=wash,
            rat_trader_pct=(rat_rate * Decimal("100")) if rat_rate is not None and rat_rate <= 1 else rat_rate,
            bundler_pct=(bundler_rate * Decimal("100")) if bundler_rate is not None and bundler_rate <= 1 else bundler_rate,
            smart_degen_count=tags_obj.get("smart_wallets"),
            sniper_count=sec.sniper_count or tags_obj.get("sniper_wallets"),
            creator_status=sec.creator_token_status or dev_obj.get("creator_token_status"),
        )

    async def token_kline(
        self,
        address: str,
        *,
        resolution: str = "5m",
        from_ts: int | None = None,
        to_ts: int | None = None,
    ) -> list[dict]:
        if resolution == "1s":
            raise ProviderError("gmgn", "1s kline requires Pro plan; use 1m or 5m resolution")
        params: dict[str, Any] = {"chain": "sol", "address": address, "resolution": resolution}
        if from_ts is not None:
            params["from"] = from_ts
        if to_ts is not None:
            params["to"] = to_ts
        data = await self.request_read("GET", "/v1/market/token_kline", params=params)
        items = (data or {}).get("list") if isinstance(data, dict) else []
        candles: list[dict] = []
        for row in items or []:
            if not isinstance(row, dict) or row.get("time") is None:
                continue
            # GMGN kline naming: `amount` is USD volume; `volume` is token count.
            candles.append(
                {
                    "timestamp": int(row["time"]),
                    "open": str(row.get("open") or "0"),
                    "high": str(row.get("high") or "0"),
                    "low": str(row.get("low") or "0"),
                    "close": str(row.get("close") or "0"),
                    "volume": str(row.get("amount") or "0"),
                    "token_volume": str(row.get("volume") or "0"),
                    "provider": "gmgn",
                }
            )
        candles.sort(key=lambda item: item["timestamp"])
        return candles

    async def paper_quote(
        self,
        *,
        input_mint: str,
        output_mint: str,
        amount_base_units: int,
        side: str,
        observed_at: datetime,
        decimals_out: int,
        slippage_bps: int = 50,
    ) -> QuoteView | None:
        """Fetch a read-only GMGN swap quote for paper simulation. Never submits an order."""
        if not self.configured:
            return None
        data = await self.request_read(
            "GET",
            "/v1/trade/quote",
            params={
                "chain": "sol",
                "from_address": "11111111111111111111111111111111",
                "input_token": input_mint,
                "output_token": output_mint,
                "input_amount": str(amount_base_units),
                "slippage": round(slippage_bps / 100.0, 2),
            },
        )
        if not isinstance(data, dict):
            return None
        cleaned = strip_gmgn_tx_fields(data)
        quote_block = cleaned.get("quote") if isinstance(cleaned.get("quote"), dict) else cleaned
        in_raw = optional_d(quote_block.get("in_amount") or quote_block.get("input_amount") or amount_base_units)
        out_raw = optional_d(quote_block.get("out_amount") or quote_block.get("output_amount"))
        if in_raw is None or out_raw is None or in_raw <= 0 or out_raw <= 0:
            return None
        if input_mint == USDC_MINT:
            notional = in_raw / Decimal("1000000")
            qty = out_raw / (Decimal(10) ** decimals_out)
            price = notional / qty if qty > 0 else Decimal("0")
        elif output_mint == USDC_MINT:
            notional = out_raw / Decimal("1000000")
            qty = in_raw / (Decimal(10) ** decimals_out)
            price = notional / qty if qty > 0 else Decimal("0")
        else:
            return None
        if price <= 0:
            return None
        impact_pct = optional_d(quote_block.get("price_impact") or quote_block.get("price_impact_pct")) or Decimal("0")
        impact_bps = impact_pct * Decimal("10000") if impact_pct < 1 else impact_pct * Decimal("100")
        fee_usd = optional_d(quote_block.get("fee_usd") or quote_block.get("gas_usd")) or (notional * Decimal("0.0045"))
        fee_bps = (fee_usd / notional * Decimal("10000")) if notional > 0 else Decimal("45")
        return QuoteView(
            price_usd=price,
            notional_usd=notional,
            fee_usd=fee_usd,
            fee_bps=fee_bps,
            slippage_bps=Decimal(slippage_bps),
            price_impact_bps=impact_bps,
            observed_at=observed_at,
            side=side,
            input_mint=input_mint,
            output_mint=output_mint,
            source="gmgn_quote",
            available_notional_usd=notional,
        )
