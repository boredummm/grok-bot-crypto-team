"""Read-only Solana RPC account inspection. This module never sends transactions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.analysis import AuthorityFacts
from app.providers.http import ProviderClient

TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"


@dataclass
class MintInspection:
    facts: AuthorityFacts
    supply: int | None
    decimals: int | None


def parse_mint_account(payload: dict, *, observed_at: datetime, source: str) -> MintInspection:
    value = ((payload.get("result") or {}).get("value")) if isinstance(payload, dict) else None
    if not value:
        return MintInspection(
            facts=AuthorityFacts(source=source, observed_at=observed_at, parsed=False),
            supply=None,
            decimals=None,
        )
    owner = value.get("owner")
    data = value.get("data")
    parsed = None
    if isinstance(data, dict):
        parsed = (data.get("parsed") or {}).get("info")
        if (data.get("parsed") or {}).get("type") not in (None, "mint"):
            parsed = None
    if not isinstance(parsed, dict):
        program = TOKEN_2022 if owner == TOKEN_2022 else (TOKEN_PROGRAM if owner == TOKEN_PROGRAM else owner)
        return MintInspection(
            facts=AuthorityFacts(
                source=source,
                observed_at=observed_at,
                token_program=program,
                parsed=False,
            ),
            supply=None,
            decimals=None,
        )
    extensions = []
    fee_bps = None
    for ext in parsed.get("extensions") or []:
        if not isinstance(ext, dict):
            continue
        name = str(ext.get("extension") or "")
        extensions.append(name)
        if name == "transferFeeConfig":
            state = ext.get("state") or {}
            newer = state.get("newerTransferFee") or state.get("olderTransferFee") or {}
            raw = newer.get("transferFeeBasisPoints")
            if raw is not None:
                fee_bps = Decimal(str(raw))
    supply_text = parsed.get("supply")
    supply = int(supply_text) if supply_text is not None else None
    return MintInspection(
        facts=AuthorityFacts(
            source=source,
            observed_at=observed_at,
            token_program=owner,
            mint_authority_active=parsed.get("mintAuthority") not in (None, ""),
            freeze_authority_active=parsed.get("freezeAuthority") not in (None, ""),
            extensions=extensions,
            transfer_fee_bps=fee_bps if fee_bps is not None else (Decimal("0") if owner == TOKEN_PROGRAM else None),
            parsed=True,
        ),
        supply=supply,
        decimals=parsed.get("decimals"),
    )


def top_holder_pct(payload: dict, supply: int | None) -> Decimal | None:
    if not supply:
        return None
    values = ((payload.get("result") or {}).get("value")) or []
    if not values:
        return None
    largest = 0
    for row in values:
        try:
            largest = max(largest, int(row.get("amount") or 0))
        except (TypeError, ValueError):
            continue
    if largest <= 0:
        return None
    return (Decimal(largest) / Decimal(supply)) * Decimal("100")


class SolanaRpc:
    def __init__(self, client: ProviderClient, url: str) -> None:
        self.client = client
        self.url = url
        self.client.name = "solana_rpc"
        self.client.configured = bool(url)

    async def _rpc(self, method: str, params: list) -> dict:
        # JSON-RPC uses POST. The shared client is GET-oriented, so call httpx directly
        # while still applying the rate limiter and circuit breaker.
        if not self.client.circuit.allow():
            from app.providers.http import ProviderError

            raise ProviderError(self.client.name, "circuit open")
        await self.client.limiter.acquire()
        import time

        started = time.perf_counter()
        response = await self.client._http().post(
            self.url,
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        )
        self.client.last_latency_ms = int((time.perf_counter() - started) * 1000)
        response.raise_for_status()
        body = response.json()
        if isinstance(body, dict) and body.get("error"):
            self.client.circuit.failure()
            self.client.last_error = str(body["error"])[:300]
            from app.providers.http import ProviderError

            raise ProviderError(self.client.name, "rpc error")
        self.client.circuit.success()
        self.client.last_error = None
        return body

    async def inspect_mint(self, mint: str, observed_at: datetime) -> MintInspection:
        payload = await self._rpc("getAccountInfo", [mint, {"encoding": "jsonParsed"}])
        inspection = parse_mint_account(payload, observed_at=observed_at, source="solana_rpc")
        if inspection.facts.parsed and inspection.supply:
            try:
                largest = await self._rpc("getTokenLargestAccounts", [mint])
                inspection.facts.top_holder_pct = top_holder_pct(largest, inspection.supply)
            except Exception:
                inspection.facts.top_holder_pct = None
        return inspection
