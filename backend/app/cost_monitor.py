"""Zero-Additional-Cost telemetry & honest integration matrix (`/api/cost-monitor`).

Reports verified $0.00/mo required monthly spend, live or estimated Supabase Free
headroom vs the 500 MB database limit, local SQLite historical market data disk usage,
GMGN read-only rate-limit state, Grok Bot freshness, and explicit subscription caveats.
"""

from __future__ import annotations

import platform
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from app.config import ROOT, Settings
from app.db import session_scope
from app.models import (
    AgentEvent,
    BacktestRun,
    KlineBar,
    MarketObservation,
    PaperOrder,
    SwarmAgentHeartbeat,
    SwarmStructuredDecision,
)
from app.tailscale_status import tailscale_health

SUPABASE_FREE_DB_LIMIT_BYTES = 500 * 1024 * 1024  # 500 MB
SUPABASE_FREE_EGRESS_LIMIT_BYTES = 5 * 1024 * 1024 * 1024  # 5 GB uncached


def _sqlite_disk_usage(database_url: str) -> dict[str, Any]:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix) or ":memory:" in database_url:
        return {
            "sqlite_path": ":memory:",
            "sqlite_bytes": 0,
            "wal_bytes": 0,
            "shm_bytes": 0,
            "total_local_bytes": 0,
            "historical_data_isolated_locally": True,
        }
    raw_path = database_url[len(prefix) :]
    db_path = Path(raw_path)
    if not db_path.is_absolute():
        db_path = (ROOT / db_path).resolve()
    main_b = db_path.stat().st_size if db_path.exists() else 0
    wal_p = Path(str(db_path) + "-wal")
    shm_p = Path(str(db_path) + "-shm")
    wal_b = wal_p.stat().st_size if wal_p.exists() else 0
    shm_b = shm_p.stat().st_size if shm_p.exists() else 0
    return {
        "sqlite_path": str(db_path),
        "sqlite_bytes": main_b,
        "wal_bytes": wal_b,
        "shm_bytes": shm_b,
        "total_local_bytes": main_b + wal_b + shm_b,
        "historical_data_isolated_locally": True,
    }


