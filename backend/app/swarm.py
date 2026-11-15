"""Orchestrator. Discovery never reaches an order without the risk engine."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import desc, func, select

from app.accounting import position_mark_value
from app.analysis import (
    STRATEGY_VERSION,
    AuthorityFacts,
    MomentumReport,
    Observation,
    SecurityReport,
    classify_security,
    dedupe_by_mint,
    discovery_score,
    exit_reason,
    momentum_from_observations,
    propose_entry,
)
from app.config import Settings
from app.db import session_scope, utcnow
from app.demo import demo_exit_routes, demo_facts, demo_observations, demo_quote
from app.execution import plan_buy, plan_sell
from app.grok_client import GrokClient
from app.models import (
    AgentEvent,
    AgentHealth,
    Alert,
    AppSetting,
    ConfigVersion,
    EquitySnapshot,
    LedgerEntry,
    LiquidityPool,
    MarketObservation,
    PaperFill,
    PaperOrder,
    PortfolioState,
    Position,
    ProviderHealth,
    RiskDecision,
    SecurityAssessment,
    StrategyProposal,
    Token,
)
from app.money import q_usdc
from app.providers.http import ProviderClient, ProviderError, ProviderSnapshot
from app.providers.jupiter import JupiterAdapter
from app.providers.normalize import parse_boosted_mints, parse_dexscreener_pairs, parse_geckoterminal_pools
from app.providers.solana import SolanaRpc
from app.risk import PortfolioView, ProposalView, QuoteView, RiskLimits, SecurityView, evaluate_entry

log = logging.getLogger(__name__)

AGENTS = (
    "orchestrator",
    "discovery",
    "momentum",
    "security",
    "strategy",
    "risk",
    "execution",
    "monitoring",
)

DEFAULT_CONFIG = {
    "mode": "demo",
    "scanner": {"interval_seconds": 60, "max_candidates": 8, "min_discovery_score": 35},
    "strategy": {
        "version": STRATEGY_VERSION,
        "momentum_threshold": 65,
        "allow_promoted": False,
        "allow_active_mint": False,
    },
    "risk": {
        "max_new_trade_usd": "20",
        "max_concurrent_positions": 3,
        "max_exposure_pct": "0.06",
        "daily_loss_pct": "0.03",
        "drawdown_pct": "0.10",
        "max_position_pct": "0.02",
        "min_pool_liquidity_usd": "50000",
        "min_pool_age_minutes": 15,
        "max_slippage_bps": "150",
        "max_price_impact_bps": "200",
        "max_fee_bps": "300",
        "quote_max_age_seconds": 20,
        "market_max_age_seconds": 90,
        "security_max_age_seconds": 300,
        "cooldown_seconds": 600,
        "max_pool_participation": "0.01",
    },
    "simulation": {"network_fee_usdc": "0.02", "network_fee_source": "assumed"},
    "ai": {"hourly_budget": 20},
}


def limits_from_config(config: dict) -> RiskLimits:
    raw = config.get("risk") or {}
    return RiskLimits(
        max_new_trade_usd=Decimal(str(raw.get("max_new_trade_usd", "20"))),
        max_concurrent_positions=int(raw.get("max_concurrent_positions", 3)),
        max_exposure_pct=Decimal(str(raw.get("max_exposure_pct", "0.06"))),
        daily_loss_pct=Decimal(str(raw.get("daily_loss_pct", "0.03"))),
        drawdown_pct=Decimal(str(raw.get("drawdown_pct", "0.10"))),
        max_position_pct=Decimal(str(raw.get("max_position_pct", "0.02"))),
        min_pool_liquidity_usd=Decimal(str(raw.get("min_pool_liquidity_usd", "50000"))),
        min_pool_age_minutes=int(raw.get("min_pool_age_minutes", 15)),
        max_slippage_bps=Decimal(str(raw.get("max_slippage_bps", "150"))),
        max_price_impact_bps=Decimal(str(raw.get("max_price_impact_bps", "200"))),
        max_fee_bps=Decimal(str(raw.get("max_fee_bps", "300"))),
        quote_max_age_seconds=int(raw.get("quote_max_age_seconds", 20)),
        market_max_age_seconds=int(raw.get("market_max_age_seconds", 90)),
        security_max_age_seconds=int(raw.get("security_max_age_seconds", 300)),
        cooldown_seconds=int(raw.get("cooldown_seconds", 600)),
        max_pool_participation=Decimal(str(raw.get("max_pool_participation", "0.01"))),
    )


class Swarm:
    def __init__(
        self,
        settings: Settings,
        *,
        gecko: ProviderClient | None = None,
        dex: ProviderClient | None = None,
        jupiter: JupiterAdapter | None = None,
        rpc: SolanaRpc | None = None,
        grok: GrokClient | None = None,
    ) -> None:
        self.settings = settings
        self.gecko = gecko or ProviderClient("geckoterminal", per_minute=8)
        self.dex = dex or ProviderClient("dexscreener", per_minute=60)
        self.jupiter = jupiter or JupiterAdapter(
            ProviderClient("jupiter", per_minute=30),
            settings.jupiter_api_key,
            settings.jupiter_base,
        )
        self.rpc = rpc or SolanaRpc(ProviderClient("solana_rpc", per_minute=20), settings.solana_rpc_url)
        self.grok = grok or GrokClient(
            ProviderClient("grok", per_minute=20),
            settings.xai_api_key,
            settings.xai_model,
            settings.xai_base_url,
            settings.ai_hourly_budget,
        )
        self.momentum_delay = 0.0
        self._sequence: dict[str, int] = {}

    async def aclose(self) -> None:
        await self.gecko.aclose()
        await self.dex.aclose()
        await self.jupiter.client.aclose()
        await self.rpc.client.aclose()
        await self.grok.client.aclose()

    def bootstrap(self) -> None:
        from app.db import Base, get_engine

        Base.metadata.create_all(get_engine())
        with session_scope() as session:
            from app.models import SchemaMigration

            if session.get(SchemaMigration, 1) is None:
                session.add(SchemaMigration(version=1, applied_at=utcnow()))
            if session.get(AppSetting, "runtime") is None:
                config = dict(DEFAULT_CONFIG)
                config["mode"] = self.settings.mode
                config["ai"]["hourly_budget"] = self.settings.ai_hourly_budget
                session.add(AppSetting(key="runtime", value=config, updated_at=utcnow()))
                session.add(
                    ConfigVersion(created_at=utcnow(), actor="system", document=config, note="initial configuration")
                )
            for name in AGENTS:
                if session.get(AgentHealth, name) is None:
                    session.add(
                        AgentHealth(
                            agent_name=name,
                            status="idle",
                            processed_count=0,
                            error_count=0,
                        )
                    )
            self._ensure_state(session, self._mode(session), utcnow())
            self._recover_pending(session)

    def scan_interval(self) -> int:
        with session_scope() as session:
            config = self._config(session)
        return int((config.get("scanner") or {}).get("interval_seconds") or self.settings.scan_interval_seconds)

    def _mode(self, session) -> str:
        row = session.get(AppSetting, "runtime")
        if row and row.value.get("mode") in {"demo", "live"}:
            return row.value["mode"]
        return self.settings.mode

    def _config(self, session) -> dict:
        row = session.get(AppSetting, "runtime")
        return row.value if row else dict(DEFAULT_CONFIG)

    def _ensure_state(self, session, mode: str, now: datetime) -> PortfolioState:
        state = session.get(PortfolioState, mode)
        if state is None:
            initial = q_usdc(self.settings.initial_equity)
            state = PortfolioState(
                mode=mode,
                cash=initial,
                reserved=Decimal("0"),
                peak_equity=initial,
                day_start_equity=initial,
                day_utc=now.astimezone(timezone.utc).date().isoformat(),
                emergency_stop=False,
                initial_equity=initial,
                updated_at=now,
            )
            session.add(state)
            session.add(
                LedgerEntry(
                    mode=mode,
                    created_at=now,
                    kind="seed",
                    amount_usdc=initial,
                    cash_after=initial,
                    ref=None,
                    memo="Simulated starting USDC. No wallet is connected.",
                )
            )
        return state

    def _recover_pending(self, session) -> None:
        pending = session.scalars(select(PaperOrder).where(PaperOrder.status == "pending")).all()
        for order in pending:
            if order.side == "buy":
                order.status = "cancelled"
                order.rejection_reason = "restart cancelled unfilled entry"
                order.updated_at = utcnow()
                state = session.get(PortfolioState, order.mode)
                if state is not None:
                    state.reserved = Decimal("0")
                    state.updated_at = utcnow()

    def _emit(
        self,
        session,
        *,
        source: str,
        event_type: str,
        correlation_id: str,
        mode: str,
        payload: dict,
        mint: str | None,
        now: datetime,
        event_id: str | None = None,
    ) -> bool:
        event_id = event_id or str(uuid.uuid4())
        if session.get(AgentEvent, event_id) is not None:
            return False
        self._sequence[correlation_id] = self._sequence.get(correlation_id, 0) + 1
        session.add(
            AgentEvent(
                id=event_id,
                created_at=now,
                source_agent=source,
                event_type=event_type,
                mint=mint,
                payload=payload,
                correlation_id=correlation_id,
                status="processed",
                mode=mode,
                sequence=self._sequence[correlation_id],
            )
        )
        return True

    def _touch(self, session, agent: str, task: str, decision: str, started: datetime, now: datetime, error: str | None = None) -> None:
        row = session.get(AgentHealth, agent)
        if row is None:
            return
        row.status = "error" if error else "ok"
        row.last_heartbeat = now
        row.last_task = task
        row.last_duration_ms = int((now - started).total_seconds() * 1000)
        row.processed_count += 0 if error else 1
        row.last_decision = decision[:500]
        if error:
            row.error_count += 1
            row.last_error = error[:500]

    def _save_provider(self, session, snap: ProviderSnapshot) -> None:
        row = session.get(ProviderHealth, snap.provider)
        if row is None:
            row = ProviderHealth(provider=snap.provider, status=snap.status, configured=snap.configured, circuit=snap.circuit)
            session.add(row)
        row.status = snap.status
        row.configured = snap.configured
        row.circuit = snap.circuit
        row.consecutive_failures = snap.consecutive_failures
        row.latency_ms = snap.latency_ms
        row.last_error = snap.last_error
        row.detail = snap.detail
        if snap.status == "ok":
            row.last_ok_at = utcnow()

    def _persist_observation(self, session, row: Observation, score: int) -> None:
        token = session.scalar(select(Token).where(Token.mode == row.mode, Token.mint == row.mint))
        if token is None:
            session.add(
                Token(
                    mode=row.mode,
                    mint=row.mint,
                    name=row.name,
                    symbol=row.symbol,
                    decimals=row.decimals,
                    token_program=None,
                    holder_count=row.holder_count,
                    promoted=row.promoted,
                    first_seen_at=row.observed_at,
                    token_created_at=row.token_created_at,
                )
            )
        else:
            token.promoted = row.promoted or token.promoted
            token.holder_count = row.holder_count or token.holder_count
            token.name = row.name
            token.symbol = row.symbol
        pool = session.scalar(
            select(LiquidityPool).where(LiquidityPool.mode == row.mode, LiquidityPool.pool_address == row.pool_address)
        )
        if pool is None and row.pool_address:
            session.add(
                LiquidityPool(
                    mode=row.mode,
                    pool_address=row.pool_address,
                    mint=row.mint,
                    quote_mint=row.quote_mint,
                    dex=row.dex,
                    pool_created_at=row.pool_created_at,
                    first_seen_at=row.observed_at,
                )
            )
        session.add(
            MarketObservation(
                mode=row.mode,
                mint=row.mint,
                pool_address=row.pool_address,
                provider=row.provider,
                provider_reported_at=row.provider_reported_at,
                observed_at=row.observed_at,
                price_usd=row.price_usd,
                market_cap_usd=row.market_cap_usd,
                fdv_usd=row.fdv_usd,
                pool_liquidity_usd=row.pool_liquidity_usd,
                token_liquidity_usd=row.token_liquidity_usd,
                volume_m5=row.volume_m5,
                volume_h1=row.volume_h1,
                price_change_m5=row.price_change_m5,
                price_change_h1=row.price_change_h1,
                buys_m5=row.buys_m5,
                sells_m5=row.sells_m5,
                buys_h1=row.buys_h1,
                sells_h1=row.sells_h1,
                unique_buyers_m5=row.unique_buyers_m5,
                discovery_score=score,
                promoted=row.promoted,
                provenance={"provider": row.provider, "promoted_listing": row.promoted},
            )
        )

    def _seed_demo_chart(self, session, now: datetime) -> None:
        """Prior demo prints so the chart is not a single dot. Labeled as fixtures."""
        from app.models import MarketObservation

        existing = session.scalar(
            select(func.count()).select_from(MarketObservation).where(MarketObservation.mode == "demo", MarketObservation.mint == "demo:momentum")
        )
        if int(existing or 0) > 4:
            return
        price = Decimal("0.88")
        for step in range(12, 0, -1):
            session.add(
                MarketObservation(
                    mode="demo",
                    mint="demo:momentum",
                    pool_address="demo-pool-demo-mom",
                    provider="demo_fixture",
                    provider_reported_at=now - timedelta(minutes=5 * step),
                    observed_at=now - timedelta(minutes=5 * step),
                    price_usd=price,
                    pool_liquidity_usd=Decimal("76000") + Decimal(step * 200),
                    volume_m5=Decimal("4000"),
                    volume_h1=Decimal("18000"),
                    price_change_m5=Decimal("0.4"),
                    price_change_h1=Decimal("1.2"),
                    buys_m5=12,
                    sells_m5=9,
                    discovery_score=70,
                    promoted=False,
                    provenance={"provider": "demo_fixture", "note": "synthetic history for demo mode"},
                )
            )
            price += Decimal("0.01")

    def _previous_observation(self, session, mode: str, mint: str, before: datetime) -> Observation | None:
        row = session.scalar(
            select(MarketObservation)
            .where(
                MarketObservation.mode == mode,
                MarketObservation.mint == mint,
                MarketObservation.observed_at < before,
            )
            .order_by(desc(MarketObservation.observed_at))
        )
        if row is None:
            return None
        return Observation(
            mode=row.mode,
            mint=row.mint,
            name="",
            symbol="",
            decimals=6,
            pool_address=row.pool_address,
            dex=None,
            quote_mint=None,
            pool_created_at=None,
            token_created_at=None,
            price_usd=row.price_usd,
            market_cap_usd=row.market_cap_usd,
            fdv_usd=row.fdv_usd,
            pool_liquidity_usd=row.pool_liquidity_usd,
            token_liquidity_usd=row.token_liquidity_usd,
            volume_m5=row.volume_m5,
            volume_h1=row.volume_h1,
            price_change_m5=row.price_change_m5,
            price_change_h1=row.price_change_h1,
            buys_m5=row.buys_m5,
            sells_m5=row.sells_m5,
            buys_h1=row.buys_h1,
            sells_h1=row.sells_h1,
            unique_buyers_m5=row.unique_buyers_m5,
            holder_count=None,
            provider=row.provider,
            provider_reported_at=row.provider_reported_at,
            observed_at=row.observed_at,
            promoted=row.promoted,
        )

    async def discover_live(self, now: datetime) -> list[Observation]:
        rows: list[Observation] = []
        headers = {"Accept": "application/json;version=20230203"}
        try:
            payload = await self.gecko.get_json(
                f"{self.settings.gecko_base}/networks/solana/new_pools",
                params={"include": "base_token,quote_token,dex", "page": 1},
                headers=headers,
            )
            rows.extend(parse_geckoterminal_pools(payload, mode="live", observed_at=now))
        except ProviderError as exc:
            log.warning("geckoterminal new pools unavailable: %s", exc.message)
        try:
            payload = await self.gecko.get_json(
                f"{self.settings.gecko_base}/networks/solana/trending_pools",
                params={"include": "base_token,quote_token,dex", "page": 1},
                headers=headers,
            )
            rows.extend(parse_geckoterminal_pools(payload, mode="live", observed_at=now))
        except ProviderError as exc:
            log.warning("geckoterminal trending unavailable: %s", exc.message)
        promoted: set[str] = set()
        try:
            boosts = await self.dex.get_json(f"{self.settings.dexscreener_base}/token-boosts/latest/v1")
            promoted = parse_boosted_mints(boosts)
        except ProviderError as exc:
            log.warning("dexscreener boosts unavailable: %s", exc.message)
        mints = list(dict.fromkeys(row.mint for row in rows))[:30]
        if mints:
            try:
                payload = await self.dex.get_json(
                    f"{self.settings.dexscreener_base}/tokens/v1/solana/{','.join(mints)}"
                )
                rows.extend(
                    parse_dexscreener_pairs(payload, mode="live", observed_at=now, promoted_mints=promoted)
                )
            except ProviderError as exc:
                log.warning("dexscreener tokens unavailable: %s", exc.message)
        for row in rows:
            if row.mint in promoted:
                row.promoted = True
        return rows

    async def _quote(self, mode: str, mint: str, side: str, price: Decimal | None, notional: Decimal, decimals: int, now: datetime) -> QuoteView | None:
        if mode == "demo":
            if price is None:
                return None
            return demo_quote(now, mint, side, price, notional)
        if not self.jupiter.client.configured:
            return None
        if side == "buy":
            amount = int((notional * Decimal("1000000")).to_integral_value())
            return await self.jupiter.order_quote(
                input_mint="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                output_mint=mint,
                amount_base_units=amount,
                side="buy",
                observed_at=now,
                decimals_out=decimals,
            )
        amount = int((notional * (Decimal(10) ** decimals) / (price or Decimal("1"))).to_integral_value())
        if amount <= 0:
            amount = 1
        return await self.jupiter.order_quote(
            input_mint=mint,
            output_mint="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
            amount_base_units=amount,
            side="sell",
            observed_at=now,
            decimals_out=decimals,
        )

    async def _facts(self, mode: str, mint: str, now: datetime, provided: dict[str, AuthorityFacts] | None) -> AuthorityFacts | None:
        if mode == "demo":
            return (provided or demo_facts(now)).get(mint)
        try:
            inspection = await self.rpc.inspect_mint(mint, now)
            return inspection.facts
        except Exception as exc:
            log.warning("solana rpc mint inspection unavailable: %s", exc)
            return None

    def _books(self, session, mode: str, now: datetime, limits: RiskLimits) -> tuple[PortfolioState, Decimal, Decimal, int, bool]:
        state = self._ensure_state(session, mode, now)
        today = now.astimezone(timezone.utc).date().isoformat()
        positions = session.scalars(select(Position).where(Position.mode == mode, Position.status.in_(("open", "unsellable")))).all()
        exposure = Decimal("0")
        fresh = True
        for pos in positions:
            obs = session.scalar(
                select(MarketObservation)
                .where(MarketObservation.mode == mode, MarketObservation.mint == pos.mint)
                .order_by(desc(MarketObservation.observed_at))
            )
            mark = obs.price_usd if obs else None
            mark_fresh = bool(obs and mark is not None and (now - obs.observed_at).total_seconds() <= limits.market_max_age_seconds)
            value, is_fresh = position_mark_value(cost_basis=pos.cost_basis, qty=pos.qty, mark=mark, mark_fresh=mark_fresh)
            if not is_fresh:
                fresh = False
            exposure += value
        equity = q_usdc(state.cash + exposure)
        if state.day_utc != today:
            state.day_utc = today
            state.day_start_equity = equity
        if equity > state.peak_equity:
            state.peak_equity = equity
        state.updated_at = now
        return state, equity, q_usdc(exposure), len(positions), fresh

    async def run_cycle(
        self,
        now: datetime | None = None,
        observations: list[Observation] | None = None,
        facts: dict[str, AuthorityFacts] | None = None,
        exit_routes: dict[str, bool | None] | None = None,
    ) -> dict:
        now = now or utcnow()
        with session_scope() as session:
            mode = self._mode(session)
            config = self._config(session)
            limits = limits_from_config(config)
            started = now
            self._touch(session, "orchestrator", "cycle", "started", started, now)
        if observations is None:
            if mode == "demo":
                observations = demo_observations(now)
            else:
                observations = await self.discover_live(now)
        raw_rows = list(observations)
        candidates = dedupe_by_mint(raw_rows)
        max_candidates = int((config.get("scanner") or {}).get("max_candidates", 8))
        scored = sorted(candidates, key=lambda row: discovery_score(row, now), reverse=True)[:max_candidates]
        with session_scope() as session:
            for row in raw_rows:
                self._persist_observation(session, row, discovery_score(row, now))
                session.flush()
            if mode == "demo":
                self._seed_demo_chart(session, now)

        quotes_by_mint: dict[str, QuoteView | None] = {}
        reports: dict[str, tuple[MomentumReport, SecurityReport, QuoteView | None]] = {}
        for row in scored:
            correlation_id = str(uuid.uuid4())
            score = discovery_score(row, now)
            previous = None
            with session_scope() as session:
                previous = self._previous_observation(session, row.mode if row.mode else mode, row.mint, row.observed_at)
                self._emit(
                    session,
                    source="discovery",
                    event_type="discovery.candidate",
                    correlation_id=correlation_id,
                    mode=mode,
                    mint=row.mint,
                    now=now,
                    payload={
                        "symbol": row.symbol,
                        "score": score,
                        "pools": row.pools,
                        "promoted": row.promoted,
                        "provider": row.provider,
                        "data_label": row.provider,
                    },
                )
                self._touch(session, "discovery", f"scan {row.symbol}", f"score {score}", now, now)

            async def _momentum():
                if self.momentum_delay:
                    await asyncio.sleep(self.momentum_delay)
                return momentum_from_observations(row, previous, now)

            async def _security():
                route_map = exit_routes if exit_routes is not None else (demo_exit_routes() if mode == "demo" else {})
                known_route = route_map.get(row.mint)
                quote = None
                if mode == "demo":
                    quote = demo_quote(now, row.mint, "buy", row.price_usd or Decimal("1"))
                    sell = demo_quote(now, row.mint, "sell", row.price_usd or Decimal("1"))
                    if known_route is False:
                        sell = None
                    quotes_by_mint[row.mint] = quote if known_route is not False else None
                    exit_ok = known_route
                    _ = sell
                else:
                    quote = await self._quote(mode, row.mint, "buy", row.price_usd, Decimal("20"), row.decimals, now)
                    sell = await self._quote(mode, row.mint, "sell", row.price_usd, Decimal("1"), row.decimals, now)
                    quotes_by_mint[row.mint] = quote
                    exit_ok = bool(sell and not sell.fail) if sell is not None else (False if self.jupiter.client.configured else None)
                authority = await self._facts(mode, row.mint, now, facts)
                return (
                    classify_security(
                        authority,
                        liquidity_usd=row.pool_liquidity_usd,
                        min_liquidity=limits.min_pool_liquidity_usd,
                        exit_route=exit_ok,
                        observed_at=now,
                        max_age_seconds=limits.security_max_age_seconds,
                        allow_active_mint=bool(config["strategy"].get("allow_active_mint")),
                    ),
                    quote,
                )

            try:
                momentum, (security, quote) = await asyncio.wait_for(
                    _gather(_momentum(), _security()),
                    timeout=self.settings.agent_timeout_seconds,
                )
            except TimeoutError:
                with session_scope() as session:
                    self._emit(
                        session,
                        source="orchestrator",
                        event_type="orchestrator.hold",
                        correlation_id=correlation_id,
                        mode=mode,
                        mint=row.mint,
                        now=now,
                        payload={"reason": "agent_timeout"},
                    )
                    self._touch(session, "orchestrator", "timeout", "HOLD", now, now, error="agent timeout")
                continue

            reports[row.mint] = (momentum, security, quote)
            with session_scope() as session:
                self._emit(
                    session,
                    source="momentum",
                    event_type="momentum.report",
                    correlation_id=correlation_id,
                    mode=mode,
                    mint=row.mint,
                    now=now,
                    payload={
                        "score": momentum.score,
                        "direction": momentum.direction,
                        "completeness": momentum.completeness,
                        "confidence": momentum.confidence,
                        "reasons": momentum.reasons,
                        "indicators": momentum.indicators,
                        "source": "deterministic",
                    },
                )
                session.add(
                    SecurityAssessment(
                        mode=mode,
                        mint=row.mint,
                        observed_at=now,
                        classification=security.classification,
                        score=security.score,
                        critical_flags=security.critical_flags,
                        warning_flags=security.warning_flags,
                        evidence=security.evidence,
                        explanation=security.explanation,
                        correlation_id=correlation_id,
                    )
                )
                self._emit(
                    session,
                    source="security",
                    event_type="security.report",
                    correlation_id=correlation_id,
                    mode=mode,
                    mint=row.mint,
                    now=now,
                    payload={
                        "classification": security.classification,
                        "score": security.score,
                        "critical_flags": security.critical_flags,
                        "warning_flags": security.warning_flags,
                        "explanation": security.explanation,
                        "source": "deterministic",
                    },
                )
                fee_bps = quote.fee_bps if quote and not quote.fail else None
                state, equity, exposure, open_positions, _fresh = self._books(session, mode, now, limits)
                decision = propose_entry(
                    observation=row,
                    momentum=momentum,
                    security=security,
                    quote_ok=bool(quote and not quote.fail),
                    quote_fee_bps=fee_bps,
                    now=now,
                    equity=equity,
                    limits_max_trade=limits.max_new_trade_usd,
                    limits_position_pct=limits.max_position_pct,
                    min_liquidity=limits.min_pool_liquidity_usd,
                    min_pool_age=limits.min_pool_age_minutes,
                    momentum_threshold=int(config["strategy"]["momentum_threshold"]),
                    max_fee_bps=limits.max_fee_bps,
                    allow_promoted=bool(config["strategy"].get("allow_promoted")),
                )
                proposal_id = str(uuid.uuid4())
                pool_age = None
                if row.pool_created_at is not None:
                    pool_age = int((now - row.pool_created_at).total_seconds() // 60)
                session.add(
                    StrategyProposal(
                        id=proposal_id,
                        mode=mode,
                        mint=row.mint,
                        pool_address=row.pool_address,
                        side="buy",
                        requested_notional=decision.notional or Decimal("0"),
                        strategy_version=STRATEGY_VERSION,
                        thesis=decision.thesis,
                        evidence=decision.evidence,
                        expected_costs=decision.expected_costs,
                        exit_conditions=decision.exit_conditions,
                        created_at=now,
                        expires_at=now + timedelta(seconds=limits.quote_max_age_seconds),
                        status="proposed" if decision.action == "propose" else "hold",
                        correlation_id=correlation_id,
                    )
                )
                session.flush()
                self._emit(
                    session,
                    source="strategy",
                    event_type="strategy.proposal" if decision.action == "propose" else "strategy.hold",
                    correlation_id=correlation_id,
                    mode=mode,
                    mint=row.mint,
                    now=now,
                    payload={"action": decision.action, "thesis": decision.thesis, "notional": format(decision.notional, "f") if decision.notional else None, "source": "deterministic"},
                )
                commentary = None
                if decision.action != "propose":
                    self._touch(session, "strategy", row.symbol, "HOLD", now, now)
                    continue
                open_same = (
                    session.scalar(
                        select(func.count())
                        .select_from(Position)
                        .where(Position.mode == mode, Position.mint == row.mint, Position.status.in_(("open", "unsellable")))
                    )
                    or 0
                )
                pending_same = (
                    session.scalar(
                        select(func.count())
                        .select_from(PaperOrder)
                        .where(
                            PaperOrder.mode == mode,
                            PaperOrder.mint == row.mint,
                            PaperOrder.side == "buy",
                            PaperOrder.status.in_(("pending", "partial")),
                        )
                    )
                    or 0
                )
                duplicate = int(open_same) > 0 or int(pending_same) > 0
                last_order = session.scalar(
                    select(PaperOrder)
                    .where(PaperOrder.mode == mode, PaperOrder.mint == row.mint, PaperOrder.side == "buy")
                    .order_by(desc(PaperOrder.created_at))
                )
                provider_ok = self.gecko.circuit.state != "open" and self.dex.circuit.state != "open"
                risk = evaluate_entry(
                    now=now,
                    limits=limits,
                    portfolio=PortfolioView(
                        mode=mode,
                        cash=q_usdc(state.cash - state.reserved),
                        equity=equity,
                        peak_equity=state.peak_equity,
                        day_start_equity=state.day_start_equity,
                        open_positions=open_positions,
                        open_exposure=exposure,
                        emergency_stop=state.emergency_stop,
                    ),
                    proposal=ProposalView(
                        proposal_id=proposal_id,
                        mode=mode,
                        mint=row.mint,
                        side="buy",
                        requested_notional=decision.notional or Decimal("0"),
                        expires_at=now + timedelta(seconds=limits.quote_max_age_seconds),
                        pool_liquidity_usd=row.pool_liquidity_usd,
                        pool_age_minutes=pool_age,
                        market_observed_at=row.observed_at,
                    ),
                    quote=quote,
                    security=SecurityView(security.classification, now, security.critical_flags),
                    provider_ok=provider_ok if mode == "live" else True,
                    duplicate=duplicate,
                    last_order_at=last_order.created_at if last_order else None,
                )
                risk_id = str(uuid.uuid4())
                session.add(
                    RiskDecision(
                        id=risk_id,
                        proposal_id=proposal_id,
                        mode=mode,
                        mint=row.mint,
                        approved=risk.approved,
                        reason_codes=risk.reason_codes,
                        explanation=risk.explanation,
                        limits_snapshot=limits.snapshot(),
                        created_at=now,
                        correlation_id=correlation_id,
                    )
                )
                proposal = session.get(StrategyProposal, proposal_id)
                if proposal:
                    proposal.status = "approved" if risk.approved else "rejected"
                self._emit(
                    session,
                    source="risk",
                    event_type="risk.decision",
                    correlation_id=correlation_id,
                    mode=mode,
                    mint=row.mint,
                    now=now,
                    payload={"approved": risk.approved, "reason_codes": risk.reason_codes, "explanation": risk.explanation},
                )
                self._touch(session, "risk", row.symbol, "approve" if risk.approved else "veto", now, now)
                if not risk.approved or state.emergency_stop:
                    continue
                plan = plan_buy(
                    cash=q_usdc(state.cash - state.reserved),
                    requested=decision.notional or Decimal("0"),
                    quote=quote,
                    liquidity=row.pool_liquidity_usd,
                    limits=limits,
                    decimals=row.decimals,
                )
                order_id = str(uuid.uuid4())
                order = PaperOrder(
                    id=order_id,
                    client_order_id=proposal_id,
                    mode=mode,
                    mint=row.mint,
                    pool_address=row.pool_address,
                    side="buy",
                    requested_notional=decision.notional or Decimal("0"),
                    status=plan.status,
                    proposal_id=proposal_id,
                    risk_decision_id=risk_id,
                    quote={
                        "price_usd": format(quote.price_usd, "f") if quote else None,
                        "source": quote.source if quote else None,
                        "fee_source": plan.fee_source,
                    },
                    filled_notional=plan.filled_notional,
                    remaining_notional=plan.remaining_notional,
                    rejection_reason=plan.reason,
                    created_at=now,
                    updated_at=now,
                    correlation_id=correlation_id,
                )
                session.add(order)
                session.flush()
                if plan.fill is None:
                    self._emit(
                        session,
                        source="execution",
                        event_type="execution.reject",
                        correlation_id=correlation_id,
                        mode=mode,
                        mint=row.mint,
                        now=now,
                        payload={"status": plan.status, "reason": plan.reason},
                    )
                    continue
                fill = plan.fill
                state.cash = fill.cash_after
                state.updated_at = now
                position_id = str(uuid.uuid4())
                session.add(
                    Position(
                        id=position_id,
                        mode=mode,
                        mint=row.mint,
                        pool_address=row.pool_address,
                        status="open",
                        qty=fill.qty,
                        avg_entry_price=fill.price,
                        cost_basis=fill.spend,
                        realized_pnl=Decimal("0"),
                        fees=fill.fee,
                        opened_at=now,
                        strategy_version=STRATEGY_VERSION,
                        exit_conditions=decision.exit_conditions,
                        high_water_price=fill.price,
                    )
                )
                order.position_id = position_id
                session.add(
                    PaperFill(
                        id=str(uuid.uuid4()),
                        order_id=order_id,
                        mode=mode,
                        qty=fill.qty,
                        price=fill.price,
                        notional=fill.notional,
                        fee=fill.fee,
                        fee_source=plan.fee_source,
                        price_impact_bps=plan.price_impact_bps,
                        filled_at=now,
                    )
                )
                session.add(
                    LedgerEntry(
                        mode=mode,
                        created_at=now,
                        kind="buy",
                        amount_usdc=-fill.spend,
                        cash_after=state.cash,
                        ref=order_id,
                        memo=f"Paper buy {row.symbol} at quote. Fee source {plan.fee_source}.",
                    )
                )
                self._emit(
                    session,
                    source="execution",
                    event_type="execution.fill",
                    correlation_id=correlation_id,
                    mode=mode,
                    mint=row.mint,
                    now=now,
                    payload={
                        "status": plan.status,
                        "price": format(fill.price, "f"),
                        "qty": format(fill.qty, "f"),
                        "fee": format(fill.fee, "f"),
                        "notional": format(fill.notional, "f"),
                    },
                )
                self._touch(session, "execution", f"buy {row.symbol}", plan.status, now, now)
                _ = commentary

        await self._monitor_exits(now, mode, limits)
        with session_scope() as session:
            state, equity, exposure, _count, fresh = self._books(session, mode, now, limits)
            peak = state.peak_equity
            drawdown = (peak - equity) / peak if peak else Decimal("0")
            session.add(
                EquitySnapshot(
                    mode=mode,
                    created_at=now,
                    equity=equity,
                    cash=state.cash,
                    exposure=exposure,
                    drawdown=drawdown,
                    marks_fresh=fresh,
                )
            )
            for snap in (
                self.gecko.snapshot(),
                self.dex.snapshot(),
                self.jupiter.client.snapshot(),
                self.rpc.client.snapshot(),
                self.grok.client.snapshot(),
            ):
                if mode == "demo":
                    snap.status = "not_used"
                    snap.detail = "Demo mode does not call this provider."
                self._save_provider(session, snap)
            if state.emergency_stop:
                self._alert(session, mode, "warning", "EMERGENCY_STOP", "New entries are paused. Open paper positions were not liquidated.", now, None)
            self._touch(session, "monitoring", "equity snapshot", format(equity, "f"), now, now)
            if not self.grok.available:
                self._emit(
                    session,
                    source="grok",
                    event_type="commentary.unavailable",
                    correlation_id="ai-status",
                    mode=mode,
                    mint=None,
                    now=now,
                    payload={"available": False, "source": "unavailable", "note": "Deterministic agent text is not Grok output."},
                )
            self._touch(session, "orchestrator", "cycle", "complete", now, now)
        return {"mode": mode, "candidates": len(scored)}

    async def _monitor_exits(self, now: datetime, mode: str, limits: RiskLimits) -> None:
        with session_scope() as session:
            positions = session.scalars(select(Position).where(Position.mode == mode, Position.status == "open")).all()
            work = []
            for pos in positions:
                obs = session.scalar(
                    select(MarketObservation)
                    .where(MarketObservation.mode == mode, MarketObservation.mint == pos.mint)
                    .order_by(desc(MarketObservation.observed_at))
                )
                sec = session.scalar(
                    select(SecurityAssessment)
                    .where(SecurityAssessment.mode == mode, SecurityAssessment.mint == pos.mint)
                    .order_by(desc(SecurityAssessment.observed_at))
                )
                work.append((pos.id, pos.mint, pos.qty, pos.cost_basis, pos.avg_entry_price, pos.opened_at, pos.high_water_price, pos.exit_conditions, pos.pool_address, obs, sec))
        for item in work:
            pos_id, mint, qty, cost, entry, opened, high, conditions, pool, obs, sec = item
            market = obs.price_usd if obs else None
            fresh = bool(obs and (now - obs.observed_at).total_seconds() <= limits.market_max_age_seconds)
            if market is not None and fresh and (high is None or market > high):
                with session_scope() as session:
                    current = session.get(Position, pos_id)
                    if current and current.status == "open":
                        current.high_water_price = market
                        high = market
            security_failed = bool(sec and sec.classification == "fail")
            quote = None
            if mode == "demo" and market is not None:
                quote = demo_quote(now, mint, "sell", market, cost)
            elif mode == "live":
                quote = await self._quote(mode, mint, "sell", market, cost, 6, now)
            signal = exit_reason(
                entry_price=entry,
                market_price=market,
                market_fresh=fresh,
                quote_price=quote.price_usd if quote and not quote.fail else None,
                liquidity_now=obs.pool_liquidity_usd if obs else None,
                liquidity_entry=None,
                opened_at=opened,
                now=now,
                high_water=high,
                conditions=conditions or {},
                security_failed=security_failed,
            )
            wants_exit = signal is not None
            if wants_exit and (quote is None or quote.fail):
                with session_scope() as session:
                    current = session.get(Position, pos_id)
                    if current and current.status == "open":
                        current.status = "unsellable"
                        current.unsellable_reason = "Exit quote unavailable. Position was not marked closed at the last displayed price."
                        self._alert(session, mode, "critical", "UNSELLABLE", current.unsellable_reason, now, mint)
                        self._emit(
                            session,
                            source="monitoring",
                            event_type="monitoring.unsellable",
                            correlation_id=str(uuid.uuid4()),
                            mode=mode,
                            mint=mint,
                            now=now,
                            payload={"reason": signal},
                        )
                continue
            if not wants_exit or quote is None:
                if market is None:
                    with session_scope() as session:
                        self._alert(session, mode, "warning", "STALE_MARK", "Mark price unavailable. Unrealized P&L is withheld.", now, mint)
                continue
            plan = plan_sell(cash=Decimal("0"), qty=qty, cost=cost, quote=quote)
            with session_scope() as session:
                state = session.get(PortfolioState, mode)
                current = session.get(Position, pos_id)
                if state is None or current is None or current.status != "open" or plan.fill is None:
                    continue
                # plan_sell used cash 0; apply proceeds onto the real cash balance.
                state.cash = q_usdc(state.cash + plan.fill.proceeds)
                current.qty = Decimal("0")
                current.cost_basis = Decimal("0")
                current.realized_pnl = plan.fill.realized_pnl
                current.fees = q_usdc(current.fees + plan.fill.fee)
                current.status = "closed"
                current.closed_at = now
                current.exit_reason = signal
                order_id = str(uuid.uuid4())
                session.add(
                    PaperOrder(
                        id=order_id,
                        client_order_id=f"exit-{pos_id}",
                        mode=mode,
                        mint=mint,
                        pool_address=pool,
                        side="sell",
                        requested_notional=plan.fill.proceeds,
                        status="filled",
                        quote={"price_usd": format(plan.fill.price, "f"), "source": quote.source},
                        filled_notional=plan.fill.proceeds,
                        remaining_notional=Decimal("0"),
                        created_at=now,
                        updated_at=now,
                        correlation_id=pos_id,
                        position_id=pos_id,
                    )
                )
                session.flush()
                session.add(
                    PaperFill(
                        id=str(uuid.uuid4()),
                        order_id=order_id,
                        mode=mode,
                        qty=plan.fill.qty_sold,
                        price=plan.fill.price,
                        notional=plan.fill.proceeds,
                        fee=plan.fill.fee,
                        fee_source=plan.fee_source,
                        price_impact_bps=plan.price_impact_bps,
                        filled_at=now,
                    )
                )
                session.add(
                    LedgerEntry(
                        mode=mode,
                        created_at=now,
                        kind="sell",
                        amount_usdc=plan.fill.proceeds,
                        cash_after=state.cash,
                        ref=order_id,
                        memo=f"Paper sell ({signal}) at executable quote.",
                    )
                )
                state.updated_at = now

    def _alert(self, session, mode: str, severity: str, code: str, message: str, now: datetime, mint: str | None) -> None:
        recent = session.scalar(
            select(Alert).where(Alert.mode == mode, Alert.code == code, Alert.mint == mint, Alert.acknowledged == False)  # noqa: E712
        )
        if recent:
            return
        session.add(
            Alert(
                id=str(uuid.uuid4()),
                created_at=now,
                severity=severity,
                code=code,
                message=message,
                mint=mint,
                mode=mode,
                acknowledged=False,
            )
        )

    def emergency_stop(self, reason: str, actor: str) -> dict:
        now = utcnow()
        with session_scope() as session:
            mode = self._mode(session)
            state = self._ensure_state(session, mode, now)
            state.emergency_stop = True
            state.emergency_stop_reason = reason
            state.updated_at = now
            cancelled = 0
            pending = session.scalars(
                select(PaperOrder).where(PaperOrder.mode == mode, PaperOrder.side == "buy", PaperOrder.status == "pending")
            ).all()
            for order in pending:
                order.status = "cancelled"
                order.rejection_reason = "EMERGENCY_STOP"
                order.updated_at = now
                cancelled += 1
            state.reserved = Decimal("0")
            self._alert(session, mode, "critical", "EMERGENCY_STOP", "Entries paused. Open positions were not liquidated.", now, None)
            self._emit(
                session,
                source="risk",
                event_type="risk.emergency_stop",
                correlation_id=str(uuid.uuid4()),
                mode=mode,
                mint=None,
                now=now,
                payload={"actor": actor, "cancelled_entries": cancelled, "positions_liquidated": False},
            )
            open_positions = session.scalar(
                select(func.count()).select_from(Position).where(Position.mode == mode, Position.status == "open")
            )
            return {
                "emergency_stop": True,
                "cancelled_entries": cancelled,
                "open_positions": int(open_positions or 0),
                "positions_liquidated": False,
            }

    def resume_entries(self, actor: str) -> dict:
        now = utcnow()
        with session_scope() as session:
            mode = self._mode(session)
            state = self._ensure_state(session, mode, now)
            state.emergency_stop = False
            state.emergency_stop_reason = None
            state.updated_at = now
            self._emit(
                session,
                source="risk",
                event_type="risk.resume",
                correlation_id=str(uuid.uuid4()),
                mode=mode,
                mint=None,
                now=now,
                payload={"actor": actor, "positions_changed": False},
            )
            return {"emergency_stop": False}

    def update_settings(self, patch: dict, actor: str) -> dict:
        now = utcnow()
        with session_scope() as session:
            current = dict(self._config(session))
            for section in ("scanner", "strategy", "risk", "simulation", "ai"):
                if section in patch and isinstance(patch[section], dict):
                    merged = dict(current.get(section) or {})
                    merged.update(patch[section])
                    current[section] = merged
            if "mode" in patch:
                if patch["mode"] not in {"demo", "live"}:
                    raise ValueError("mode must be demo or live")
                current["mode"] = patch["mode"]
            # Re-validate risk by constructing limits. Rejects bad decimals.
            limits_from_config(current)
            row = session.get(AppSetting, "runtime")
            row.value = current
            row.updated_at = now
            session.add(ConfigVersion(created_at=now, actor=actor, document=current, note="settings update"))
            return current


async def _gather(left, right):
    return await asyncio.gather(left, right)
