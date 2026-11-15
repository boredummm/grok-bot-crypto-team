import json
from pathlib import Path

import httpx
import pytest

from app.providers.http import ProviderClient
from app.providers.jupiter import strip_transaction
from app.providers.normalize import parse_boosted_mints, parse_dexscreener_pairs, parse_geckoterminal_pools
from app.providers.solana import parse_mint_account, top_holder_pct
from datetime import datetime, timezone


ROOT = Path(__file__).resolve().parents[2]


def test_repository_has_no_transaction_submission():
    banned = ("sendTransaction", "signTransaction", "private_key", "seed_phrase", "/swap/v1", "tailscale funnel")
    allowed_mentions = {"FORBIDDEN_JUPITER_PATHS", "funnel_enabled", "Funnel must stay off", "Do not run tailscale funnel"}
    for path in (ROOT / "backend" / "app").rglob("*.py"):
        text = path.read_text()
        for phrase in ("sendTransaction", "signTransaction", "/swap/v1/quote"):
            assert phrase not in text, path
    script = (ROOT / "scripts" / "tailscale-serve.ps1").read_text() if (ROOT / "scripts" / "tailscale-serve.ps1").exists() else ""
    assert "funnel" in script.lower()
    assert "tailscale funnel " not in script.lower() or "off" in script.lower()


def test_jupiter_strips_transaction_and_parsers():
    cleaned = strip_transaction({"inputMint": "a", "transaction": "deadbeef", "inAmount": "1"})
    assert "transaction" not in cleaned
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    pairs = parse_dexscreener_pairs(
        [
            {
                "chainId": "solana",
                "dexId": "raydium",
                "pairAddress": "pool1",
                "baseToken": {"address": "Mint111", "name": "A", "symbol": "A"},
                "quoteToken": {"address": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "symbol": "USDC"},
                "priceUsd": "0.1",
                "txns": {"m5": {"buys": 3, "sells": 1}},
                "volume": {"m5": 10, "h1": 40},
                "priceChange": {"m5": 1.2},
                "liquidity": {"usd": 1000},
                "pairCreatedAt": 1767225600000,
            }
        ],
        mode="live",
        observed_at=now,
        promoted_mints=set(),
    )
    assert pairs[0].mint == "Mint111"
    assert pairs[0].price_usd is not None
    boosted = parse_boosted_mints([{"chainId": "solana", "tokenAddress": "Mint111"}])
    assert boosted == {"Mint111"}
    gecko = parse_geckoterminal_pools(
        {
            "data": [
                {
                    "id": "solana_pool",
                    "attributes": {
                        "address": "pool",
                        "base_token_price_usd": "0.2",
                        "reserve_in_usd": "10",
                        "pool_created_at": "2026-01-01T00:00:00Z",
                        "transactions": {"m5": {"buys": 2, "sells": 1, "buyers": 2}},
                        "volume_usd": {"m5": "5", "h1": "9"},
                        "price_change_percentage": {"m5": "1.5"},
                    },
                    "relationships": {
                        "base_token": {"data": {"id": "solana_mint"}},
                        "quote_token": {"data": {"id": "solana_usdc"}},
                    },
                }
            ],
            "included": [
                {"id": "solana_mint", "attributes": {"address": "Mint222", "name": "B", "symbol": "B", "decimals": 6}},
                {
                    "id": "solana_usdc",
                    "attributes": {"address": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "symbol": "USDC"},
                },
            ],
        },
        mode="live",
        observed_at=now,
    )
    assert gecko[0].unique_buyers_m5 == 2
    mint = parse_mint_account(
        {
            "result": {
                "value": {
                    "owner": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                    "data": {
                        "parsed": {
                            "type": "mint",
                            "info": {
                                "mintAuthority": None,
                                "freezeAuthority": "Freeze1111111111111111111111111111111111111",
                                "decimals": 6,
                                "supply": "1000",
                            },
                        }
                    },
                }
            }
        },
        observed_at=now,
        source="unit",
    )
    assert mint.facts.freeze_authority_active is True
    assert mint.facts.mint_authority_active is False
    from decimal import Decimal

    assert top_holder_pct({"result": {"value": [{"amount": "400"}]}}, 1000) == Decimal("40")


@pytest.mark.asyncio
async def test_provider_retries_then_opens_circuit():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500, json={"error": "down"})

    transport = httpx.MockTransport(handler)
    client = ProviderClient("dexscreener", per_minute=1000, client=httpx.AsyncClient(transport=transport), sleep=_instant)
    with pytest.raises(Exception):
        await client.get_json("https://api.dexscreener.com/tokens/v1/solana/abc")
    assert calls["n"] == 3
    assert client.circuit.state == "open"
    await client.aclose()


async def _instant(_seconds: float) -> None:
    return None


def test_api_auth_emergency_stop_and_replay_isolation(api):
    assert api.get("/api/health").json()["live_execution_enabled"] is False
    assert api.get("/api/summary").status_code == 401
    monitor = {"Authorization": "Bearer monitor-token-test"}
    admin = {"Authorization": "Bearer admin-token-test"}
    summary = api.get("/api/summary", headers=monitor)
    assert summary.status_code == 200
    body = summary.json()
    assert body["paper_trading"] is True
    assert body["live_execution_enabled"] is False
    assert body["win_rate_available"] is False
    assert "jupiter_api_key" not in summary.text
    assert api.post("/api/control/emergency-stop", headers=monitor, json={"reason": "nope"}).status_code == 403
    assert api.post("/api/monitor/control/emergency-stop", headers=admin, json={"reason": "nope"}).status_code == 404
    stopped = api.post("/api/control/emergency-stop", headers=admin, json={"reason": "desk pause"})
    assert stopped.status_code == 200
    assert stopped.json()["emergency_stop"] is True
    settings = api.get("/api/settings", headers=monitor).json()
    assert settings["providers"]["jupiter_configured"] is False
    assert "xai_api_key" not in json.dumps(settings)
    replay = api.post("/api/backtests", headers=admin, json={"speed": 2, "notional": "20"})
    assert replay.status_code == 200
    assert replay.json()["disclosure"]["isolated_from_paper_book"] is True
    assert api.get("/api/orders", headers=monitor).json() == []
    resumed = api.post("/api/control/resume", headers=admin)
    assert resumed.status_code == 200
    bad = api.put("/api/settings", headers=admin, json={"risk": {"max_new_trade_usd": "1000000"}})
    assert bad.status_code == 422
