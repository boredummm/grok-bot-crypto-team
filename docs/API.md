# API & MCP Bridge

Authentication uses `Authorization: Bearer <token>` (or `?token=<token>` on WebSocket `/ws` and SSE `/api/events/stream`).

- `SOLSWARM_MONITOR_TOKEN`: Read-only access to dashboard queries, `/api/desk`, `/api/cost-monitor`, `/api/integrations/status`, `/api/events/stream`, `/api/monitor/*`, and read-only MCP tools.
- `SOLSWARM_MCP_TOKEN`: Scoped agent token for Grok Bot teammates (`SCAN`, `VET`, `SOCIAL`, `CHIEF`) to read evidence and submit schema-validated findings, heartbeats, and `CHIEF` decisions on `POST /api/mcp` without granting emergency-stop or risk-limit admin mutations.
- `SOLSWARM_ADMIN_TOKEN`: Required for `POST /api/control/emergency-stop`, `POST /api/control/resume`, `POST /api/control/mode`, `POST /api/control/cycle`, `POST /api/control/prune-retention`, `PUT /api/settings`, `PUT /api/thresholds`, and `POST /api/backtests`.

`GET /api/health` is public and confirms `paper_trading: true` and `live_execution_enabled: false`.

## Zero-Cost Telemetry & Coordination Endpoints

- `GET /api/cost-monitor` — Verified `$0.00/mo` required spend, Supabase Free usage vs `500 MB` limit, retention TTLs, local SQLite historical disk usage, GMGN rate-limit telemetry, Grok Bot freshness, decision source counts (`grok_bot` vs `deterministic_fallback` vs `optional_jev`), and integration classification matrix.
- `GET /api/integrations/status` — Compact integration matrix (`free_active`, `free_unconfigured`, `unavailable_limitation`, `optional_paid_disabled`, `permanently_disabled`).
- `GET /api/coordination` — Active pending `swarm_agent_tasks`, `swarm_agent_heartbeats`, `swarm_agent_findings`, and `swarm_decisions`.
- `POST /api/control/prune-retention` — Admin trigger to prune expired coordination rows locally and via Supabase `swarm_prune_expired_data()`.

## Authenticated MCP Streamable HTTP Bridge (`POST /api/mcp`)

JSON-RPC 2.0 MCP endpoint (`initialize`, `ping`, `tools/list`, `tools/call`) protected by Bearer auth and Tailscale Funnel refusal:
- `swarm_get_pending_tasks` (read-only)
- `swarm_get_candidate_evidence` (read-only)
- `swarm_get_office_status` (read-only)
- `swarm_record_heartbeat` (`AGENT` / `ADMIN`)
- `swarm_submit_finding` (`AGENT` / `ADMIN` — strict Pydantic schemas for `SCAN`, `VET`, `SOCIAL`)
- `swarm_submit_chief_decision` (`AGENT` / `ADMIN` — strict Pydantic schema for `CHIEF`; always subject to `SIZE` and `RiskEngine` final veto)

## Control Room & Funnel Endpoints

- `GET /api/desk` — Complete 8-seat state (`CHIEF`, `SCAN`, `VET`, `SOCIAL`, `SIZE`, `FILLS`, `RISK`, `CRAWLER`), active token candlesticks & security checklist, crawler web edges, real paper balance curve, `JEV · ANALYSIS` audit log, and `thresholds.py` snapshot.
- `GET /api/desk/seats/{seat}` — Click-to-inspect dossier for any seat including `decision_source`.
- `GET /api/desk/demo-playback` — Explicitly labeled `DEMO PLAYBACK` deterministic 7-step storyboard.
- `GET /api/bench`, `GET /api/shadow`, `GET /api/thresholds`, `PUT /api/thresholds`, `GET /api/events/stream`.

## External Agent Monitor Surface (`/api/monitor/*`)

Read-only routes:
`/api/monitor/summary`, `/api/monitor/desk`, `/api/monitor/cost-monitor`, `/api/monitor/integrations/status`, `/api/monitor/bench`, `/api/monitor/shadow`, `/api/monitor/thresholds`, `/api/monitor/portfolio`, `/api/monitor/positions`, `/api/monitor/orders`, `/api/monitor/agents`, `/api/monitor/alerts`, `/api/monitor/providers`, `/api/monitor/tailscale`, `/api/monitor/decisions`.