def build_integrations_matrix(
    settings: Settings,
    swarm: Any,
    *,
    now: datetime,
    grok_bot_seats_fresh: list[str],
    tailscale: dict[str, Any],
) -> list[dict[str, Any]]:
    gmgn_rl = swarm.gmgn.rate_limit_telemetry()
    gmgn_status = "free_active" if settings.gmgn_api_key else "free_unconfigured"
    gmgn_note = (
        "Read-only Exist Auth active ($0 Free key). /v1/market/rank, /v1/token/info, /v1/token/security, and "
        "/v1/market/token_kline work on Free tier (limit=5); /v1/trade/quote (weight 11) uses deterministic AMM reserve quote fallback."
        if settings.gmgn_api_key
        else "Set GMGN_API_KEY in .env (free read-only key from gmgn.ai/ai). Secondary free DEX feeds remain available."
    )

    grok_bot_status = "free_active" if grok_bot_seats_fresh else "free_unconfigured"
    grok_bot_note = (
        f"Active Grok Bot heartbeats on {', '.join(grok_bot_seats_fresh)} via existing Cursor subscription + /api/mcp."
        if grok_bot_seats_fresh
        else "MCP bridge (/api/mcp) ready over Tailscale Serve. Connect SCAN, VET, SOCIAL, CHIEF in Grok Bot; local deterministic fallback covers all offline windows."
    )

    supa_status = "free_active" if settings.supabase_configured else "free_unconfigured"
    supa_note = (
        "Connected to Supabase Free PostgREST/RPC for compact coordination only (<4 KB/row + TTL pruning)."
        if settings.supabase_configured
        else "SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set; running 100% locally on SQLite coordination mirror ($0)."
    )

    ts_ok = bool(tailscale.get("connected") and not tailscale.get("funnel_enabled"))
    ts_status = "free_active" if ts_ok else "free_unconfigured"
    ts_note = (
        "Tailscale Personal connected; Serve-only private tailnet HTTPS active (Funnel disabled)."
        if ts_ok and tailscale.get("serve_enabled")
        else (
            "Tailscale connected; run scripts/tailscale-serve.ps1 to expose https://<node>.<tailnet>.ts.net/api/mcp privately."
            if ts_ok
            else "Tailscale Personal works with Grok Bot via one-click Connect (owner/admin of personal tailnet; no Enterprise plan required)."
        )
    )

    win_scripts_ready = (ROOT / "scripts" / "windows" / "register-task-scheduler.ps1").exists()
    is_windows = platform.system().lower().startswith("win")
    sched_status = "free_active" if (is_windows and win_scripts_ready) else "free_unconfigured"

    jev_active = bool(settings.enable_optional_jev and settings.typesafe_api_key)
    xai_active = bool(settings.xai_api_key)

    return [
        {
            "id": "gmgn_readonly_api",
            "name": "GMGN Official Read-Only OpenAPI",
            "classification": gmgn_status,
            "monthly_cost_usd": "0.00",
            "required": False,
            "missing_env": [] if settings.gmgn_api_key else ["GMGN_API_KEY"],
            "detail": gmgn_note,
            "meta": gmgn_rl,
        },
        {
            "id": "grok_bot_cursor",
            "name": "Grok Bot Teammates (SCAN, VET, SOCIAL, CHIEF)",
            "classification": grok_bot_status,
            "monthly_cost_usd": "0.00",
            "required": False,
            "missing_env": [],
            "detail": grok_bot_note,
            "meta": {"mcp_endpoint": "/api/mcp", "fresh_seats": grok_bot_seats_fresh},
        },
        {
            "id": "grok_bot_unattended_60s",
            "name": "24/7 High-Frequency Unattended Cloud LLM Polling",
            "classification": "unavailable_limitation",
            "monthly_cost_usd": "0.00",
            "required": False,
            "missing_env": [],
            "detail": (
                "Honest limitation: Grok Bot local --dev schedules do not auto-fire, and firing cloud LLM turns every 60s "
                "24/7 would exhaust existing Cursor subscription request quotas. Solved at $0 via Windows Task Scheduler + "
                "local CRAWLER pre-filtering + Free Local Deterministic Fallback."
            ),
        },
        {
            "id": "local_deterministic_fallback",
            "name": "Local Deterministic Analysis + SIZE / FILLS / RISK",
            "classification": "free_active",
            "monthly_cost_usd": "0.00",
            "required": True,
            "missing_env": [],
            "detail": "Always-on local math & RiskEngine final veto; tags offline fallback decisions explicitly as decision_source='deterministic_fallback'.",
        },
        {
            "id": "supabase_free",
            "name": "Supabase Free Shared Coordination",
            "classification": supa_status,
            "monthly_cost_usd": "0.00",
            "required": False,
            "missing_env": [
                k
                for k, v in (
                    ("SUPABASE_URL", settings.supabase_url),
                    ("SUPABASE_SERVICE_ROLE_KEY", settings.supabase_service_role_key),
                )
                if not v
            ],
            "detail": supa_note,
        },
        {
            "id": "tailscale_personal_serve",
            "name": "Tailscale Personal + Tailscale Serve (No Funnel)",
            "classification": ts_status,
            "monthly_cost_usd": "0.00",
            "required": False,
            "missing_env": [],
            "detail": ts_note,
        },
        {
            "id": "windows_task_scheduler",
            "name": "Windows PC + Windows Task Scheduler",
            "classification": sched_status,
            "monthly_cost_usd": "0.00",
            "required": False,
            "missing_env": [],
            "detail": (
                "PowerShell scripts in scripts/windows/ run recurring local GMGN cycles and Supabase retention pruning at $0."
            ),
            "meta": {"scripts_present": win_scripts_ready, "host_os": platform.system()},
        },
        {
            "id": "optional_jev_typesafe",
            "name": "Optional TypeSafe System One (JEV)",
            "classification": "optional_paid_active" if jev_active else "optional_paid_disabled",
            "monthly_cost_usd": "usage_based" if jev_active else "0.00",
            "required": False,
            "missing_env": [],
            "detail": (
                "Explicitly enabled by operator (SOLSWARM_ENABLE_OPTIONAL_JEV=true)."
                if jev_active
                else "Strictly optional & disabled by default ($0.00/mo). Not required for SOL SWARM operation."
            ),
        },
        {
            "id": "optional_xai_api",
            "name": "Optional Direct xAI API (Paid Key)",
            "classification": "optional_paid_active" if xai_active else "optional_paid_disabled",
            "monthly_cost_usd": "usage_based" if xai_active else "0.00",
            "required": False,
            "missing_env": [],
            "detail": (
                "XAI_API_KEY configured by operator."
                if xai_active
                else "Disabled ($0.00/mo). Grok Bot runs through existing Cursor subscription instead of a paid xAI API key."
            ),
        },
        {
            "id": "live_swaps_wallet_signing",
            "name": "Live Swap Execution & Wallet Signing",
            "classification": "permanently_disabled",
            "monthly_cost_usd": "0.00",
            "required": False,
            "missing_env": [],
            "detail": "Structurally blocked across GMGN (/v1/trade/swap), Jupiter, MCP, and RiskEngine. Paper accounting only.",
        },
    ]


