"""HTTP, SSE, and WebSocket API. State changes require the admin token."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from app.auth import Role, require_admin, resolve_role
from app.config import ROOT, Settings
from app.cost_monitor import cost_monitor_report, integrations_status_report
from app.db import init_engine, session_scope
from app.logging_config import configure_logging
from app.mcp_bridge import MCP_PROTOCOL_VERSION, MCP_TOOLS, handle_mcp_jsonrpc
from app.models import BacktestRun
from app.queries import (
    agents,
    alerts,
    analytics,
    backtest_detail,
    backtests,
    bench_list,
    coordination_state,
    decisions,
    demo_playback,
    desk_state,
    events,
    orders,
    portfolio,
    positions,
    providers,
    seat_inspector,
    settings_view,
    shadow_list,
    summary,
    token_detail,
    tokens,
)
from app.replay import run_replay
from app.swarm import STRATEGY_VERSION, Swarm
from app.tailscale_status import tailscale_health

DIST = ROOT / "frontend" / "dist"


class Hub:
    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()
        self.sse_queues: set[asyncio.Queue[dict]] = set()
        self.shutdown = asyncio.Event()

    async def publish(self, message: dict) -> None:
        dead = []
        for socket in list(self.clients):
            try:
                await socket.send_json(message)
            except Exception:
                dead.append(socket)
        for socket in dead:
            self.clients.discard(socket)
        for queue in list(self.sse_queues):
            try:
                if queue.full():
                    queue.get_nowait()
                queue.put_nowait(message)
            except Exception:
                self.sse_queues.discard(queue)


class SettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str | None = None
    scanner: dict | None = None
    strategy: dict | None = None
    risk: dict | None = None
    thresholds: dict | None = None
    simulation: dict | None = None
    ai: dict | None = None


class ThresholdsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hard: dict | None = None
    soft: dict | None = None
    shadow_mode: bool | None = None
    one_position_at_a_time: bool | None = None
    use_bench: bool | None = None


class StopBody(BaseModel):
    reason: str = Field(min_length=3, max_length=200)


class ModeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str


class ReplayBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    speed: int = Field(default=1, ge=1, le=20)
    notional: str = "20"


def _check_risk_patch(risk: dict) -> None:
    forbidden = {"live_trading", "execute", "leverage", "short", "live_execution_enabled"}
    if forbidden.intersection(risk) or any("key" in str(k).lower() or "phrase" in str(k).lower() for k in risk):
        raise HTTPException(status_code=422, detail="risk controls cannot enable execution or leverage")
    bounds = {
        "max_new_trade_usd": (Decimal("1"), Decimal("100")),
        "max_exposure_pct": (Decimal("0.01"), Decimal("0.20")),
        "daily_loss_pct": (Decimal("0.005"), Decimal("0.50")),
        "drawdown_pct": (Decimal("0.02"), Decimal("0.50")),
        "max_position_pct": (Decimal("0.005"), Decimal("0.10")),
        "min_pool_liquidity_usd": (Decimal("0"), Decimal("10000000")),
    }
    for key, (low, high) in bounds.items():
        if key in risk:
            value = Decimal(str(risk[key]))
            if value < low or value > high:
                raise HTTPException(status_code=422, detail=f"{key} is outside the allowed paper-trading range")


def _check_thresholds_patch(thresholds: dict) -> None:
    forbidden = {"live_trading", "execute", "swap", "live_execution_enabled"}
    if forbidden.intersection(thresholds):
        raise HTTPException(status_code=422, detail="thresholds cannot enable live trading")
    hard = thresholds.get("hard")
    if isinstance(hard, dict):
        if "min_age_minutes" in hard and not (0 <= float(hard["min_age_minutes"]) <= 1440):
            raise HTTPException(status_code=422, detail="min_age_minutes out of range")
        if "min_liquidity_usd" in hard and not (0 <= float(hard["min_liquidity_usd"]) <= 10_000_000):
            raise HTTPException(status_code=422, detail="min_liquidity_usd out of range")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    settings.require_tokens()
    configure_logging(settings)
    init_engine(settings)
    swarm = Swarm(settings)
    swarm.bootstrap()
    hub = Hub()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = None
        if settings.autostart:
            await swarm.run_cycle()
            await hub.publish({"type": "cycle", "summary": summary(settings, swarm)})

            async def loop() -> None:
                while True:
                    await asyncio.sleep(max(5, swarm.scan_interval()))
                    try:
                        await swarm.run_cycle()
                        await hub.publish({"type": "cycle", "summary": summary(settings, swarm)})
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        await hub.publish({"type": "cycle_error"})

            task = asyncio.create_task(loop())
        yield
        hub.shutdown.set()
        if task:
            task.cancel()
        await swarm.aclose()

    app = FastAPI(title="SOL SWARM", version="0.2.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.swarm = swarm
    app.state.hub = hub
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def reader_header(
        authorization: str | None = Header(default=None),
        x_solswarm_token: str | None = Header(default=None),
    ) -> Role:
        return resolve_role(settings, authorization, x_solswarm_token)

    def stream_reader(
        token: str | None = Query(default=None),
        authorization: str | None = Header(default=None),
        x_solswarm_token: str | None = Header(default=None),
    ) -> Role:
        auth_val = authorization or (f"Bearer {token}" if token else None)
        return resolve_role(settings, auth_val, x_solswarm_token)

    def admin_header(role: Role = Depends(reader_header)) -> Role:
        return require_admin(role)

    @app.get("/api/health")
    def health():
        return {
            "status": "ok",
            "paper_trading": True,
            "live_execution_enabled": False,
            "venue": "gmgn_paper_only",
            "service": "sol-swarm",
        }

    @app.get("/api/summary")
    def get_summary(_role: Role = Depends(reader_header)):
        return summary(settings, swarm)

    @app.get("/api/desk")
    def get_desk(_role: Role = Depends(reader_header)):
        return desk_state(settings, swarm)

    @app.get("/api/desk/demo-playback")
    def get_demo_playback(_role: Role = Depends(reader_header)):
        return demo_playback()

    @app.get("/api/desk/seats/{seat}")
    def get_seat(seat: str, _role: Role = Depends(reader_header)):
        info = seat_inspector(seat, settings, swarm)
        if info is None:
            raise HTTPException(status_code=404, detail="unknown desk seat")
        return info

    @app.get("/api/bench")
    def get_bench(_role: Role = Depends(reader_header)):
        return bench_list()

    @app.get("/api/shadow")
    def get_shadow(_role: Role = Depends(reader_header)):
        return shadow_list()

    @app.get("/api/thresholds")
    def get_thresholds(_role: Role = Depends(reader_header)):
        return desk_state(settings, swarm)["thresholds"]

    @app.put("/api/thresholds")
    async def put_thresholds(body: ThresholdsPatch, _role: Role = Depends(admin_header)):
        patch_dict: dict = {}
        th_patch: dict = {}
        if body.hard is not None:
            th_patch["hard"] = body.hard
        if body.soft is not None:
            th_patch["soft"] = body.soft
        if th_patch:
            _check_thresholds_patch(th_patch)
            patch_dict["thresholds"] = th_patch
        scanner_patch: dict = {}
        if body.shadow_mode is not None:
            scanner_patch["shadow_mode"] = body.shadow_mode
        if body.one_position_at_a_time is not None:
            scanner_patch["one_position_at_a_time"] = body.one_position_at_a_time
        if body.use_bench is not None:
            scanner_patch["use_bench"] = body.use_bench
        if scanner_patch:
            patch_dict["scanner"] = scanner_patch
        if "hard" in th_patch:
            risk_sync: dict = {}
            if "min_liquidity_usd" in th_patch["hard"]:
                risk_sync["min_pool_liquidity_usd"] = str(th_patch["hard"]["min_liquidity_usd"])
            if "min_age_minutes" in th_patch["hard"]:
                risk_sync["min_pool_age_minutes"] = int(th_patch["hard"]["min_age_minutes"])
            if risk_sync:
                _check_risk_patch(risk_sync)
                patch_dict["risk"] = risk_sync
        swarm.update_settings(patch_dict, actor="admin")
        await hub.publish({"type": "thresholds_updated"})
        return desk_state(settings, swarm)["thresholds"]

    @app.get("/api/portfolio")
    def get_portfolio(_role: Role = Depends(reader_header)):
        return portfolio()

    @app.get("/api/positions")
    def get_positions(_role: Role = Depends(reader_header)):
        return positions()

    @app.get("/api/orders")
    def get_orders(_role: Role = Depends(reader_header)):
        return orders()

    @app.get("/api/tokens")
    def get_tokens(_role: Role = Depends(reader_header)):
        return tokens()

    @app.get("/api/tokens/{mint:path}")
    def get_token(mint: str, _role: Role = Depends(reader_header)):
        detail = token_detail(mint)
        if detail is None:
            raise HTTPException(status_code=404, detail="token not found in the active mode")
        return detail

    @app.get("/api/agents")
    def get_agents(_role: Role = Depends(reader_header)):
        return agents()

    @app.get("/api/events")
    def get_events(_role: Role = Depends(reader_header)):
        return events()

    @app.get("/api/events/stream")
    async def get_events_stream(request: Request, _role: Role = Depends(stream_reader)):
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=32)
        hub.sse_queues.add(queue)

        async def generator():
            try:
                hello = {"type": "hello", "paper_trading": True, "live_execution_enabled": False}
                yield f"data: {json.dumps(hello)}\n\n"
                while not hub.shutdown.is_set():
                    if await request.is_disconnected():
                        break
                    try:
                        msg = await asyncio.wait_for(queue.get(), timeout=2.0)
                        yield f"data: {json.dumps(msg)}\n\n"
                    except TimeoutError:
                        yield ": keepalive\n\n"
            finally:
                hub.sse_queues.discard(queue)

        return StreamingResponse(
            generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/decisions")
    def get_decisions(_role: Role = Depends(reader_header)):
        return decisions()

    @app.get("/api/analytics")
    def get_analytics(_role: Role = Depends(reader_header)):
        return analytics()

    @app.get("/api/settings")
    def get_settings(_role: Role = Depends(reader_header)):
        return settings_view(settings)

    @app.put("/api/settings")
    async def put_settings(body: SettingsPatch, _role: Role = Depends(admin_header)):
        patch = body.model_dump(exclude_none=True)
        if "risk" in patch:
            _check_risk_patch(patch["risk"])
        if "thresholds" in patch:
            _check_thresholds_patch(patch["thresholds"])
        if "strategy" in patch and "live_trading" in patch["strategy"]:
            raise HTTPException(status_code=422, detail="strategy cannot enable live trading")
        try:
            updated = swarm.update_settings(patch, actor="admin")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        await hub.publish({"type": "settings_updated"})
        return settings_view(settings) | {"saved": True, "mode": updated.get("mode")}

    @app.get("/api/providers")
    def get_providers(_role: Role = Depends(reader_header)):
        return providers()

    @app.get("/api/alerts")
    def get_alerts(_role: Role = Depends(reader_header)):
        return alerts()

    @app.get("/api/tailscale")
    def get_tailscale(_role: Role = Depends(reader_header)):
        return tailscale_health()

    @app.get("/api/cost-monitor")
    async def get_cost_monitor(_role: Role = Depends(reader_header)):
        if settings.supabase_configured and swarm.coord.remote_telemetry is None:
            await swarm.coord.refresh_remote_telemetry()
        return cost_monitor_report(settings, swarm)

    @app.get("/api/integrations/status")
    async def get_integrations_status(_role: Role = Depends(reader_header)):
        if settings.supabase_configured and swarm.coord.remote_telemetry is None:
            await swarm.coord.refresh_remote_telemetry()
        return integrations_status_report(settings, swarm)

    @app.get("/api/coordination")
    def get_coordination(_role: Role = Depends(reader_header)):
        return coordination_state(swarm)

    @app.get("/api/mcp")
    def get_mcp_info(role: Role = Depends(reader_header)):
        ts = tailscale_health()
        if ts.get("funnel_enabled"):
            raise HTTPException(status_code=403, detail="Tailscale Funnel detected; MCP public exposure is forbidden")
        return {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "transport": "streamable-http",
            "endpoint": "/api/mcp",
            "role": role.value,
            "tools": [t["name"] for t in MCP_TOOLS if role in {Role.ADMIN, Role.AGENT} or t["annotations"]["readOnlyHint"]],
            "tailscale_serve_only": True,
        }

    @app.post("/api/mcp")
    async def post_mcp(request: Request, role: Role = Depends(reader_header)):
        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="invalid JSON body") from exc
        status_code, payload = await handle_mcp_jsonrpc(body, role=role, settings=settings, swarm=swarm)
        if payload is None:
            return Response(status_code=status_code)
        if status_code == 200 and isinstance(payload, dict) and isinstance(payload.get("result"), dict):
            sc = payload["result"].get("structuredContent")
            if isinstance(sc, dict) and sc.get("accepted") is True:
                await hub.publish({"type": "grok_bot_mcp", "summary": summary(settings, swarm)})
        return JSONResponse(status_code=status_code, content=payload)

    @app.post("/api/control/prune-retention")
    async def post_prune(_role: Role = Depends(admin_header)):
        result = await swarm.coord.prune_expired()
        return result

    @app.post("/api/control/emergency-stop")
    async def post_stop(body: StopBody, _role: Role = Depends(admin_header)):
        result = swarm.emergency_stop(body.reason, actor="admin")
        await hub.publish({"type": "emergency_stop", "result": result})
        return result

    @app.post("/api/control/resume")
    async def post_resume(_role: Role = Depends(admin_header)):
        result = swarm.resume_entries(actor="admin")
        await hub.publish({"type": "resume", "result": result})
        return result

    @app.post("/api/control/mode")
    async def post_mode(body: ModeBody, _role: Role = Depends(admin_header)):
        if body.mode not in {"demo", "live"}:
            raise HTTPException(status_code=422, detail="mode must be demo or live")
        swarm.update_settings({"mode": body.mode}, actor="admin")
        await hub.publish({"type": "mode_changed", "mode": body.mode})
        return {"mode": body.mode, "paper_trading": True, "live_execution_enabled": False}

    @app.post("/api/control/cycle")
    async def post_cycle(_role: Role = Depends(admin_header)):
        result = await swarm.run_cycle()
        await hub.publish({"type": "cycle", "summary": summary(settings, swarm)})
        return result

    @app.get("/api/backtests")
    def get_backtests(_role: Role = Depends(reader_header)):
        return backtests()

    @app.get("/api/backtests/{run_id}")
    def get_backtest(run_id: str, _role: Role = Depends(reader_header)):
        row = backtest_detail(run_id)
        if row is None:
            raise HTTPException(status_code=404, detail="replay not found")
        return row

    @app.post("/api/backtests")
    def post_backtest(body: ReplayBody, _role: Role = Depends(admin_header)):
        dataset_path = Path(__file__).parent / "data" / "replay_demo.json"
        dataset = json.loads(dataset_path.read_text())
        before = _order_count()
        result = run_replay(dataset, {"notional": body.notional, "speed": body.speed})
        after = _order_count()
        if after != before:
            raise HTTPException(status_code=500, detail="replay must not write the paper book")
        run_id = __import__("uuid").uuid4().hex
        disclosure = {
            "text": result["disclosure"],
            "label": result["label"],
            "point_in_time_security": result["point_in_time_security"],
            "isolated_from_paper_book": True,
        }
        with session_scope() as session:
            session.add(
                BacktestRun(
                    id=run_id,
                    created_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
                    status="completed",
                    params={"speed": body.speed, "notional": body.notional, "dataset": dataset.get("dataset")},
                    disclosure=disclosure,
                    results=result,
                    strategy_version=STRATEGY_VERSION,
                )
            )
        return {"id": run_id, "disclosure": disclosure, "results": result}

    def _order_count() -> int:
        from sqlalchemy import func, select

        from app.models import PaperOrder

        with session_scope() as session:
            return int(session.scalar(select(func.count()).select_from(PaperOrder)) or 0)

    # Read-only monitor surface for external agents (Claude, Grok Bot). No control routes exist here.
    @app.get("/api/monitor/summary")
    def monitor_summary(_role: Role = Depends(reader_header)):
        return summary(settings, swarm)

    @app.get("/api/monitor/desk")
    def monitor_desk(_role: Role = Depends(reader_header)):
        return desk_state(settings, swarm)

    @app.get("/api/monitor/bench")
    def monitor_bench(_role: Role = Depends(reader_header)):
        return bench_list()

    @app.get("/api/monitor/shadow")
    def monitor_shadow(_role: Role = Depends(reader_header)):
        return shadow_list()

    @app.get("/api/monitor/thresholds")
    def monitor_thresholds(_role: Role = Depends(reader_header)):
        return desk_state(settings, swarm)["thresholds"]

    @app.get("/api/monitor/portfolio")
    def monitor_portfolio(_role: Role = Depends(reader_header)):
        return portfolio()

    @app.get("/api/monitor/positions")
    def monitor_positions(_role: Role = Depends(reader_header)):
        return positions()

    @app.get("/api/monitor/orders")
    def monitor_orders(_role: Role = Depends(reader_header)):
        return orders()

    @app.get("/api/monitor/agents")
    def monitor_agents(_role: Role = Depends(reader_header)):
        return agents()

    @app.get("/api/monitor/alerts")
    def monitor_alerts(_role: Role = Depends(reader_header)):
        return alerts()

    @app.get("/api/monitor/providers")
    def monitor_providers(_role: Role = Depends(reader_header)):
        return providers()

    @app.get("/api/monitor/tailscale")
    def monitor_tailscale(_role: Role = Depends(reader_header)):
        return tailscale_health()

    @app.get("/api/monitor/decisions")
    def monitor_decisions(_role: Role = Depends(reader_header)):
        return decisions()

    @app.get("/api/monitor/cost-monitor")
    async def monitor_cost_monitor(_role: Role = Depends(reader_header)):
        if settings.supabase_configured and swarm.coord.remote_telemetry is None:
            await swarm.coord.refresh_remote_telemetry()
        return cost_monitor_report(settings, swarm)

    @app.get("/api/monitor/integrations/status")
    async def monitor_integrations_status(_role: Role = Depends(reader_header)):
        if settings.supabase_configured and swarm.coord.remote_telemetry is None:
            await swarm.coord.refresh_remote_telemetry()
        return integrations_status_report(settings, swarm)

    @app.websocket("/ws")
    async def ws(socket: WebSocket, token: str = ""):
        try:
            resolve_role(settings, f"Bearer {token}" if token else None, None)
        except HTTPException:
            await socket.close(code=4401)
            return
        await socket.accept()
        hub.clients.add(socket)
        try:
            await socket.send_json({"type": "hello", "paper_trading": True, "summary": summary(settings, swarm)})
            while True:
                await socket.receive_text()
        except WebSocketDisconnect:
            hub.clients.discard(socket)
        finally:
            hub.clients.discard(socket)

    @app.api_route(
        "/api/{unmatched:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        include_in_schema=False,
    )
    def unmatched_api(unmatched: str):
        raise HTTPException(status_code=404, detail=f"unknown api route /api/{unmatched}")

    index = DIST / "index.html"
    if index.exists():
        assets = DIST / "assets"
        if assets.exists():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{path:path}")
        def spa(path: str):
            if path.startswith("api") or path == "ws":
                raise HTTPException(status_code=404)
            candidate = DIST / path
            if path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(index)

    return app


# Uvicorn entry: `uvicorn app.main:create_app --factory`
