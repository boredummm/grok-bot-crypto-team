# API

Authentication is `Authorization: Bearer <token>`.

- `SOLSWARM_MONITOR_TOKEN` reads dashboard and `/api/monitor/*` routes.
- `SOLSWARM_ADMIN_TOKEN` can also pause entries, resume entries, change mode, change settings, and start a replay.

`GET /api/health` is public and reports only that the process is up and that live execution is disabled.

Monitor routes: `/api/monitor/summary`, `portfolio`, `positions`, `orders`, `agents`, `alerts`, `providers`, `tailscale`, `decisions`.

There is no monitor route that changes risk limits or submits an order. External agents, including Claude and Grok Bot, should use the monitor token. Responses do not include API keys.

WebSocket: `/ws?token=<monitor-or-admin-token>`.

Settings writes reject values outside the paper-trading ranges and reject fields that would enable leverage or live execution. There is no live-trading switch.

Replay `POST /api/backtests` stores a result document and does not insert paper orders.
