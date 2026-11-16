# Architecture

SOL SWARM is a local paper-trading research desk (`SOL SWARM + JEV`). It never signs or submits Solana transactions.

## Eight-Seat Desk & Funnel

Follows **code fetches, model judges, code decides**:

`GMGN / GeckoTerminal universe -> free_kill (age, liquidity, volume, mcap) -> DexScreener trade_kill -> Solana RPC + GMGN security chain_kill -> optional JEV typed judgements (soft_kill) -> CHIEF pick or NONE -> SIZE -> FILLS (paper quote) -> independent RiskEngine veto & exit monitoring`.

The 2×4 Control Room maps directly to persistent backend state:
- `CHIEF` (`orchestrator`): coordinates shortlists and final pick-or-stand-down.
- `SCAN` (`momentum`): runs `free_kill` / `trade_kill` arithmetic and momentum structure.
- `VET` (`security`): inspects mint/freeze authorities, holder concentration, and contract flags.
- `SOCIAL`: extracts verified on-chain X handles (`clean_x_handle`) without guessing fan accounts.
- `SIZE` (`strategy`): deterministic paper ticket sizing (`<=2%` bank, `<=6%` exposure, pool & fee viability).
- `FILLS` (`execution`): simulates paper fills from read-only GMGN or Jupiter quotes with fee/impact provenance.
- `RISK` (`risk` + `monitoring`): independent final veto, emergency stop, and exit surveillance.
- `CRAWLER` (`discovery`): sweeps feeds, tracks rate-limit budgets, and maintains the reason-TTL `BenchEntry` table (`bench_entries`) plus `ShadowDecision` comparison journal (`shadow_decisions`).

Demo mode and live mode remain strictly partitioned in SQLite so fixture trades never enter live analytics.

## Providers

- **GMGN OpenAPI (`backend/app/providers/gmgn.py`)**: Isolated read-only adapter using `X-APIKEY` + `timestamp` + per-request `client_id` UUID (`Exist Auth`). All write/swap endpoints (`/v1/trade/swap`, `/v1/trade/multi_swap`, `/v1/trade/strategy/*`, `/v1/cooking/*`) and `X-Signature` headers are blocked at the service boundary. Handles `RATE_LIMIT_BANNED_MAX` (`reset_at` cooldown) and `PLAN_UPGRADE_REQUIRED` without ban-extending retries, sanitizes untrusted token metadata, and cross-checks prices/liquidity against GeckoTerminal and DexScreener.
- **TypeSafe System One / JEV (`backend/app/jev.py`)**: Typed `noul`, `choice`, and `score` question sets (`POST /v1/systemone`). Validates answer schemas and probability sums, records resolved model IDs (`jev-1.14.0`), never retries HTTP `422`, and reports `JEV NOT CONNECTED` when `TYPESAFE_API_KEY` is absent.
- **GeckoTerminal, DexScreener, Solana RPC, Jupiter Swap v2 (quote-only)**: Secondary discovery, on-chain mint verification, and quote fallback.

## Tailscale

The process binds to `127.0.0.1`. `scripts/tailscale-serve.ps1` publishes that port privately via Tailscale Serve and aborts if Funnel is enabled.
