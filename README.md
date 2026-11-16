# SOL SWARM (Zero-Cost GMGN + Grok Bot + Supabase Free Paper Control Room)

Local Solana memecoin research and paper-trading desk with a top-down 2D pixel-art control room (`SOL SWARM + JEV`). Operates with **$0.00/mo additional required monthly spend** using:

- **GMGN Official Read-Only OpenAPI (`Exist Auth`)** for Solana trending (`/v1/market/rank`), token info/security (`/v1/token/info`, `/v1/token/security`), 5m candlesticks (`/v1/market/token_kline`), and paper quote simulation (with automatic AMM pool-reserve paper quote fallback when `/v1/trade/quote` weight 11 exceeds the Free-tier limit of 5).
- **Grok Bot Teammates (`SCAN`, `VET`, `SOCIAL`, `CHIEF`)** via existing Cursor subscription connected over **Tailscale Personal + Tailscale Serve** (`POST /api/mcp` authenticated Streamable HTTP MCP bridge) + **Free Local Deterministic Fallback** (`decision_source="deterministic_fallback"`) whenever Grok Bot teammates are offline.
- **Storage Split**: Historical market observations, candlesticks (`kline_bars`), and replay runs stay **100% local** in Windows SQLite (`data/solswarm.db`), while **Supabase Free** (`< 500 MB`) stores only compact coordination rows (`<= 4 KB` payload cap + automatic TTL/row-cap retention pruning).
- **Windows Task Scheduler** (`scripts/windows/*.ps1`) for zero-cost local recurring discovery cycles and Supabase Free retention/keepalive pruning.
- **Paper-Only Safety**: No wallets, no private keys, no signing, and no live swaps. Independent deterministic `SIZE`, `FILLS`, `RiskEngine` final veto, and Emergency Stop remain active at all times.

## Start

Windows PowerShell:

```powershell
.\scripts\start.ps1
```

Register recurring local cycles & retention pruning on Windows Task Scheduler ($0/mo):

```powershell
.\scripts\windows\register-task-scheduler.ps1 -CycleMinutes 5 -PruneHours 6
```

Linux or macOS:

```bash
bash scripts/start.sh
```

Dashboard: http://127.0.0.1:5173 (includes `/cost` **Cost Monitor**)  
API: http://127.0.0.1:8000  
MCP Bridge (Tailscale Serve only): `POST /api/mcp`

`start.ps1` generates distinct `SOLSWARM_ADMIN_TOKEN`, `SOLSWARM_MONITOR_TOKEN`, and `SOLSWARM_MCP_TOKEN` secrets into `.env` without printing them. Tailnet access: see [docs/TAILSCALE.md](docs/TAILSCALE.md). Private Tailscale Serve only; Funnel is never enabled and `/api/mcp` refuses requests if Funnel is detected.

## Environment Variables (`.env`)

- `GMGN_API_KEY`: Free read-only GMGN OpenAPI key from `https://gmgn.ai/ai`. Swap/write endpoints are blocked at the backend service boundary.
- `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY`: Optional Supabase Free project (`supabase/migrations/20261116000000_sol_swarm_zero_cost.sql`) for shared coordination. Falls back seamlessly to local SQLite when unset.
- `SOLSWARM_ENABLE_OPTIONAL_JEV`: `false` by default ($0.00/mo). Optional paid TypeSafe System One (`TYPESAFE_API_KEY`) and direct xAI API (`XAI_API_KEY`) are not required.

## Tests

```bash
cd backend && ../.venv/bin/pytest -q
cd frontend && npm test && npm run build
```

## Docs

- [Architecture](docs/ARCHITECTURE.md)
- [Risk model](docs/RISK_MODEL.md)
- [API & MCP Bridge](docs/API.md)
- [Tailscale](docs/TAILSCALE.md)
