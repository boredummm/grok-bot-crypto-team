# SOL SWARM

Local Solana memecoin research and paper trading. The app discovers tokens, scores momentum, checks mint authorities, and simulates entries only after a deterministic risk veto. It does not create wallets, store seed phrases, sign transactions, or submit them.

Demo mode runs from labeled fixtures and needs no API keys. Live data mode reads GeckoTerminal, DexScreener, optional Jupiter quotes, and Solana RPC. Without a Jupiter key, live mode still scans but will not invent a fill.

## Start

Windows:

```powershell
.\scripts\start.ps1
```

Linux or macOS:

```bash
bash scripts/start.sh
```

Dashboard: http://127.0.0.1:5173  
API: http://127.0.0.1:8000

The start script writes `SOLSWARM_ADMIN_TOKEN` and `SOLSWARM_MONITOR_TOKEN` into `.env` when they are empty. Paste one into the dashboard. The monitor token is read-only. The admin token can pause entries and edit paper settings.

Tailnet access: see [docs/TAILSCALE.md](docs/TAILSCALE.md). Serve only. Funnel is not used.

## Tests

```bash
cd backend && ../.venv/bin/pytest -q
cd frontend && npm test
```

## Docs

- [Architecture](docs/ARCHITECTURE.md)
- [Risk model](docs/RISK_MODEL.md)
- [API](docs/API.md)
- [Tailscale](docs/TAILSCALE.md)

## Assumptions

- Canonical identity is the mint address.
- USDC notionals use 6-decimal integer quotes from Jupiter and decimal accounting internally.
- A Jupiter order response includes a transaction. That field is discarded. The order is not executed.
- Birdeye and Grok are optional. Missing credentials stay visible as unavailable.
- Demo performance is not live performance. The books are separate.
- Default limits are experimental controls, not a claim that the strategy makes money.
