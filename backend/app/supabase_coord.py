"""Compact shared coordination store for Supabase Free + seamless local SQLite fallback.

Storage Split Invariant:
- Historical market data (`market_observations`, `kline_bars`), raw provider caches,
  and replay runs NEVER leave local SQLite (`data/solswarm.db`).
- Supabase Free receives ONLY compact coordination records (`<= 4 KB` payload cap)
  with strict TTL expiration and row-cap retention pruning so usage stays well below
  the 500 MB Supabase Free Postgres limit.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import delete, desc, func, select

from app.config import Settings
from app.db import session_scope, utcnow
from app.models import (
    SwarmAgentFinding,
    SwarmAgentHeartbeat,
    SwarmAgentTask,
    SwarmDashboardState,
    SwarmStructuredDecision,
)

log = logging.getLogger(__name__)

VALID_DECISION_SOURCES = {"grok_bot", "deterministic_fallback", "optional_jev"}
VALID_HEARTBEAT_SOURCES = VALID_DECISION_SOURCES | {"deterministic_engine"}


def clamp_json_payload(payload: dict[str, Any], max_bytes: int = 4096) -> dict[str, Any]:
    """Ensure compact JSON payload stays strictly within the byte budget."""
    encoded = json.dumps(payload, default=str, separators=(",", ":")).encode("utf-8")
    if len(encoded) <= max_bytes:
        return json.loads(encoded.decode("utf-8"))
    trimmed: dict[str, Any] = {}
    budget = max(256, max_bytes - 96)
    used = 2
    for key, val in payload.items():
        if isinstance(val, str) and len(val) > 240:
            val = val[:237] + "..."
        elif isinstance(val, list) and len(val) > 8:
            val = val[:8]
        piece = json.dumps({key: val}, default=str, separators=(",", ":")).encode("utf-8")
        if used + len(piece) <= budget:
            trimmed[key] = json.loads(piece.decode("utf-8"))[key]
            used += len(piece)
    trimmed["_truncated"] = True
    return trimmed


def _parse_iso(val: str | datetime | None, default: datetime) -> datetime:
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    if not val:
        return default
    cleaned = str(val).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(cleaned)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return default


class SupabaseCoordinator:
    """PostgREST + RPC client over httpx with automatic SQLite mirror & retention enforcement."""

    def __init__(self, settings: Settings, http_client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self.base_url = settings.supabase_url.strip().rstrip("/")
        self.service_key = settings.supabase_service_role_key.strip()
        self._external_http = http_client is not None
        self._http = http_client
        self.last_sync_at: datetime | None = None
        self.last_prune_at: datetime | None = None
        self.last_error: str | None = None
        self.remote_telemetry: dict[str, Any] | None = None
        self.max_tasks_rows: int = 500
        self.max_findings_rows: int = 1000
        self.max_decisions_rows: int = 1000

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.service_key)

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=6.0)
        return self._http

    async def aclose(self) -> None:
        if self._http is not None and not self._external_http:
            await self._http.aclose()
            self._http = None

    def _headers(self, *, prefer: str | None = None) -> dict[str, str]:
        headers = {
            "apikey": self.service_key,
            "Authorization": f"Bearer {self.service_key}",
            "Content-Type": "application/json",
        }
        if prefer:
            headers["Prefer"] = prefer
        return headers

    async def _postgrest_upsert(self, table: str, rows: list[dict[str, Any]], on_conflict: str) -> bool:
        if not self.configured or not rows:
            return False
        url = f"{self.base_url}/rest/v1/{table}"
        try:
            res = await self._client().post(
                url,
                params={"on_conflict": on_conflict},
                headers=self._headers(prefer="resolution=merge-duplicates,return=minimal"),
                json=rows,
            )
            if res.status_code >= 400:
                self.last_error = f"Supabase {table} HTTP {res.status_code}: {res.text[:180]}"
                log.warning("supabase upsert %s failed: %s", table, self.last_error)
                return False
            self.last_sync_at = utcnow()
            self.last_error = None
            return True
        except Exception as exc:
            self.last_error = str(exc)[:180]
            log.warning("supabase upsert %s error: %s", table, self.last_error)
            return False

    async def _postgrest_get(self, table: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        if not self.configured:
            return []
        url = f"{self.base_url}/rest/v1/{table}"
        try:
            res = await self._client().get(url, params=params, headers=self._headers())
            if res.status_code >= 400:
                self.last_error = f"Supabase GET {table} HTTP {res.status_code}"
                return []
            data = res.json()
            self.last_sync_at = utcnow()
            self.last_error = None
            return data if isinstance(data, list) else []
        except Exception as exc:
            self.last_error = str(exc)[:180]
            return []

    async def _rpc(self, fn_name: str, payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
        if not self.configured:
            return None
        url = f"{self.base_url}/rest/v1/rpc/{fn_name}"
        try:
            res = await self._client().post(url, headers=self._headers(), json=payload or {})
            if res.status_code >= 400:
                self.last_error = f"Supabase RPC {fn_name} HTTP {res.status_code}: {res.text[:180]}"
                return None
            data = res.json()
            self.last_sync_at = utcnow()
            self.last_error = None
            return data if isinstance(data, dict) else None
        except Exception as exc:
            self.last_error = str(exc)[:180]
            return None

    async def enqueue_candidate_tasks(
        self,
        *,
        cycle_id: str,
        mode: str,
        mint: str,
        symbol: str,
        evidence_summary: dict[str, Any],
        seats: tuple[str, ...] = ("SCAN", "VET", "SOCIAL", "CHIEF"),
        priority: int = 50,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        now = now or utcnow()
        expires = now + timedelta(hours=max(1, self.settings.supabase_task_ttl_hours))
        compact_ev = clamp_json_payload(evidence_summary, self.settings.supabase_max_payload_bytes)
        created_rows: list[dict[str, Any]] = []

        with session_scope() as session:
            for seat in seats:
                existing = session.scalar(
                    select(SwarmAgentTask).where(
                        SwarmAgentTask.mode == mode,
                        SwarmAgentTask.seat == seat,
                        SwarmAgentTask.mint == mint,
                        SwarmAgentTask.status == "pending",
                        SwarmAgentTask.expires_at > now,
                    )
                )
                if existing is not None:
                    existing.cycle_id = cycle_id
                    existing.evidence_summary = compact_ev
                    existing.expires_at = expires
                    task_id = existing.id
                else:
                    task_id = f"task-{seat.lower()}-{uuid.uuid4().hex[:16]}"
                    session.add(
                        SwarmAgentTask(
                            id=task_id,
                            cycle_id=cycle_id,
                            mode=mode,
                            seat=seat,
                            mint=mint,
                            symbol=symbol[:32],
                            status="pending",
                            priority=priority,
                            evidence_summary=compact_ev,
                            created_at=now,
                            expires_at=expires,
                        )
                    )
                created_rows.append(
                    {
                        "id": task_id,
                        "cycle_id": cycle_id,
                        "mode": mode,
                        "seat": seat,
                        "mint": mint,
                        "symbol": symbol[:32],
                        "status": "pending",
                        "priority": priority,
                        "evidence_summary": compact_ev,
                        "created_at": now.isoformat(),
                        "expires_at": expires.isoformat(),
                    }
                )

        if self.configured:
            await self._postgrest_upsert("swarm_agent_tasks", created_rows, on_conflict="id")
        return created_rows

    def list_pending_tasks(self, *, mode: str | None = None, seat: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        now = utcnow()
        with session_scope() as session:
            stmt = select(SwarmAgentTask).where(
                SwarmAgentTask.status == "pending",
                SwarmAgentTask.expires_at > now,
            )
            if mode:
                stmt = stmt.where(SwarmAgentTask.mode == mode)
            if seat:
                stmt = stmt.where(SwarmAgentTask.seat == seat.upper())
            rows = session.scalars(stmt.order_by(desc(SwarmAgentTask.priority), desc(SwarmAgentTask.created_at)).limit(min(max(limit, 1), 50))).all()
            return [
                {
                    "id": r.id,
                    "cycle_id": r.cycle_id,
                    "mode": r.mode,
                    "seat": r.seat,
                    "mint": r.mint,
                    "symbol": r.symbol,
                    "status": r.status,
                    "priority": r.priority,
                    "evidence_summary": r.evidence_summary,
                    "created_at": r.created_at.isoformat(),
                    "expires_at": r.expires_at.isoformat(),
                }
                for r in rows
            ]

    async def record_finding(
        self,
        *,
        mode: str,
        seat: str,
        mint: str,
        symbol: str,
        decision_source: str,
        verdict: str,
        confidence: Decimal | None,
        score: int | None,
        summary: str,
        finding_payload: dict[str, Any],
        task_id: str | None = None,
        cycle_id: str | None = None,
        agent_id: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if decision_source not in VALID_DECISION_SOURCES:
            raise ValueError(f"invalid decision_source {decision_source!r}")
        now = now or utcnow()
        expires = now + timedelta(hours=max(1, self.settings.supabase_finding_ttl_hours))
        compact_payload = clamp_json_payload(finding_payload, self.settings.supabase_max_payload_bytes)
        finding_id = f"fnd-{seat.lower()}-{uuid.uuid4().hex[:16]}"
        clean_summary = summary.strip()[:480]

        with session_scope() as session:
            matched_task_id = None
            if decision_source == "grok_bot":
                if task_id:
                    task_row = session.get(SwarmAgentTask, task_id)
                    if task_row:
                        task_row.status = "completed"
                        task_row.completed_at = now
                        matched_task_id = task_row.id
                else:
                    pending = session.scalar(
                        select(SwarmAgentTask).where(
                            SwarmAgentTask.mode == mode,
                            SwarmAgentTask.seat == seat.upper(),
                            SwarmAgentTask.mint == mint,
                            SwarmAgentTask.status == "pending",
                        )
                    )
                    if pending:
                        pending.status = "completed"
                        pending.completed_at = now
                        matched_task_id = pending.id

            session.add(
                SwarmAgentFinding(
                    id=finding_id,
                    task_id=matched_task_id,
                    cycle_id=cycle_id,
                    mode=mode,
                    seat=seat.upper(),
                    mint=mint,
                    symbol=symbol[:32],
                    decision_source=decision_source,
                    verdict=verdict,
                    confidence=confidence,
                    score=score,
                    summary=clean_summary,
                    finding_payload=compact_payload,
                    agent_id=agent_id,
                    created_at=now,
                    expires_at=expires,
                )
            )

        wire = {
            "id": finding_id,
            "task_id": matched_task_id,
            "cycle_id": cycle_id,
            "mode": mode,
            "seat": seat.upper(),
            "mint": mint,
            "symbol": symbol[:32],
            "decision_source": decision_source,
            "verdict": verdict,
            "confidence": float(confidence) if confidence is not None else None,
            "score": score,
            "summary": clean_summary,
            "finding_payload": compact_payload,
            "agent_id": agent_id,
            "created_at": now.isoformat(),
            "expires_at": expires.isoformat(),
        }
        if self.configured:
            await self._postgrest_upsert("swarm_agent_findings", [wire], on_conflict="id")
        return wire

    async def record_heartbeat(
        self,
        *,
        seat: str,
        status: str,
        decision_source: str,
        mode: str,
        active_mint: str | None = None,
        active_symbol: str | None = None,
        agent_id: str | None = None,
        note: str | None = None,
        meta: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if decision_source not in VALID_HEARTBEAT_SOURCES:
            raise ValueError(f"invalid heartbeat decision_source {decision_source!r}")
        now = now or utcnow()
        expires = now + timedelta(hours=max(1, self.settings.supabase_heartbeat_ttl_hours))
        seat_u = seat.upper()
        compact_meta = clamp_json_payload(meta or {}, min(2048, self.settings.supabase_max_payload_bytes))
        clean_note = (note or "").strip()[:280] or None

        with session_scope() as session:
            row = session.get(SwarmAgentHeartbeat, seat_u)
            if row is None:
                row = SwarmAgentHeartbeat(
                    seat=seat_u,
                    status=status[:24],
                    decision_source=decision_source,
                    active_mint=active_mint,
                    active_symbol=active_symbol[:32] if active_symbol else None,
                    mode=mode,
                    agent_id=agent_id,
                    note=clean_note,
                    meta=compact_meta,
                    last_seen_at=now,
                    expires_at=expires,
                )
                session.add(row)
            else:
                row.status = status[:24]
                row.decision_source = decision_source
                row.active_mint = active_mint
                row.active_symbol = active_symbol[:32] if active_symbol else None
                row.mode = mode
                row.agent_id = agent_id
                row.note = clean_note
                row.meta = compact_meta
                row.last_seen_at = now
                row.expires_at = expires

        wire = {
            "seat": seat_u,
            "status": status[:24],
            "decision_source": decision_source,
            "active_mint": active_mint,
            "active_symbol": active_symbol[:32] if active_symbol else None,
            "mode": mode,
            "agent_id": agent_id,
            "note": clean_note,
            "meta": compact_meta,
            "last_seen_at": now.isoformat(),
            "expires_at": expires.isoformat(),
        }
        if self.configured:
            await self._postgrest_upsert("swarm_agent_heartbeats", [wire], on_conflict="seat")
        return wire

    async def record_decision(
        self,
        *,
        cycle_id: str,
        mode: str,
        mint: str,
        symbol: str,
        action: str,
        decision_source: str,
        confidence: Decimal | None,
        size_factor: Decimal,
        thesis: str,
        reasons: list[str],
        risk_approved: bool | None = None,
        risk_reason_codes: list[str] | None = None,
        paper_order_id: str | None = None,
        decision_payload: dict[str, Any] | None = None,
        decision_id: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if decision_source not in VALID_DECISION_SOURCES:
            raise ValueError(f"invalid decision_source {decision_source!r}")
        now = now or utcnow()
        expires = now + timedelta(days=max(1, self.settings.supabase_decision_ttl_days))
        dec_id = decision_id or f"dec-{uuid.uuid4().hex[:18]}"
        compact_payload = clamp_json_payload(decision_payload or {}, self.settings.supabase_max_payload_bytes)
        clean_thesis = thesis.strip()[:600]
        clean_reasons = [str(r)[:120] for r in (reasons or [])[:12]]
        clean_risk_codes = [str(r)[:64] for r in (risk_reason_codes or [])[:12]]

        with session_scope() as session:
            existing = session.get(SwarmStructuredDecision, dec_id)
            if existing is None:
                session.add(
                    SwarmStructuredDecision(
                        id=dec_id,
                        cycle_id=cycle_id,
                        mode=mode,
                        mint=mint,
                        symbol=symbol[:32],
                        action=action,
                        decision_source=decision_source,
                        confidence=confidence,
                        size_factor=size_factor,
                        thesis=clean_thesis,
                        reasons=clean_reasons,
                        risk_approved=risk_approved,
                        risk_reason_codes=clean_risk_codes,
                        paper_order_id=paper_order_id,
                        decision_payload=compact_payload,
                        created_at=now,
                        expires_at=expires,
                    )
                )
            else:
                existing.risk_approved = risk_approved
                existing.risk_reason_codes = clean_risk_codes
                existing.paper_order_id = paper_order_id
                existing.decision_payload = compact_payload

        wire = {
            "id": dec_id,
            "cycle_id": cycle_id,
            "mode": mode,
            "mint": mint,
            "symbol": symbol[:32],
            "action": action,
            "decision_source": decision_source,
            "confidence": float(confidence) if confidence is not None else None,
            "size_factor": float(size_factor),
            "thesis": clean_thesis,
            "reasons": clean_reasons,
            "risk_approved": risk_approved,
            "risk_reason_codes": clean_risk_codes,
            "paper_order_id": paper_order_id,
            "decision_payload": compact_payload,
            "created_at": now.isoformat(),
            "expires_at": expires.isoformat(),
        }
        if self.configured:
            await self._postgrest_upsert("swarm_decisions", [wire], on_conflict="id")
        return wire

    async def sync_dashboard_snapshot(
        self,
        *,
        mode: str,
        emergency_stop: bool,
        equity_usdc: str | None,
        cash_usdc: str | None,
        open_positions: int,
        held_symbol: str | None,
        decision_source: str,
        snapshot: dict[str, Any],
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = now or utcnow()
        compact_snap = clamp_json_payload(snapshot, 8000)
        with session_scope() as session:
            row = session.get(SwarmDashboardState, "singleton")
            if row is None:
                session.add(
                    SwarmDashboardState(
                        slot="singleton",
                        mode=mode,
                        paper_trading=True,
                        live_execution_enabled=False,
                        emergency_stop=emergency_stop,
                        equity_usdc=equity_usdc,
                        cash_usdc=cash_usdc,
                        open_positions=open_positions,
                        held_symbol=held_symbol,
                        decision_source=decision_source,
                        snapshot=compact_snap,
                        updated_at=now,
                    )
                )
            else:
                row.mode = mode
                row.emergency_stop = emergency_stop
                row.equity_usdc = equity_usdc
                row.cash_usdc = cash_usdc
                row.open_positions = open_positions
                row.held_symbol = held_symbol
                row.decision_source = decision_source
                row.snapshot = compact_snap
                row.updated_at = now

        wire = {
            "slot": "singleton",
            "mode": mode,
            "paper_trading": True,
            "live_execution_enabled": False,
            "emergency_stop": emergency_stop,
            "equity_usdc": equity_usdc,
            "cash_usdc": cash_usdc,
            "open_positions": open_positions,
            "held_symbol": held_symbol,
            "decision_source": decision_source,
            "snapshot": compact_snap,
            "updated_at": now.isoformat(),
        }
        if self.configured:
            await self._postgrest_upsert("swarm_dashboard_state", [wire], on_conflict="slot")
        return wire

    async def pull_remote_grok_bot_updates(self, *, mode: str, now: datetime | None = None) -> int:
        """Pull fresh Grok Bot findings/heartbeats/decisions written directly to Supabase via Supabase MCP."""
        if not self.configured:
            return 0
        now = now or utcnow()
        cutoff = (now - timedelta(seconds=max(30, self.settings.grok_bot_freshness_seconds))).isoformat()
        imported = 0

        remote_hbs = await self._postgrest_get(
            "swarm_agent_heartbeats",
            {"decision_source": "eq.grok_bot", "last_seen_at": f"gte.{cutoff}", "limit": "8"},
        )
        remote_findings = await self._postgrest_get(
            "swarm_agent_findings",
            {
                "mode": f"eq.{mode}",
                "decision_source": "eq.grok_bot",
                "created_at": f"gte.{cutoff}",
                "order": "created_at.desc",
                "limit": "30",
            },
        )
        remote_decisions = await self._postgrest_get(
            "swarm_decisions",
            {
                "mode": f"eq.{mode}",
                "decision_source": "eq.grok_bot",
                "created_at": f"gte.{cutoff}",
                "order": "created_at.desc",
                "limit": "20",
            },
        )

        with session_scope() as session:
            for hb in remote_hbs:
                seat_u = str(hb.get("seat") or "").upper()
                if seat_u not in {"CHIEF", "SCAN", "VET", "SOCIAL"}:
                    continue
                seen_dt = _parse_iso(hb.get("last_seen_at"), now)
                exp_dt = _parse_iso(hb.get("expires_at"), now + timedelta(hours=24))
                existing_hb = session.get(SwarmAgentHeartbeat, seat_u)
                if existing_hb is None or existing_hb.last_seen_at < seen_dt:
                    if existing_hb is None:
                        session.add(
                            SwarmAgentHeartbeat(
                                seat=seat_u,
                                status=str(hb.get("status") or "online")[:24],
                                decision_source="grok_bot",
                                active_mint=hb.get("active_mint"),
                                active_symbol=hb.get("active_symbol"),
                                mode=str(hb.get("mode") or mode)[:8],
                                agent_id=hb.get("agent_id"),
                                note=hb.get("note"),
                                meta=hb.get("meta") if isinstance(hb.get("meta"), dict) else {},
                                last_seen_at=seen_dt,
                                expires_at=exp_dt,
                            )
                        )
                    else:
                        existing_hb.status = str(hb.get("status") or "online")[:24]
                        existing_hb.decision_source = "grok_bot"
                        existing_hb.active_mint = hb.get("active_mint")
                        existing_hb.active_symbol = hb.get("active_symbol")
                        existing_hb.agent_id = hb.get("agent_id")
                        existing_hb.note = hb.get("note")
                        existing_hb.last_seen_at = seen_dt
                        existing_hb.expires_at = exp_dt
                    imported += 1

            for fnd in remote_findings:
                fid = str(fnd.get("id") or "")
                seat_u = str(fnd.get("seat") or "").upper()
                verdict = str(fnd.get("verdict") or "")
                if not fid or seat_u not in {"SCAN", "VET", "SOCIAL"} or verdict not in {"pass", "warn", "kill"}:
                    continue
                if session.get(SwarmAgentFinding, fid) is None:
                    conf_raw = fnd.get("confidence")
                    session.add(
                        SwarmAgentFinding(
                            id=fid,
                            task_id=fnd.get("task_id"),
                            cycle_id=fnd.get("cycle_id"),
                            mode=mode,
                            seat=seat_u,
                            mint=str(fnd.get("mint") or ""),
                            symbol=str(fnd.get("symbol") or "UNKNOWN")[:32],
                            decision_source="grok_bot",
                            verdict=verdict,
                            confidence=Decimal(str(conf_raw)) if conf_raw is not None else None,
                            score=int(fnd["score"]) if fnd.get("score") is not None else None,
                            summary=str(fnd.get("summary") or "")[:480],
                            finding_payload=fnd.get("finding_payload") if isinstance(fnd.get("finding_payload"), dict) else {},
                            agent_id=fnd.get("agent_id"),
                            created_at=_parse_iso(fnd.get("created_at"), now),
                            expires_at=_parse_iso(fnd.get("expires_at"), now + timedelta(hours=72)),
                        )
                    )
                    imported += 1

            for dec in remote_decisions:
                did = str(dec.get("id") or "")
                action = str(dec.get("action") or "")
                if not did or action not in {"propose", "hold", "veto"}:
                    continue
                if session.get(SwarmStructuredDecision, did) is None:
                    conf_raw = dec.get("confidence")
                    sf_raw = dec.get("size_factor", "1.0")
                    session.add(
                        SwarmStructuredDecision(
                            id=did,
                            cycle_id=str(dec.get("cycle_id") or "remote"),
                            mode=mode,
                            mint=str(dec.get("mint") or ""),
                            symbol=str(dec.get("symbol") or "UNKNOWN")[:32],
                            action=action,
                            decision_source="grok_bot",
                            confidence=Decimal(str(conf_raw)) if conf_raw is not None else None,
                            size_factor=Decimal(str(sf_raw)),
                            thesis=str(dec.get("thesis") or "")[:600],
                            reasons=dec.get("reasons") if isinstance(dec.get("reasons"), list) else [],
                            risk_approved=dec.get("risk_approved"),
                            risk_reason_codes=dec.get("risk_reason_codes") if isinstance(dec.get("risk_reason_codes"), list) else [],
                            paper_order_id=dec.get("paper_order_id"),
                            decision_payload=dec.get("decision_payload") if isinstance(dec.get("decision_payload"), dict) else {},
                            created_at=_parse_iso(dec.get("created_at"), now),
                            expires_at=_parse_iso(dec.get("expires_at"), now + timedelta(days=14)),
                        )
                    )
                    imported += 1
        return imported

    def fresh_grok_bot_intelligence(self, *, mode: str, mint: str, now: datetime | None = None) -> dict[str, Any]:
        """Return fresh validated Grok Bot findings & CHIEF decision for `mint` within freshness window."""
        now = now or utcnow()
        cutoff = now - timedelta(seconds=max(15, self.settings.grok_bot_freshness_seconds))
        with session_scope() as session:
            findings: dict[str, SwarmAgentFinding] = {}
            rows = session.scalars(
                select(SwarmAgentFinding)
                .where(
                    SwarmAgentFinding.mode == mode,
                    SwarmAgentFinding.mint == mint,
                    SwarmAgentFinding.decision_source == "grok_bot",
                    SwarmAgentFinding.created_at >= cutoff,
                    SwarmAgentFinding.expires_at > now,
                )
                .order_by(desc(SwarmAgentFinding.created_at))
            ).all()
            for r in rows:
                if r.seat not in findings:
                    findings[r.seat] = r

            chief = session.scalar(
                select(SwarmStructuredDecision)
                .where(
                    SwarmStructuredDecision.mode == mode,
                    SwarmStructuredDecision.mint == mint,
                    SwarmStructuredDecision.decision_source == "grok_bot",
                    SwarmStructuredDecision.created_at >= cutoff,
                    SwarmStructuredDecision.expires_at > now,
                )
                .order_by(desc(SwarmStructuredDecision.created_at))
            )

            return {
                "has_grok_bot": bool(findings or chief),
                "findings": {
                    seat: {
                        "id": f.id,
                        "verdict": f.verdict,
                        "confidence": float(f.confidence) if f.confidence is not None else None,
                        "score": f.score,
                        "summary": f.summary,
                        "payload": f.finding_payload,
                        "agent_id": f.agent_id,
                        "created_at": f.created_at.isoformat(),
                    }
                    for seat, f in findings.items()
                },
                "chief": (
                    {
                        "id": chief.id,
                        "action": chief.action,
                        "confidence": float(chief.confidence) if chief.confidence is not None else None,
                        "size_factor": Decimal(str(chief.size_factor)),
                        "thesis": chief.thesis,
                        "reasons": chief.reasons,
                        "payload": chief.decision_payload,
                        "created_at": chief.created_at.isoformat(),
                    }
                    if chief is not None
                    else None
                ),
            }

    async def prune_expired(self, *, now: datetime | None = None) -> dict[str, Any]:
        """Enforce TTL expiration and row-count caps locally and on Supabase Free."""
        now = now or utcnow()
        pruned_tasks = 0
        pruned_findings = 0
        pruned_hbs = 0
        pruned_decisions = 0

        with session_scope() as session:
            # 1. Mark stale pending tasks expired and purge old expired tasks
            stale_pending = session.scalars(
                select(SwarmAgentTask).where(
                    SwarmAgentTask.status.in_(("pending", "claimed")),
                    SwarmAgentTask.expires_at < now,
                )
            ).all()
            for row in stale_pending:
                row.status = "expired"

            res_t = session.execute(delete(SwarmAgentTask).where(SwarmAgentTask.expires_at < now - timedelta(hours=6)))
            pruned_tasks += int(res_t.rowcount or 0)

            keep_task_ids = session.scalars(
                select(SwarmAgentTask.id).order_by(desc(SwarmAgentTask.created_at)).limit(self.max_tasks_rows)
            ).all()
            if keep_task_ids:
                res_to = session.execute(delete(SwarmAgentTask).where(SwarmAgentTask.id.notin_(keep_task_ids)))
                pruned_tasks += int(res_to.rowcount or 0)

            # 2. Findings TTL + cap
            res_f = session.execute(delete(SwarmAgentFinding).where(SwarmAgentFinding.expires_at < now))
            pruned_findings += int(res_f.rowcount or 0)
            keep_fnd_ids = session.scalars(
                select(SwarmAgentFinding.id).order_by(desc(SwarmAgentFinding.created_at)).limit(self.max_findings_rows)
            ).all()
            if keep_fnd_ids:
                res_fo = session.execute(delete(SwarmAgentFinding).where(SwarmAgentFinding.id.notin_(keep_fnd_ids)))
                pruned_findings += int(res_fo.rowcount or 0)

            # 3. Heartbeats TTL
            res_h = session.execute(delete(SwarmAgentHeartbeat).where(SwarmAgentHeartbeat.expires_at < now))
            pruned_hbs += int(res_h.rowcount or 0)

            # 4. Decisions TTL + cap
            res_d = session.execute(delete(SwarmStructuredDecision).where(SwarmStructuredDecision.expires_at < now))
            pruned_decisions += int(res_d.rowcount or 0)
            keep_dec_ids = session.scalars(
                select(SwarmStructuredDecision.id).order_by(desc(SwarmStructuredDecision.created_at)).limit(self.max_decisions_rows)
            ).all()
            if keep_dec_ids:
                res_do = session.execute(delete(SwarmStructuredDecision).where(SwarmStructuredDecision.id.notin_(keep_dec_ids)))
                pruned_decisions += int(res_do.rowcount or 0)

        remote_result = None
        if self.configured:
            remote_result = await self._rpc(
                "swarm_prune_expired_data",
                {
                    "p_max_tasks": self.max_tasks_rows,
                    "p_max_findings": self.max_findings_rows,
                    "p_max_decisions": self.max_decisions_rows,
                },
            )
            telemetry = await self._rpc("swarm_storage_telemetry")
            if telemetry:
                self.remote_telemetry = telemetry

        self.last_prune_at = now
        return {
            "pruned_at": now.isoformat(),
            "local": {
                "pruned_tasks": pruned_tasks,
                "pruned_findings": pruned_findings,
                "pruned_heartbeats": pruned_hbs,
                "pruned_decisions": pruned_decisions,
            },
            "supabase_remote": remote_result,
        }

    async def refresh_remote_telemetry(self) -> dict[str, Any] | None:
        if not self.configured:
            return None
        telemetry = await self._rpc("swarm_storage_telemetry")
        if telemetry:
            self.remote_telemetry = telemetry
        return self.remote_telemetry

    def local_coordination_metrics(self) -> dict[str, Any]:
        """Compute local coordination row counts and serialized byte footprint."""
        with session_scope() as session:
            t_count = int(session.scalar(select(func.count()).select_from(SwarmAgentTask)) or 0)
            f_count = int(session.scalar(select(func.count()).select_from(SwarmAgentFinding)) or 0)
            h_count = int(session.scalar(select(func.count()).select_from(SwarmAgentHeartbeat)) or 0)
            d_count = int(session.scalar(select(func.count()).select_from(SwarmStructuredDecision)) or 0)
            s_count = int(session.scalar(select(func.count()).select_from(SwarmDashboardState)) or 0)

            est_bytes = (
                t_count * 720
                + f_count * 900
                + h_count * 420
                + d_count * 980
                + s_count * 2400
            )
            return {
                "row_counts": {
                    "swarm_agent_tasks": t_count,
                    "swarm_agent_findings": f_count,
                    "swarm_agent_heartbeats": h_count,
                    "swarm_decisions": d_count,
                    "swarm_dashboard_state": s_count,
                },
                "estimated_coordination_bytes": est_bytes,
            }
