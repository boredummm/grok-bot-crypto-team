# Architecture

SOL SWARM is a local paper-trading research desk. It never signs or submits Solana transactions.

## Agents

Events are append-only rows. Each has an id, UTC timestamp, source agent, type, optional mint, payload, correlation id, sequence, and status. Re-publishing an existing id is ignored.

1. DiscoveryAgent scans GeckoTerminal new and trending pools, then enriches with DexScreener. Boosted listings are flagged and do not add discovery score.
2. MomentumAgent and SecurityAgent run concurrently under a timeout. A timeout becomes HOLD.
3. StrategyAgent runs Momentum Confirmation v1 and may propose one long entry.
4. RiskEngine is the only component that can approve an entry. Strategy cannot skip it.
5. PaperExecutionAgent fills from a quote. Displayed price is not the fill price.
6. MonitoringAgent checks exits. A missing sell quote marks the position unsellable instead of closing it at the last mark.
7. OrchestratorAgent sequences the work and records the timeline.
8. Optional Grok commentary is a separate event. If no API key is configured, commentary is unavailable and deterministic text is not labeled as Grok.

Demo mode and live mode use separate portfolio rows, orders, positions, and observations.

## Providers

Adapters share timeouts, a per-minute limiter, Retry-After handling, exponential backoff, and a circuit breaker. Jupiter calls `GET /swap/v2/order` only. The transaction field is stripped before validation. `/execute`, `/build`, and `/submit` are not called.

Solana RPC is used for `getAccountInfo` and `getTokenLargestAccounts` only.

## Tailscale

The process binds to 127.0.0.1. `scripts/tailscale-serve.ps1` publishes that port with Tailscale Serve. It checks Funnel status and refuses to continue when Funnel is already on. It never runs Funnel.

Read-only monitor routes live under `/api/monitor`. They accept the monitor token. They do not include emergency stop or settings writes. Those require the admin token.
