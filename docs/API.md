# API

Authentication uses `Authorization: Bearer <token>` (or `?token=<token>` on WebSocket `/ws` and SSE `/api/events/stream`).

- `SOLSWARM_MONITOR_TOKEN`: Read-only access to dashboard queries, `/api/desk`, `/api/events/stream`, and `/api/monitor/*`.
- `SOLSWARM_ADMIN_TOKEN`: Required for `POST /api/control/emergency-stop`, `POST /api/control/resume`, `POST /api/control/mode`, `POST /api/control/cycle`, `PUT /api/settings`, `PUT /api/thresholds`, and `POST /api/backtests`.

`GET /api/health` is public and confirms `paper_trading: true` and `live_execution_enabled: false`.

## Control Room & Funnel Endpoints

- `GET /api/desk` — Complete 8-seat state (`CHIEF`, `SCAN`, `VET`, `SOCIAL`, `SIZE`, `FILLS`, `RISK`, `CRAWLER`), active token candlesticks & security checklist, crawler web edges, real paper balance curve, `JEV · ANALYSIS` audit log, and `thresholds.py` snapshot.
- `GET /api/desk/seats/{seat}` — Click-to-inspect dossier for any seat: logical engine, heartbeat age, duration, JEV/GMGN provenance, threshold snapshot, recent `RiskEngine` vetoes, and seat event timeline.
- `GET /api/desk/demo-playback` — Explicitly labeled `DEMO PLAYBACK` deterministic 7-step storyboard (never modifies portfolio state).
- `GET /api/bench` — Rejection bench entries with reason-specific TTLs.
- `GET /api/shadow` — Shadow comparison rows and disagreement counts.
- `GET /api/thresholds` / `PUT /api/thresholds` — Active `HARD` and `SOFT` gates, `filter.py` stage counters, `shadow_mode`, `one_position_at_a_time`, and `use_bench` toggles.
- `GET /api/events/stream` — Authenticated Server-Sent Events stream for real-time Control Room updates.

## External Agent Monitor Surface (`/api/monitor/*`)

Read-only routes for Claude, Grok Bot, and external monitors:
`/api/monitor/summary`, `/api/monitor/desk`, `/api/monitor/bench`, `/api/monitor/shadow`, `/api/monitor/thresholds`, `/api/monitor/portfolio`, `/api/monitor/positions`, `/api/monitor/orders`, `/api/monitor/agents`, `/api/monitor/alerts`, `/api/monitor/providers`, `/api/monitor/tailscale`, `/api/monitor/decisions`.

No control or live-execution endpoint exists under `/api/monitor/*`.
