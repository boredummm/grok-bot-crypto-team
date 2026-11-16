# SOL SWARM + JEV (GMGN Paper Control Room)

Local Solana memecoin research and paper-trading desk with a top-down 2D pixel-art control room (`SOL SWARM + JEV`). The system discovers Solana tokens, runs deterministic stage gates (`free_kill -> trade_kill -> chain_kill -> soft_kill -> pick`), optionally calls TypeSafe System One (`JEV`) for typed `noul` / `choice` / `score` judgements when `TYPESAFE_API_KEY` is provided, and simulates paper entries only after the independent deterministic `RiskEngine` approves them.

It does not create wallets, accept trading private keys, sign transactions, or execute live swaps. GMGN is integrated strictly via its official read-only OpenAPI (`Exist Auth`) for market, security, kline, and paper quote simulation.

## Start

Windows PowerShell:

```powershell
.\scripts\start.ps1
```

Linux or macOS:

```bash
bash scripts/start.sh
```

Dashboard: http://127.0.0.1:5173  
API: http://127.0.0.1:8000

`start.ps1` and `start.sh` generate distinct `SOLSWARM_ADMIN_TOKEN` and `SOLSWARM_MONITOR_TOKEN` secrets into `.env` without printing them. Paste either token into the dashboard login gate. Monitor tokens are read-only; admin tokens are required to pause/resume entries, edit `thresholds.py` / settings, or trigger scans.

Tailnet access: see [docs/TAILSCALE.md](docs/TAILSCALE.md). Private Tailscale Serve only. Funnel is never enabled.

## Optional Credentials (`.env`)

- `GMGN_API_KEY`: Read-only GMGN OpenAPI key from `https://gmgn.ai/ai` (`X-APIKEY` + `timestamp` + `client_id` UUID). Unlocks GMGN Solana trending (`GET /v1/market/rank`), token info/security (`GET /v1/token/info`, `GET /v1/token/security`), candlesticks (`GET /v1/market/token_kline`), and read-only paper swap quotes (`GET /v1/trade/quote`). Swap/write endpoints are blocked at the backend service boundary.
- `TYPESAFE_API_KEY`: TypeSafe System One key (`POST https://api.typesafe.ai/v1/systemone`, default `jev-latest`). Unlocks live `market`, `solana`, and `pick` typed judgements. Without it, the desk displays `JEV NOT CONNECTED` and runs deterministic gates without faking model outputs.
- `JUPITER_API_KEY`: Secondary quote fallback (`GET /swap/v2/order` with transaction blob stripped).
- `XAI_API_KEY`: Optional Grok commentary seat (`grok-4-fast`). Never overrides risk limits or authorizes trades.

## Tests

```bash
cd backend && ../.venv/bin/pytest -q
cd frontend && npm test && npm run build
```

## Docs

- [Architecture](docs/ARCHITECTURE.md)
- [Risk model](docs/RISK_MODEL.md)
- [API](docs/API.md)
- [Tailscale](docs/TAILSCALE.md)
