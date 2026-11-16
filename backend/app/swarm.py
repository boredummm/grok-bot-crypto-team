"""Orchestrator + 8-seat GMGN/JEV funnel. Discovery never reaches an order without the risk engine."""

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
from app.funnel import (
    DEFAULT_THRESHOLDS,
    bench_ttl_minutes,
    chain_kill,
    compute_size_ticket,
    free_kill,
    soft_kill,
    trade_kill,
)
from app.grok_client import GrokClient
from app.jev import JevClient
from app.models import (
    AgentEvent,
    AgentHealth,
    Alert,
    AppSetting,
    BenchEntry,
    ConfigVersion,
    EquitySnapshot,
    FunnelRun,
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
    ShadowDecision,
    StrategyProposal,
    Token,
)
from app.money import USDC_MINT, q_usdc
from app.providers.gmgn import GmgnAdapter, GmgnDossier, clean_x_handle, compare_provider_evidence
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
    "scanner": {
        "interval_seconds": 60,
        "max_candidates": 8,
        "min_discovery_score": 35,
        "one_position_at_a_time": False,
        "shadow_mode": False,
        "use_bench": False,
    },
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
    "thresholds": {
        "hard": dict(DEFAULT_THRESHOLDS["hard"]),
        "soft": dict(DEFAULT_THRESHOLDS["soft"]),
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
        gmgn: GmgnAdapter | None = None,
        jupiter: JupiterAdapter | None = None,
        rpc: SolanaRpc | None = None,
        jev: JevClient | None = None,
        grok: GrokClient | None = None,
    ) -> None:
        self.settings = settings
        self.gecko = gecko or ProviderClient("geckoterminal", per_minute=8)
        self.dex = dex or ProviderClient("dexscreener", per_minute=60)
        self.gmgn = gmgn or GmgnAdapter(
            ProviderClient("gmgn", per_minute=20),
            settings.gmgn_api_key,
            settings.gmgn_base_url,
        )
        self.jupiter = jupiter or JupiterAdapter(
            ProviderClient("jupiter", per_minute=30),
            settings.jupiter_api_key,
            settings.jupiter_base,
        )
        self.rpc = rpc or SolanaRpc(ProviderClient("solana_rpc", per_minute=20), settings.solana_rpc_url)
        self.jev = jev or JevClient(
            ProviderClient("jev", per_minute=30),
            settings.typesafe_api_key,
            settings.typesafe_model,
            settings.typesafe_base_url,
        )
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
        await self.gmgn.client.aclose()
        await self.jupiter.client.aclose()
        await self.rpc.client.aclose()
        await self.jev.client.aclose()
        await self.grok.client.aclose()

    def bootstrap(self) -> None:
        from app.db import Base, get_engine

        Base.metadata.create_all(get_engine())
        with session_scope() as session:
            from app.models import SchemaMigration

            if session.get(SchemaMigration, 1) is None:
                session.add(SchemaMigration(version=1, applied_at=utcnow()))
            if session.get(SchemaMigration, 2) is None:
                session.add(SchemaMigration(version=2, applied_at=utcnow()))
            runtime = session.get(AppSetting, "runtime")
            if runtime is None:
                config = dict(DEFAULT_CONFIG)
                config["mode"] = self.settings.mode
                config["ai"]["hourly_budget"] = self.settings.ai_hourly_budget
                session.add(AppSetting(key="runtime", value=config, updated_at=utcnow()))
                session.add(
                    ConfigVersion(created_at=utcnow(), actor="system", document=config, note="initial configuration")
                )
            else:
                merged = dict(DEFAULT_CONFIG)
                for key, val in (runtime.value or {}).items():
                    if isinstance(val, dict) and isinstance(merged.get(key), dict):
                        merged[key] = {**merged[key], **val}
                    else:
                        merged[key] = val
                if "thresholds" not in merged:
                    merged["thresholds"] = {
                        "hard": dict(DEFAULT_THRESHOLDS["hard"]),
                        "soft": dict(DEFAULT_THRESHOLDS["soft"]),
                    }
                runtime.value = merged
                runtime.updated_at = utcnow()
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

    def _thresholds(self, config: dict) -> tuple[dict, dict]:
        th = config.get("thresholds") or {}
        hard = {**DEFAULT_THRESHOLDS["hard"], **(th.get("hard") or {})}
        # Keep min_liquidity_usd synced with risk.min_pool_liquidity_usd when risk overrides it.
        risk_cfg = config.get("risk") or {}
        if "min_pool_liquidity_usd" in risk_cfg:
            hard["min_liquidity_usd"] = float(risk_cfg["min_pool_liquidity_usd"])
        if "min_pool_age_minutes" in risk_cfg:
            hard["min_age_minutes"] = int(risk_cfg["min_pool_age_minutes"])
        soft = {**DEFAULT_THRESHOLDS["soft"], **(th.get("soft") or {})}
        return hard, soft

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

    def _is_benched(self, session, mode: str, mint: str, now: datetime) -> BenchEntry | None:
        row = session.scalar(select(BenchEntry).where(BenchEntry.mode == mode, BenchEntry.mint == mint))
        if row is not None and row.until_at > now:
            return row
        return None

    def _sit_bench(
        self,
        session,
        *,
        mode: str,
        mint: str,
        symbol: str,
        reason: str,
        stage: str,
        now: datetime,
        details: dict | None = None,
    ) -> BenchEntry:
        ttl = bench_ttl_minutes(reason)
        until = now + timedelta(minutes=ttl)
        existing = session.scalar(select(BenchEntry).where(BenchEntry.mode == mode, BenchEntry.mint == mint))
        if existing is None:
            existing = BenchEntry(
                mode=mode,
                mint=mint,
                symbol=symbol[:32],
                reason=reason[:64],
                stage=stage[:32],
                ttl_minutes=ttl,
                benched_at=now,
                until_at=until,
                details=details,
            )
            session.add(existing)
        else:
            existing.symbol = symbol[:32]
            existing.reason = reason[:64]
            existing.stage = stage[:32]
            existing.ttl_minutes = ttl
            existing.benched_at = now
            existing.until_at = until
            existing.details = details
        return existing

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

    def _touch(
        self,
        session,
        agent: str,
        task: str,
        decision: str,
        started: datetime,
        now: datetime,
        error: str | None = None,
    ) -> None:
        row = session.get(AgentHealth, agent)
        if row is None:
            return
        row.status = "error" if error else "ok"
        row.last_heartbeat = now
        row.last_task = task[:160]
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

    def _persist_observation(self, session, row: Observation, score: int, provenance_extra: dict | None = None) -> None:
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
        prov = {"provider": row.provider, "promoted_listing": row.promoted}
        if provenance_extra:
            prov.update(provenance_extra)
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
                provenance=prov,
            )
        )

    def _seed_demo_chart(self, session, now: datetime) -> None:
        """Prior demo prints so the chart is not a single dot. Labeled as fixtures."""
        existing = session.scalar(
            select(func.count())
            .select_from(MarketObservation)
            .where(MarketObservation.mode == "demo", MarketObservation.mint == "demo:momentum")
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
        if self.gmgn.configured:
            try:
                rows.extend(await self.gmgn.trending_sol(observed_at=now, mode="live", interval="5m", limit=20))
            except ProviderError as exc:
                log.warning("gmgn trending unavailable: %s", exc.message)
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

    async def _quote(
        self,
        mode: str,
        mint: str,
        side: str,
        price: Decimal | None,
        notional: Decimal,
        decimals: int,
        now: datetime,
    ) -> QuoteView | None:
        if mode == "demo":
            if price is None:
                return None
            return demo_quote(now, mint, side, price, notional)
        # Prefer GMGN paper quote when GMGN_API_KEY is configured, fallback to Jupiter Swap v2 quote.
        if side == "buy":
            amount = int((notional * Decimal("1000000")).to_integral_value())
            input_mint, output_mint = USDC_MINT, mint
        else:
            amount = int((notional * (Decimal(10) ** decimals) / (price or Decimal("1"))).to_integral_value())
            if amount <= 0:
                amount = 1
            input_mint, output_mint = mint, USDC_MINT

        if self.gmgn.configured:
            try:
                gmgn_q = await self.gmgn.paper_quote(
                    input_mint=input_mint,
                    output_mint=output_mint,
                    amount_base_units=amount,
                    side=side,
                    observed_at=now,
                    decimals_out=decimals,
                )
                if gmgn_q is not None:
                    return gmgn_q
            except ProviderError as exc:
                log.warning("gmgn paper quote unavailable: %s", exc.message)

        if not self.jupiter.client.configured:
            return None
        return await self.jupiter.order_quote(
            input_mint=input_mint,
            output_mint=output_mint,
            amount_base_units=amount,
            side=side,
            observed_at=now,
            decimals_out=decimals,
        )

    async def _facts_and_dossier(
        self,
        mode: str,
        row: Observation,
        now: datetime,
        provided: dict[str, AuthorityFacts] | None,
    ) -> tuple[AuthorityFacts | None, GmgnDossier | None, str | None]:
        if mode == "demo":
            demo_map = provided or demo_facts(now)
            handle = "demomom_sol" if row.mint == "demo:momentum" else None
            return demo_map.get(row.mint), None, handle

        dossier: GmgnDossier | None = None
        x_handle: str | None = None
        if self.gmgn.configured:
            try:
                dossier = await self.gmgn.token_dossier(row.mint, mode=mode, observed_at=now)
                x_handle = dossier.x_handle
            except ProviderError as exc:
                log.warning("gmgn dossier unavailable for %s: %s", row.mint, exc.message)

        if x_handle is None and mode == "live":
            # Try GeckoTerminal token info for verified on-chain twitter_handle if available.
            try:
                gt_info = await self.gecko.get_json(
                    f"{self.settings.gecko_base}/networks/solana/tokens/{row.mint}/info",
                    headers={"Accept": "application/json;version=20230203"},
                )
                attr = ((gt_info or {}).get("data") or {}).get("attributes") or {}
                x_handle = clean_x_handle(attr.get("twitter_handle"))
            except ProviderError:
                pass

        try:
            inspection = await self.rpc.inspect_mint(row.mint, now)
            rpc_facts = inspection.facts
            if dossier and dossier.facts:
                if rpc_facts.top_holder_pct is None and dossier.top_10_percent is not None:
                    rpc_facts.top_holder_pct = dossier.top_10_percent
                if dossier.facts.malicious_flag is True:
                    rpc_facts.malicious_flag = True
            return rpc_facts, dossier, x_handle
        except Exception as exc:
            log.warning("solana rpc mint inspection unavailable: %s", exc)
            if dossier and dossier.facts:
                return dossier.facts, dossier, x_handle
            return None, dossier, x_handle

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
        cycle_id = str(uuid.uuid4())
        with session_scope() as session:
            mode = self._mode(session)
            config = self._config(session)
            limits = limits_from_config(config)
            hard, soft = self._thresholds(config)
            scanner_cfg = config.get("scanner") or {}
            one_at_a_time = bool(scanner_cfg.get("one_position_at_a_time", False))
            shadow_mode = bool(scanner_cfg.get("shadow_mode", False))
            use_bench = bool(scanner_cfg.get("use_bench", False))
            started = now
            self._touch(session, "orchestrator", "cycle", "started", started, now)
            held_pos = session.scalar(
                select(Position).where(Position.mode == mode, Position.status.in_(("open", "unsellable"))).order_by(desc(Position.opened_at))
            )
            held_symbol = None
            if held_pos:
                tok = session.scalar(select(Token).where(Token.mode == mode, Token.mint == held_pos.mint))
                held_symbol = tok.symbol if tok else held_pos.mint

        # Optional strict guide rule: when `one_position_at_a_time` is enabled and a position is held,
        # skip scanning and run RISK exit monitoring only.
        if one_at_a_time and held_symbol and observations is None:
            await self._monitor_exits(now, mode, limits)
            with session_scope() as session:
                self._emit(
                    session,
                    source="orchestrator",
                    event_type="desk.held_skip_scan",
                    correlation_id=cycle_id,
                    mode=mode,
                    mint=None,
                    now=now,
                    payload={"seat": "RISK", "phase": "held", "symbol": held_symbol, "reason": "one_position_at_a_time"},
                )
                session.add(
                    FunnelRun(
                        id=cycle_id,
                        mode=mode,
                        created_at=now,
                        held_symbol=held_symbol,
                        shadow_mode=shadow_mode,
                        jev_connected=self.jev.available,
                        jev_model=self.jev.last_resolved_model,
                        gmgn_configured=self.gmgn.configured,
                        stats={"seen": 0, "benched": 0, "held": held_symbol, "free": {}, "trade": {}, "chain": {}, "soft": {}, "picked": None},
                        thresholds_snapshot={"hard": hard, "soft": soft},
                    )
                )
            return {"mode": mode, "candidates": 0, "held": held_symbol}

        if observations is None:
            if mode == "demo":
                observations = demo_observations(now)
            else:
                observations = await self.discover_live(now)
        raw_rows = list(observations)
        by_mint_provider: dict[tuple[str, str], Observation] = {(r.mint, r.provider): r for r in raw_rows}
        candidates = dedupe_by_mint(raw_rows)
        max_candidates = int((config.get("scanner") or {}).get("max_candidates", 8))
        scored = sorted(candidates, key=lambda row: discovery_score(row, now), reverse=True)[:max_candidates]

        funnel_stats: dict = {
            "seen": len(candidates),
            "evaluated": len(scored),
            "benched": 0,
            "free": {},
            "trade": {},
            "chain": {},
            "soft": {},
            "picked": None,
            "pages_read": len(raw_rows),
            "rugs_killed": 0,
            "webs_spun": 0,
        }

        with session_scope() as session:
            for row in raw_rows:
                gmgn_obs = by_mint_provider.get((row.mint, "gmgn"))
                sec_obs = by_mint_provider.get((row.mint, "dexscreener")) or by_mint_provider.get((row.mint, "geckoterminal"))
                cmp_extra = None
                if gmgn_obs and sec_obs and row.provider == "gmgn":
                    cmp = compare_provider_evidence(gmgn_obs, sec_obs, now=now, max_age_seconds=limits.market_max_age_seconds)
                    cmp_extra = {"comparison": cmp.__dict__}
                self._persist_observation(session, row, discovery_score(row, now), provenance_extra=cmp_extra)
                session.flush()
            if mode == "demo":
                self._seed_demo_chart(session, now)
            self._emit(
                session,
                source="discovery",
                event_type="crawler.sweep",
                correlation_id=cycle_id,
                mode=mode,
                mint=scored[0].mint if scored else None,
                now=now,
                payload={
                    "seat": "CRAWLER",
                    "phase": "crawl",
                    "raw_pools": len(raw_rows),
                    "deduped": len(candidates),
                    "gmgn_active": bool(self.gmgn.configured and mode == "live"),
                    "source": "demo_fixture" if mode == "demo" else ("gmgn+geckoterminal+dexscreener" if self.gmgn.configured else "geckoterminal+dexscreener"),
                },
            )

        quotes_by_mint: dict[str, QuoteView | None] = {}
        jev_survivors: list[tuple[Observation, dict, str]] = []

        for row in scored:
            correlation_id = str(uuid.uuid4())
            score = discovery_score(row, now)
            with session_scope() as session:
                benched = self._is_benched(session, mode, row.mint, now) if use_bench else None
                if benched is not None:
                    funnel_stats["benched"] += 1
                    self._emit(
                        session,
                        source="discovery",
                        event_type="funnel.benched_skip",
                        correlation_id=correlation_id,
                        mode=mode,
                        mint=row.mint,
                        now=now,
                        payload={
                            "seat": "SCAN",
                            "phase": "bench",
                            "symbol": row.symbol,
                            "reason": benched.reason,
                            "until": benched.until_at.isoformat(),
                        },
                    )
                    continue
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
                        "seat": "SCAN",
                        "phase": "universe",
                        "symbol": row.symbol,
                        "score": score,
                        "pools": row.pools,
                        "promoted": row.promoted,
                        "provider": row.provider,
                        "data_label": row.provider,
                    },
                )
                self._touch(session, "discovery", f"scan {row.symbol}", f"score {score}", now, now)

            # Record deterministic funnel stage classifications (`free_kill`, `trade_kill`) for `thresholds.py` telemetry.
            fk = free_kill(row, now, hard)
            tk = None if fk else trade_kill(row, hard)
            if fk:
                funnel_stats["free"][fk] = funnel_stats["free"].get(fk, 0) + 1
            elif tk:
                funnel_stats["trade"][tk] = funnel_stats["trade"].get(tk, 0) + 1

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
                    has_quote_provider = self.gmgn.configured or self.jupiter.client.configured
                    exit_ok = bool(sell and not sell.fail) if sell is not None else (False if has_quote_provider else None)
                authority, dossier, x_handle = await self._facts_and_dossier(mode, row, now, facts)
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
                    authority,
                    dossier,
                    x_handle,
                )

            t_anal_start = utcnow()
            try:
                momentum, (security, quote, authority, dossier, x_handle) = await asyncio.wait_for(
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
                        payload={"seat": "CHIEF", "phase": "timeout", "reason": "agent_timeout"},
                    )
                    self._touch(session, "orchestrator", "timeout", "HOLD", now, now, error="agent timeout")
                continue
            t_anal_done = utcnow()

            funnel_stats["webs_spun"] += 1
            ck = None
            if not fk and not tk:
                ck = chain_kill(
                    row,
                    authority,
                    hard,
                    top_10_pct=dossier.top_10_percent if dossier else None,
                    allow_active_mint=bool(config["strategy"].get("allow_active_mint")),
                )
                if ck:
                    funnel_stats["chain"][ck] = funnel_stats["chain"].get(ck, 0) + 1
            if security.classification == "fail":
                funnel_stats["rugs_killed"] += 1

            # Optional JEV System One typed judgements (never faked when TYPESAFE_API_KEY is absent).
            jev_answers: dict = {}
            jev_model_used: str | None = None
            sk: str | None = None
            if self.jev.available and mode == "live" and not (fk or tk or ck):
                try:
                    market_state = {
                        "ticker": row.symbol,
                        "age_minutes": round((now - row.pool_created_at).total_seconds() / 60, 1) if row.pool_created_at else None,
                        "holder_count": row.holder_count,
                        "liquidity_usd": float(row.pool_liquidity_usd) if row.pool_liquidity_usd is not None else None,
                        "mcap_usd": float(row.market_cap_usd) if row.market_cap_usd is not None else None,
                        "volume_m5": float(row.volume_m5) if row.volume_m5 is not None else None,
                        "volume_h1": float(row.volume_h1) if row.volume_h1 is not None else None,
                        "buys_m5": row.buys_m5,
                        "sells_m5": row.sells_m5,
                        "change": {
                            "5m": float(row.price_change_m5) if row.price_change_m5 is not None else None,
                            "1h": float(row.price_change_h1) if row.price_change_h1 is not None else None,
                        },
                        "intended_ticket_usd": float(limits.max_new_trade_usd),
                    }
                    m_rec = await self.jev.ask("market", market_state)
                    if m_rec:
                        jev_answers.update(m_rec.answers)
                        jev_model_used = m_rec.resolved_model
                    c_state = {
                        "mint_authority_active": authority.mint_authority_active if authority else None,
                        "freeze_authority_active": authority.freeze_authority_active if authority else None,
                        "top_wallet_percent": float(authority.top_holder_pct) if authority and authority.top_holder_pct is not None else None,
                        "top_10_percent": float(dossier.top_10_percent) if dossier and dossier.top_10_percent is not None else None,
                        "developer_holding_percentage": float(dossier.dev_hold_percent) if dossier and dossier.dev_hold_percent is not None else None,
                        "holder_count": row.holder_count,
                    }
                    c_rec = await self.jev.ask("solana", c_state)
                    if c_rec:
                        jev_answers.update(c_rec.answers)
                        jev_model_used = c_rec.resolved_model
                    sk = soft_kill(jev_answers, soft)
                    if sk:
                        funnel_stats["soft"][sk] = funnel_stats["soft"].get(sk, 0) + 1
                    else:
                        jev_survivors.append((row, jev_answers, correlation_id))
                except ProviderError as exc:
                    log.warning("jev judgement failed for %s: %s", row.symbol, exc.message)

            with session_scope() as session:
                # Record bench entry for rejected tokens so bench view and optional skip logic stay accurate.
                bench_reason = fk or tk or ck or sk or (security.critical_flags[0] if security.classification == "fail" and security.critical_flags else None)
                bench_stage = "free_kill" if fk else ("trade_kill" if tk else ("chain_kill" if ck else ("soft_kill" if sk else "security")))
                if bench_reason:
                    self._sit_bench(
                        session,
                        mode=mode,
                        mint=row.mint,
                        symbol=row.symbol,
                        reason=bench_reason,
                        stage=bench_stage,
                        now=now,
                        details={"symbol": row.symbol, "flags": security.critical_flags},
                    )

                self._touch(
                    session,
                    "momentum",
                    f"analyze {row.symbol}",
                    f"{momentum.direction} ({momentum.score})",
                    t_anal_start,
                    t_anal_done,
                )
                self._touch(
                    session,
                    "security",
                    f"vet {row.symbol}",
                    f"{security.classification} ({security.score})",
                    t_anal_start,
                    t_anal_done,
                )
                self._emit(
                    session,
                    source="momentum",
                    event_type="momentum.report",
                    correlation_id=correlation_id,
                    mode=mode,
                    mint=row.mint,
                    now=now,
                    payload={
                        "seat": "SCAN",
                        "phase": "trade_kill" if tk else ("free_kill" if fk else "momentum"),
                        "symbol": row.symbol,
                        "score": momentum.score,
                        "direction": momentum.direction,
                        "completeness": momentum.completeness,
                        "confidence": momentum.confidence,
                        "reasons": momentum.reasons,
                        "indicators": momentum.indicators,
                        "free_kill": fk,
                        "trade_kill": tk,
                        "jev_shape": jev_answers.get("shape"),
                        "jev_model": jev_model_used,
                        "source": "jev+deterministic" if jev_model_used else "deterministic",
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
                        "seat": "VET",
                        "phase": "chain_kill",
                        "symbol": row.symbol,
                        "classification": security.classification,
                        "score": security.score,
                        "critical_flags": security.critical_flags,
                        "warning_flags": security.warning_flags,
                        "chain_kill": ck,
                        "top_holder_pct": format(authority.top_holder_pct, "f") if authority and authority.top_holder_pct is not None else None,
                        "explanation": security.explanation,
                        "jev_authority": jev_answers.get("authority_risk"),
                        "jev_concentration": jev_answers.get("concentration_is_exit_risk"),
                        "jev_dev_loaded": jev_answers.get("dev_still_loaded"),
                        "jev_model": jev_model_used,
                        "source": "jev+deterministic" if jev_model_used else "deterministic",
                    },
                )
                # SOCIAL seat event: strictly reports verified on-chain X handle or honest missing/unconfigured state.
                self._emit(
                    session,
                    source="discovery",
                    event_type="social.report",
                    correlation_id=correlation_id,
                    mode=mode,
                    mint=row.mint,
                    now=now,
                    payload={
                        "seat": "SOCIAL",
                        "phase": "social",
                        "symbol": row.symbol,
                        "x_handle": x_handle,
                        "status": "handle_verified_plugin_unconfigured" if x_handle else "no_verified_handle",
                        "note": (
                            f"On-chain X handle @{x_handle} verified; live X feed plugin not connected."
                            if x_handle
                            else "No verified on-chain X handle. Fan-account search is disabled."
                        ),
                        "size_factor": float(soft.get("no_social_cut", 0.60)) if not x_handle else 1.0,
                        "source": "onchain_metadata",
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

                # If JEV soft_kill rejected a token that deterministic rules would have proposed, record disagreement & hold.
                det_outcome = decision.action
                funnel_outcome = "kill" if (fk or tk or ck or sk) else decision.action
                session.add(
                    ShadowDecision(
                        id=str(uuid.uuid4()),
                        mode=mode,
                        cycle_id=cycle_id,
                        created_at=now,
                        mint=row.mint,
                        symbol=row.symbol,
                        funnel_stage=bench_stage if bench_reason else "pick",
                        funnel_outcome=funnel_outcome,
                        deterministic_outcome=det_outcome,
                        disagreement=(funnel_outcome != det_outcome),
                        jev_model=jev_model_used,
                        jev_answers=jev_answers or None,
                        reasons=[r for r in (fk, tk, ck, sk) if r] or (decision.evidence.get("failures") or []),
                        paper_notional=decision.notional,
                    )
                )

                if sk and decision.action == "propose":
                    decision.action = "hold"
                    decision.thesis = f"JEV soft gate ({sk}) withheld entry before RiskEngine."
                    decision.notional = None

                size_info = compute_size_ticket(
                    equity=equity,
                    free_cash=q_usdc(state.cash - state.reserved),
                    open_exposure=exposure,
                    max_position_pct=limits.max_position_pct,
                    max_exposure_pct=limits.max_exposure_pct,
                    max_trade_usd=limits.max_new_trade_usd,
                    pool_liquidity_usd=row.pool_liquidity_usd,
                    size_factor=Decimal("1.0"),
                    expected_fee_usd=quote.fee_usd if quote else Decimal("0.06"),
                    max_fee_bps=limits.max_fee_bps,
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
                        evidence={**decision.evidence, "jev_model": jev_model_used, "size_ticket": format(size_info.ticket_usd, "f")},
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
                    payload={
                        "seat": "CHIEF",
                        "phase": "pick" if decision.action == "propose" else "hold",
                        "symbol": row.symbol,
                        "action": decision.action,
                        "thesis": decision.thesis,
                        "notional": format(decision.notional, "f") if decision.notional else None,
                        "size_pct_bank": format(size_info.pct_of_bank, "f"),
                        "size_reason": size_info.reason,
                        "jev_connected": self.jev.available,
                        "jev_model": jev_model_used,
                        "source": "jev+deterministic" if jev_model_used else "deterministic",
                    },
                )
                if decision.action != "propose":
                    self._touch(session, "strategy", row.symbol, "HOLD", now, now)
                    continue

                funnel_stats["picked"] = row.symbol
                self._touch(session, "strategy", row.symbol, f"PICK {row.symbol}", now, now)
                self._emit(
                    session,
                    source="strategy",
                    event_type="size.ticket",
                    correlation_id=correlation_id,
                    mode=mode,
                    mint=row.mint,
                    now=now,
                    payload={
                        "seat": "SIZE",
                        "phase": "size",
                        "symbol": row.symbol,
                        "ticket_usd": format(decision.notional or Decimal("0"), "f"),
                        "pct_of_bank": format(size_info.pct_of_bank, "f"),
                        "reason": size_info.reason,
                    },
                )

                if shadow_mode:
                    self._emit(
                        session,
                        source="orchestrator",
                        event_type="shadow.logged_only",
                        correlation_id=correlation_id,
                        mode=mode,
                        mint=row.mint,
                        now=now,
                        payload={
                            "seat": "CHIEF",
                            "phase": "shadow",
                            "symbol": row.symbol,
                            "notional": format(decision.notional or Decimal("0"), "f"),
                            "note": "Shadow mode active: candidate logged to shadow_decisions without creating a paper order.",
                        },
                    )
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
                provider_ok = self.gecko.circuit.state != "open" and self.dex.circuit.state != "open" and self.gmgn.client.circuit.state != "open"
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
                    payload={
                        "seat": "RISK",
                        "phase": "risk_gate",
                        "symbol": row.symbol,
                        "approved": risk.approved,
                        "reason_codes": risk.reason_codes,
                        "explanation": risk.explanation,
                    },
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
                        "venue": "gmgn_paper" if (quote and quote.source == "gmgn_quote") else "paper_sim",
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
                        payload={"seat": "FILLS", "phase": "fills", "symbol": row.symbol, "status": plan.status, "reason": plan.reason},
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
                        memo=f"Paper buy {row.symbol} at quote ({plan.fee_source}).",
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
                        "seat": "FILLS",
                        "phase": "fills",
                        "symbol": row.symbol,
                        "status": plan.status,
                        "price": format(fill.price, "f"),
                        "qty": format(fill.qty, "f"),
                        "fee": format(fill.fee, "f"),
                        "notional": format(fill.notional, "f"),
                        "quote_source": quote.source if quote else None,
                    },
                )
                self._touch(session, "execution", f"buy {row.symbol}", plan.status, now, now)

        # If 2+ live JEV survivors exist, execute the single `pick` call and log it transparently.
        if len(jev_survivors) >= 2 and self.jev.available:
            try:
                pick_candidates = [
                    {
                        "symbol": obs.symbol,
                        "summary": (
                            f"liq ${float(obs.pool_liquidity_usd or 0):,.0f}, "
                            f"crowd {float((ans.get('shape') or {}).get('probabilities', {}).get('crowd', 0)):.2f}, "
                            f"conc {float((ans.get('concentration_is_exit_risk') or {}).get('noul', 0)):.2f}"
                        ),
                    }
                    for obs, ans, _ in jev_survivors
                ]
                pick_rec = await self.jev.ask("pick", {"candidates": pick_candidates})
                if pick_rec:
                    with session_scope() as session:
                        self._emit(
                            session,
                            source="strategy",
                            event_type="jev.pick",
                            correlation_id=cycle_id,
                            mode=mode,
                            mint=None,
                            now=now,
                            payload={
                                "seat": "CHIEF",
                                "phase": "pick",
                                "resolved_model": pick_rec.resolved_model,
                                "answers": pick_rec.answers,
                                "input_tokens": pick_rec.input_tokens,
                                "estimated_cost_usd": pick_rec.estimated_cost_usd,
                            },
                        )
            except ProviderError as exc:
                log.warning("jev pick call failed: %s", exc.message)

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
            session.add(
                FunnelRun(
                    id=cycle_id,
                    mode=mode,
                    created_at=now,
                    held_symbol=None,
                    shadow_mode=shadow_mode,
                    jev_connected=self.jev.available,
                    jev_model=self.jev.last_resolved_model,
                    gmgn_configured=self.gmgn.configured,
                    stats=funnel_stats,
                    thresholds_snapshot={"hard": hard, "soft": soft},
                )
            )
            for snap in (
                self.gmgn.snapshot(),
                self.jev.snapshot(),
                self.gecko.snapshot(),
                self.dex.snapshot(),
                self.jupiter.client.snapshot(),
                self.rpc.client.snapshot(),
                self.grok.client.snapshot(),
            ):
                if mode == "demo" and snap.provider not in {"jev", "gmgn", "grok"}:
                    snap.status = "not_used"
                    snap.detail = "Demo mode does not call this provider."
                elif mode == "demo" and snap.provider == "gmgn" and not snap.configured:
                    snap.status = "not_used"
                    snap.detail = "Demo mode uses labeled fixtures; set GMGN_API_KEY for live GMGN feeds."
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
        return {"mode": mode, "candidates": len(scored), "cycle_id": cycle_id}

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
                            payload={"seat": "RISK", "phase": "unsellable", "reason": signal},
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
                self._emit(
                    session,
                    source="risk",
                    event_type="risk.exit_release",
                    correlation_id=pos_id,
                    mode=mode,
                    mint=mint,
                    now=now,
                    payload={"seat": "RISK", "phase": "release", "signal": signal, "realized_pnl": format(plan.fill.realized_pnl, "f")},
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
                payload={"seat": "RISK", "actor": actor, "cancelled_entries": cancelled, "positions_liquidated": False},
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
                payload={"seat": "RISK", "actor": actor, "positions_changed": False},
            )
            return {"emergency_stop": False}

    def update_settings(self, patch: dict, actor: str) -> dict:
        now = utcnow()
        with session_scope() as session:
            current = dict(self._config(session))
            for section in ("scanner", "strategy", "risk", "simulation", "ai", "thresholds"):
                if section in patch and isinstance(patch[section], dict):
                    merged = dict(current.get(section) or {})
                    if section == "thresholds":
                        for sub in ("hard", "soft"):
                            if isinstance(patch["thresholds"].get(sub), dict):
                                merged[sub] = {**(merged.get(sub) or {}), **patch["thresholds"][sub]}
                    else:
                        merged.update(patch[section])
                    current[section] = merged
            if "risk" in patch and "thresholds" not in patch:
                th = dict(current.get("thresholds") or {})
                hard = dict(th.get("hard") or DEFAULT_THRESHOLDS["hard"])
                if "min_pool_liquidity_usd" in patch["risk"]:
                    hard["min_liquidity_usd"] = float(patch["risk"]["min_pool_liquidity_usd"])
                if "min_pool_age_minutes" in patch["risk"]:
                    hard["min_age_minutes"] = int(patch["risk"]["min_pool_age_minutes"])
                th["hard"] = hard
                current["thresholds"] = th
            if "mode" in patch:
                if patch["mode"] not in {"demo", "live"}:
                    raise ValueError("mode must be demo or live")
                current["mode"] = patch["mode"]
            limits_from_config(current)
            row = session.get(AppSetting, "runtime")
            row.value = current
            row.updated_at = now
            session.add(ConfigVersion(created_at=now, actor=actor, document=current, note="settings update"))
            return current


async def _gather(left, right):
    return await asyncio.gather(left, right)
