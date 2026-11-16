"""Jupiter Tokens API v2 and Swap API v2 order quotes.

The order endpoint also returns a transaction. This adapter discards that field
and never calls /execute, /build, or /submit.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.money import USDC_MINT, optional_d
from app.providers.http import ProviderClient, ProviderError
from app.risk import QuoteView


class JupiterToken(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    name: str | None = None
    symbol: str | None = None
    decimals: int | None = None
    tokenProgram: str | None = None
    holderCount: int | None = None
    audit: dict | None = None
    organicScoreLabel: str | None = None
    isVerified: bool | None = None
    tags: list[str] | None = None


class JupiterOrderQuote(BaseModel):
    """Quote fields only. The transaction blob is intentionally absent."""

    model_config = ConfigDict(extra="ignore")
    inputMint: str
    outputMint: str
    inAmount: str
    outAmount: str
    otherAmountThreshold: str | None = None
    slippageBps: int | None = None
    priceImpactPct: str | None = None
    routePlan: list | None = None
    platformFee: dict | None = None
    error: str | None = None
    errorCode: str | None = Field(default=None)


FORBIDDEN_JUPITER_PATHS = ("/execute", "/build", "/submit", "/swap/v1")


def strip_transaction(payload: dict) -> dict:
    cleaned = dict(payload)
    for key in ("transaction", "tx", "swapTransaction", "lastValidBlockHeight"):
        cleaned.pop(key, None)
    return cleaned


class JupiterAdapter:
    def __init__(self, client: ProviderClient, api_key: str, base_url: str) -> None:
        self.client = client
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.client.configured = bool(api_key)
        self.client.name = "jupiter"

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            return {}
        return {"x-api-key": self.api_key}

    async def search_mints(self, mints: list[str]) -> list[JupiterToken]:
        if not self.api_key or not mints:
            return []
        query = ",".join(mints[:100])
        payload = await self.client.get_json(
            f"{self.base_url}/tokens/v2/search",
            params={"query": query},
            headers=self._headers(),
        )
        if not isinstance(payload, list):
            raise ProviderError("jupiter", "token search response was not a list")
        return [JupiterToken.model_validate(row) for row in payload if isinstance(row, dict)]

    async def order_quote(
        self,
        *,
        input_mint: str,
        output_mint: str,
        amount_base_units: int,
        side: str,
        observed_at: datetime,
        decimals_out: int,
        source_label: str = "jupiter_swap_v2_order",
    ) -> QuoteView | None:
        if not self.api_key:
            return None
        url = f"{self.base_url}/swap/v2/order"
        if any(piece in url for piece in ("/execute", "/swap/v1")):
            raise ProviderError("jupiter", "refusing deprecated or execution endpoint")
        payload = await self.client.get_json(
            url,
            params={
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": str(amount_base_units),
            },
            headers=self._headers(),
        )
        if not isinstance(payload, dict):
            raise ProviderError("jupiter", "order response was not an object")
        cleaned = strip_transaction(payload)
        if "transaction" in cleaned:
            raise ProviderError("jupiter", "transaction field survived stripping")
        quote = JupiterOrderQuote.model_validate(cleaned)
        if quote.error:
            return QuoteView(
                price_usd=Decimal("0"),
                notional_usd=Decimal(amount_base_units) / Decimal("1000000") if input_mint == USDC_MINT else Decimal("0"),
                fee_usd=Decimal("0"),
                fee_bps=Decimal("0"),
                slippage_bps=Decimal(quote.slippageBps or 0),
                price_impact_bps=Decimal("0"),
                observed_at=observed_at,
                side=side,
                input_mint=input_mint,
                output_mint=output_mint,
                source=source_label,
                fail=True,
                fail_reason=quote.error,
            )
        in_amount = Decimal(quote.inAmount)
        out_amount = Decimal(quote.outAmount)
        if in_amount <= 0 or out_amount <= 0:
            return None
        if input_mint == USDC_MINT:
            notional = in_amount / Decimal("1000000")
            token_qty = out_amount / (Decimal(10) ** decimals_out)
            price = notional / token_qty
        elif output_mint == USDC_MINT:
            notional = out_amount / Decimal("1000000")
            token_qty = in_amount / (Decimal(10) ** decimals_out)
            price = notional / token_qty
        else:
            # SOL-denominated routes are not converted implicitly.
            return None
        impact = optional_d(quote.priceImpactPct) or Decimal("0")
        impact_bps = impact * Decimal("10000") if impact < 1 else impact
        fee_usd = Decimal("0")
        if isinstance(quote.platformFee, dict) and quote.platformFee.get("amount"):
            fee_usd = Decimal(str(quote.platformFee["amount"])) / Decimal("1000000")
        fee_bps = (fee_usd / notional * Decimal("10000")) if notional else Decimal("0")
        return QuoteView(
            price_usd=price,
            notional_usd=notional,
            fee_usd=fee_usd,
            fee_bps=fee_bps,
            slippage_bps=Decimal(quote.slippageBps or 50),
            price_impact_bps=impact_bps,
            observed_at=observed_at,
            side=side,
            input_mint=input_mint,
            output_mint=output_mint,
            source=source_label,
            available_notional_usd=notional,
        )
