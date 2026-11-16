"""Runtime configuration. Secrets come from the environment, never from code."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    admin_token: str = Field(default="", alias="SOLSWARM_ADMIN_TOKEN")
    monitor_token: str = Field(default="", alias="SOLSWARM_MONITOR_TOKEN")
    mode: str = Field(default="demo", alias="SOLSWARM_MODE")
    database_url: str = Field(
        default=f"sqlite:///{(DATA_DIR / 'solswarm.db').as_posix()}",
        alias="SOLSWARM_DATABASE_URL",
    )
    host: str = Field(default="127.0.0.1", alias="SOLSWARM_HOST")
    port: int = Field(default=8000, alias="SOLSWARM_PORT")
    autostart: bool = Field(default=True, alias="SOLSWARM_AUTOSTART")
    scan_interval_seconds: int = Field(default=60, alias="SOLSWARM_SCAN_INTERVAL_SECONDS")
    agent_timeout_seconds: float = Field(default=8.0, alias="SOLSWARM_AGENT_TIMEOUT_SECONDS")

    solana_rpc_url: str = Field(
        default="https://api.mainnet-beta.solana.com",
        alias="SOLANA_RPC_URL",
    )
    gmgn_api_key: str = Field(default="", alias="GMGN_API_KEY")
    gmgn_base_url: str = Field(default="https://openapi.gmgn.ai", alias="GMGN_BASE_URL")

    # Optional paid JEV (TypeSafe System One) — disabled by default for zero-additional-cost operation.
    enable_optional_jev: bool = Field(default=False, alias="SOLSWARM_ENABLE_OPTIONAL_JEV")
    typesafe_api_key: str = Field(default="", alias="TYPESAFE_API_KEY")
    typesafe_model: str = Field(default="jev-latest", alias="TYPESAFE_DEFAULT_MODEL")
    typesafe_base_url: str = Field(default="https://api.typesafe.ai", alias="TYPESAFE_BASE_URL")

    # Supabase Free shared coordination (optional; falls back seamlessly to local SQLite when unset).
    supabase_url: str = Field(default="", alias="SUPABASE_URL")
    supabase_anon_key: str = Field(default="", alias="SUPABASE_ANON_KEY")
    supabase_service_role_key: str = Field(default="", alias="SUPABASE_SERVICE_ROLE_KEY")
    supabase_db_limit_bytes: int = Field(default=500 * 1024 * 1024, alias="SOLSWARM_SUPABASE_LIMIT_BYTES")
    supabase_task_ttl_hours: int = Field(default=24, alias="SOLSWARM_SUPABASE_TASK_TTL_HOURS")
    supabase_finding_ttl_hours: int = Field(default=72, alias="SOLSWARM_SUPABASE_FINDING_TTL_HOURS")
    supabase_decision_ttl_days: int = Field(default=14, alias="SOLSWARM_SUPABASE_DECISION_TTL_DAYS")
    supabase_heartbeat_ttl_hours: int = Field(default=24, alias="SOLSWARM_SUPABASE_HEARTBEAT_TTL_HOURS")
    supabase_max_payload_bytes: int = Field(default=4096, alias="SOLSWARM_SUPABASE_MAX_PAYLOAD_BYTES")

    # Grok Bot MCP bridge (over Tailscale Serve only; never exposed via Funnel).
    mcp_enabled: bool = Field(default=True, alias="SOLSWARM_MCP_ENABLED")
    mcp_token: str = Field(default="", alias="SOLSWARM_MCP_TOKEN")
    grok_bot_freshness_seconds: int = Field(default=180, alias="SOLSWARM_GROK_BOT_FRESHNESS_SECONDS")

    jupiter_api_key: str = Field(default="", alias="JUPITER_API_KEY")
    birdeye_api_key: str = Field(default="", alias="BIRDEYE_API_KEY")
    xai_api_key: str = Field(default="", alias="XAI_API_KEY")
    xai_model: str = Field(default="grok-4-fast", alias="XAI_MODEL")
    xai_base_url: str = Field(default="https://api.x.ai/v1", alias="XAI_BASE_URL")
    ai_hourly_budget: int = Field(default=20, alias="SOLSWARM_AI_HOURLY_BUDGET")

    dexscreener_base: str = "https://api.dexscreener.com"
    gecko_base: str = "https://api.geckoterminal.com/api/v2"
    jupiter_base: str = "https://api.jup.ag"
    birdeye_base: str = "https://public-api.birdeye.so"

    initial_equity: Decimal = Field(default=Decimal("1000"))

    @property
    def supabase_configured(self) -> bool:
        return bool(self.supabase_url.strip() and self.supabase_service_role_key.strip())

    def secret_values(self) -> list[str]:
        values = [
            self.admin_token,
            self.monitor_token,
            self.mcp_token,
            self.gmgn_api_key,
            self.supabase_anon_key,
            self.supabase_service_role_key,
            self.typesafe_api_key,
            self.jupiter_api_key,
            self.birdeye_api_key,
            self.xai_api_key,
        ]
        return [item for item in values if item and len(item) >= 6]

    def require_tokens(self) -> None:
        if not self.admin_token or not self.monitor_token:
            raise RuntimeError(
                "SOLSWARM_ADMIN_TOKEN and SOLSWARM_MONITOR_TOKEN must be set. "
                "See .env.example and scripts/start.ps1."
            )
        if self.admin_token == self.monitor_token:
            raise RuntimeError("Admin and monitor tokens must be different.")
        if self.mode not in {"demo", "live"}:
            raise RuntimeError("SOLSWARM_MODE must be demo or live.")
