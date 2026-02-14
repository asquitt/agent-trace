"""Application configuration using pydantic-settings."""

from functools import lru_cache
from typing import Any, Literal

from pydantic import Field, SecretStr
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Database
    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5434/ai_trace",
        description="PostgreSQL connection string",
    )
    database_echo: bool = Field(default=False, description="Echo SQL queries")
    database_pool_size: int = Field(default=5, description="Connection pool size")
    database_max_overflow: int = Field(default=10, description="Max overflow connections")

    # Redis
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection string",
    )

    # AI Providers
    anthropic_api_key: SecretStr = Field(
        default=SecretStr(""),
        description="Anthropic API key",
    )
    openai_api_key: SecretStr = Field(
        default=SecretStr(""),
        description="OpenAI API key",
    )

    # Default models
    default_llm_model: str = Field(
        default="claude-sonnet-4-20250514",
        description="Default LLM model for ranking",
    )
    default_embedding_model: str = Field(
        default="text-embedding-3-small",
        description="Default embedding model",
    )
    embedding_dimensions: int = Field(default=1536, description="Embedding vector dimensions")

    # Tracing Configuration
    trace_retention_days: int = Field(default=90, description="Days to retain traces")
    trace_batch_size: int = Field(default=100, description="Batch size for trace operations")
    trace_flush_interval_seconds: float = Field(
        default=5.0,
        description="Interval for flushing trace buffer",
    )
    trace_capture_prompts: bool = Field(
        default=True,
        description="Capture full prompts/responses in traces",
    )

    # Model pricing (per 1M tokens)
    claude_sonnet_input_price: float = Field(default=3.00, description="Claude Sonnet input price")
    claude_sonnet_output_price: float = Field(
        default=15.00, description="Claude Sonnet output price"
    )
    openai_embedding_price: float = Field(
        default=0.02, description="OpenAI embedding price per 1M tokens"
    )

    # Ranking weights (should sum to 1.0)
    weight_solo_buildable: float = Field(default=0.25)
    weight_resource_intensity: float = Field(default=0.20)
    weight_moat_potential: float = Field(default=0.20)
    weight_market_timing: float = Field(default=0.15)
    weight_profitability_path: float = Field(default=0.15)
    weight_personal_fit: float = Field(default=0.05)

    # Server
    service_name: str = Field(default="ai-trace")
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8000)
    debug: bool = Field(default=False)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(default="INFO")

    # API auth and tenancy controls
    api_auth_enabled: bool = Field(
        default=False,
        description="Enable API key authentication",
    )
    api_key_header: str = Field(default="X-API-Key")
    api_tenant_header: str = Field(default="X-Org-Id")
    api_require_tenant_header: bool = Field(
        default=False,
        description="Require tenant org header for authenticated requests",
    )
    api_keys: list[str] = Field(
        default_factory=list,
        description=(
            "API key entries as token:subject:role1|role2:org1|org2 "
            "(org '*' grants global access)"
        ),
    )
    api_rate_limit_enabled: bool = Field(default=False)
    api_rate_limit_requests_per_window: int = Field(default=240, ge=1, le=100000)
    api_rate_limit_window_seconds: int = Field(default=60, ge=1, le=3600)
    api_rate_limit_per_path: bool = Field(default=False)

    # CORS (for dashboard)
    cors_origins: list[str] = Field(
        default=["http://localhost:3000", "http://127.0.0.1:3000"],
        description="Allowed CORS origins",
    )

    # Observability runtime operations
    observability_scheduler_enabled: bool = Field(
        default=False,
        description="Enable background scheduler for detector/policy loops",
    )
    observability_scheduler_org_ids: list[str] = Field(
        default_factory=list,
        description="Org IDs that scheduler processes",
    )
    observability_scheduler_interval_seconds: int = Field(default=60, ge=5, le=3600)
    observability_scheduler_run_detectors: bool = Field(default=True)
    observability_scheduler_run_policies: bool = Field(default=True)
    observability_scheduler_execute_policy_actions: bool = Field(default=True)
    observability_scheduler_enable_notifications: bool = Field(default=True)

    # Detector tuning defaults
    observability_detector_current_window_minutes: int = Field(default=15, ge=1, le=180)
    observability_detector_baseline_window_hours: int = Field(default=24, ge=1, le=168)
    observability_detector_api_spike_multiplier: float = Field(default=10.0, ge=1.0, le=100.0)
    observability_detector_cost_spike_multiplier: float = Field(default=5.0, ge=1.0, le=100.0)
    observability_detector_unusual_resource_min_calls: int = Field(default=3, ge=1, le=1000)
    observability_detector_memory_divergence_threshold: float = Field(default=0.30, ge=0.0, le=1.0)
    observability_detector_anomaly_dedupe_window_minutes: int = Field(default=30, ge=1, le=1440)
    observability_detector_anomaly_reopen_acknowledged: bool = Field(default=True)

    # Notification dispatch
    observability_notification_webhooks: list[str] = Field(default_factory=list)
    observability_notification_slack_webhooks: list[str] = Field(default_factory=list)
    observability_notification_pagerduty_routing_keys: list[str] = Field(default_factory=list)
    observability_notification_timeout_seconds: float = Field(default=5.0, ge=0.1, le=30.0)
    observability_notification_max_attempts: int = Field(default=3, ge=1, le=10)
    observability_notification_retry_backoff_seconds: float = Field(default=0.5, ge=0.0, le=10.0)

    # Runtime policy safety controls
    observability_shutdown_requires_approval: bool = Field(
        default=True,
        description="Require approval records before shutdown actions execute",
    )
    observability_shutdown_approval_max_age_minutes: int = Field(default=60, ge=1, le=10080)

    @field_validator("observability_scheduler_org_ids", mode="before")
    @classmethod
    def _parse_org_ids(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("observability_notification_webhooks", mode="before")
    @classmethod
    def _parse_webhooks(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("observability_notification_slack_webhooks", mode="before")
    @classmethod
    def _parse_slack_webhooks(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("observability_notification_pagerduty_routing_keys", mode="before")
    @classmethod
    def _parse_pagerduty_routing_keys(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("api_keys", mode="before")
    @classmethod
    def _parse_api_keys(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
