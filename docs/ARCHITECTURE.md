# Architecture

SOL SWARM is a zero-additional-cost local paper-trading research desk (`SOL SWARM + JEV`). It never signs or submits Solana transactions.

## Zero-Cost Intelligence & Storage Split

1. **Local Historical SQLite (`data/solswarm.db`)**: Stores all historical market observations (`market_observations`), 5-minute candlesticks (`kline_bars`), replay runs (`backtest_runs`), paper orders/fills, and ledger state on the local Windows PC so cloud storage never grows unbounded.
2. **Supabase Free Shared Coordination (`backend/app/supabase_coord.py`, `supabase/migrations/20261116000000_sol_swarm_zero_cost.sql`)**: Stores only compact shared coordination rows (`<= 4 KB` payload cap) across `swarm_agent_tasks`, `swarm_agent_findings`, `swarm_agent_heartbeats`, `swarm_decisions`, and `swarm_dashboard_state`, with automatic TTL + row-cap pruning (`swarm_prune_expired_data()`) and live 500 MB headroom monitoring (`swarm_storage_telemetry()`). Falls back seamlessly to local SQLite mirror tables when `SUPABASE_URL` is unconfigured.
3. **Grok Bot Primary Intelligence + Free Local Deterministic Fallback**:
   - Grok Bot teammates (`SCAN`, `VET`, `SOCIAL`, `CHIEF`) connect over Tailscale Personal + Tailscale Serve via `POST /api/mcp` (`backend/app/mcp_bridge.py`) or Supabase MCP (`decision_source="grok_bot"`).
   - When Grok Bot teammates are offline or outside the freshness window (`SOLSWARM_GROK_BOT_FRESHNESS_SECONDS`), the free local deterministic analyzer evaluates candidates and tags every finding, seat card, event, and decision explicitly as `decision_source="deterministic_fallback"`.
   - TypeSafe JEV (`SOLSWARM_ENABLE_OPTIONAL_JEV=false`) and direct xAI API (`XAI_API_KEY`) are strictly optional and disabled by default (`$0.00/mo` required).

## Eight-Seat Desk & Funnel

Follows **code fetches, Grok Bot (or deterministic fallback) judges, deterministic code decides**:

`GMGN / GeckoTerminal universe -> CRAWLER evidence & local kline cache -> free_kill -> trade_kill -> Solana RPC + GMGN security chain_kill -> Grok Bot MCP findings/CHIEF pick (or deterministic_fallback) -> SIZE -> independent RiskEngine final veto -> FILLS (paper quote) & exit monitoring`.

## Providers

- **GMGN OpenAPI (`backend/app/providers/gmgn.py`)**: Isolated read-only adapter using `X-APIKEY` + `timestamp` + per-request `client_id` UUID (`Exist Auth`). Blocks all write/swap endpoints and `X-Signature` headers. Unwraps nested `/v1/market/rank` envelopes, normalizes millisecond `/v1/market/token_kline` bars, tracks `x-ratelimit-*` headers, and falls back to deterministic AMM pool-reserve paper quotes (`gmgn_amm_reserve_quote`) when `/v1/trade/quote` (weight 11) exceeds the GMGN Free-tier limit of 5.
- **Tailscale Serve (`scripts/tailscale-serve.ps1`)**: Binds FastAPI/Vite to `127.0.0.1` and publishes HTTPS strictly inside the user's Personal tailnet. Tailscale Funnel is forbidden.