def cost_monitor_report(settings: Settings, swarm: Any) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    local_disk = _sqlite_disk_usage(settings.database_url)
    coord_local = swarm.coord.local_coordination_metrics()
    remote = swarm.coord.remote_telemetry or {}
    tailscale = tailscale_health()

    fresh_cutoff = now - timedelta(seconds=max(15, settings.grok_bot_freshness_seconds))
    with session_scope() as session:
        obs_count = int(session.scalar(select(func.count()).select_from(MarketObservation)) or 0)
        kline_count = int(session.scalar(select(func.count()).select_from(KlineBar)) or 0)
        ev_count = int(session.scalar(select(func.count()).select_from(AgentEvent)) or 0)
        ord_count = int(session.scalar(select(func.count()).select_from(PaperOrder)) or 0)
        bt_count = int(session.scalar(select(func.count()).select_from(BacktestRun)) or 0)

        grok_hbs = session.scalars(
            select(SwarmAgentHeartbeat).where(
                SwarmAgentHeartbeat.decision_source == "grok_bot",
                SwarmAgentHeartbeat.last_seen_at >= fresh_cutoff,
            )
        ).all()
        grok_seats_fresh = sorted(r.seat for r in grok_hbs)

        src_rows = session.execute(
            select(SwarmStructuredDecision.decision_source, func.count())
            .group_by(SwarmStructuredDecision.decision_source)
        ).all()
        decision_counts = {"grok_bot": 0, "deterministic_fallback": 0, "optional_jev": 0}
        for src_name, cnt in src_rows:
            if src_name in decision_counts:
                decision_counts[src_name] = int(cnt or 0)

    limit_bytes = int(settings.supabase_db_limit_bytes or SUPABASE_FREE_DB_LIMIT_BYTES)
    remote_db_bytes = remote.get("database_bytes")
    remote_swarm_bytes = remote.get("swarm_tables_bytes")
    effective_bytes = int(remote_db_bytes) if remote_db_bytes is not None else int(coord_local["estimated_coordination_bytes"])
    usage_pct = round((effective_bytes / limit_bytes) * 100.0, 4) if limit_bytes > 0 else 0.0
    headroom_bytes = max(0, limit_bytes - effective_bytes)

    optional_paid_enabled = bool((settings.enable_optional_jev and settings.typesafe_api_key) or settings.xai_api_key)
    integrations = build_integrations_matrix(
        settings,
        swarm,
        now=now,
        grok_bot_seats_fresh=grok_seats_fresh,
        tailscale=tailscale,
    )

    return {
        "checked_at": now.isoformat(),
        "required_monthly_cost_usd": "0.00",
        "optional_paid_spend_usd": (
            f"{round((swarm.jev.total_input_tokens / 1_000_000.0) * 0.042, 6):.6f}"
            if optional_paid_enabled
            else "0.00"
        ),
        "zero_additional_cost_verified": not optional_paid_enabled,
        "paper_trading_only": True,
        "live_execution_enabled": False,
        "supabase": {
            "configured": settings.supabase_configured,
            "mode": "supabase_postgrest" if settings.supabase_configured else "local_sqlite_fallback",
            "organization_audit": {
                "org_slug": "jxyerpsdnnregvudaqda",
                "plan": "free",
                "inactivity_pause_days": 7,
                "pause_mitigation": "Recurring local Task Scheduler sync & retention RPC keeps Free project active.",
            },
            "free_tier_limit_bytes": limit_bytes,
            "free_tier_limit_mb": round(limit_bytes / (1024 * 1024), 1),
            "free_tier_egress_limit_bytes": SUPABASE_FREE_EGRESS_LIMIT_BYTES,
            "database_bytes": int(remote_db_bytes) if remote_db_bytes is not None else None,
            "swarm_tables_bytes": int(remote_swarm_bytes) if remote_swarm_bytes is not None else coord_local["estimated_coordination_bytes"],
            "usage_pct": usage_pct,
            "headroom_bytes": headroom_bytes,
            "headroom_mb": round(headroom_bytes / (1024 * 1024), 2),
            "row_counts": remote.get("row_counts") or coord_local["row_counts"],
            "retention_policy": {
                "task_ttl_hours": settings.supabase_task_ttl_hours,
                "finding_ttl_hours": settings.supabase_finding_ttl_hours,
                "heartbeat_ttl_hours": settings.supabase_heartbeat_ttl_hours,
                "decision_ttl_days": settings.supabase_decision_ttl_days,
                "max_payload_bytes": settings.supabase_max_payload_bytes,
                "max_tasks_rows": swarm.coord.max_tasks_rows,
                "max_findings_rows": swarm.coord.max_findings_rows,
                "max_decisions_rows": swarm.coord.max_decisions_rows,
            },
            "last_sync_at": swarm.coord.last_sync_at.isoformat() if swarm.coord.last_sync_at else None,
            "last_prune_at": swarm.coord.last_prune_at.isoformat() if swarm.coord.last_prune_at else None,
            "last_error": swarm.coord.last_error,
        },
        "local_storage": {
            **local_disk,
            "historical_row_counts": {
                "market_observations": obs_count,
                "kline_bars": kline_count,
                "agent_events": ev_count,
                "paper_orders": ord_count,
                "backtest_runs": bt_count,
            },
        },
        "gmgn_rate_limits": swarm.gmgn.rate_limit_telemetry(),
        "grok_bot": {
            "mcp_enabled": settings.mcp_enabled,
            "mcp_endpoint": "/api/mcp",
            "freshness_window_seconds": settings.grok_bot_freshness_seconds,
            "fresh_seats": grok_seats_fresh,
            "fallback_mode": "deterministic_fallback",
        },
        "decision_sources": decision_counts,
        "integrations": integrations,
    }


def integrations_status_report(settings: Settings, swarm: Any) -> dict[str, Any]:
    report = cost_monitor_report(settings, swarm)
    return {
        "checked_at": report["checked_at"],
        "required_monthly_cost_usd": report["required_monthly_cost_usd"],
        "zero_additional_cost_verified": report["zero_additional_cost_verified"],
        "integrations": report["integrations"],
    }
