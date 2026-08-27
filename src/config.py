"""Application configuration using pydantic-settings."""

import json
from functools import lru_cache
from typing import Any, Literal, cast

from pydantic import Field, SecretStr, field_validator
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    EnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

_DELIMITED_LIST_FIELDS = (
    "api_keys",
    "cors_origins",
    "observability_scheduler_org_ids",
    "observability_notification_webhooks",
    "observability_notification_slack_webhooks",
    "observability_notification_pagerduty_routing_keys",
    "observability_notification_allowed_hosts",
    "observability_notification_idempotent_webhooks",
)


def _parse_list_setting(value: Any) -> Any:
    """Accept JSON arrays, comma-delimited values, scalars, and empty strings."""
    if not isinstance(value, str):
        return value

    normalized = value.strip()
    if not normalized:
        return []

    if normalized.startswith("["):
        parsed = json.loads(normalized)
        if not isinstance(parsed, list):
            raise ValueError("list settings encoded as JSON must use an array")
        return [
            item.strip() if isinstance(item, str) else item
            for item in parsed
            if not isinstance(item, str) or item.strip()
        ]

    return [item.strip() for item in normalized.split(",") if item.strip()]


class _DelimitedListEnvSettingsSource(EnvSettingsSource):
    """Environment source that decodes the application's flexible list syntax."""

    def decode_complex_value(
        self,
        field_name: str,
        field: FieldInfo,
        value: Any,
    ) -> Any:
        if field_name in _DELIMITED_LIST_FIELDS:
            return _parse_list_setting(value)
        return super().decode_complex_value(field_name, field, value)


class _DelimitedListDotEnvSettingsSource(DotEnvSettingsSource):
    """Dotenv source that decodes the application's flexible list syntax."""

    def decode_complex_value(
        self,
        field_name: str,
        field: FieldInfo,
        value: Any,
    ) -> Any:
        if field_name in _DELIMITED_LIST_FIELDS:
            return _parse_list_setting(value)
        return super().decode_complex_value(field_name, field, value)


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Use flexible list decoding before pydantic-settings attempts JSON parsing."""
        environment = cast(EnvSettingsSource, env_settings)
        dotenv = cast(DotEnvSettingsSource, dotenv_settings)
        return (
            init_settings,
            _DelimitedListEnvSettingsSource(
                settings_cls,
                case_sensitive=environment.case_sensitive,
                env_prefix=environment.env_prefix,
                env_nested_delimiter=environment.env_nested_delimiter,
            ),
            _DelimitedListDotEnvSettingsSource(
                settings_cls,
                env_file=dotenv.env_file,
                env_file_encoding=dotenv.env_file_encoding,
                case_sensitive=dotenv.case_sensitive,
                env_prefix=dotenv.env_prefix,
                env_nested_delimiter=dotenv.env_nested_delimiter,
            ),
            file_secret_settings,
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
    host: str = Field(default="127.0.0.1")
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
    browser_session_cookie_name: str = Field(default="ai_trace_session", min_length=1)
    browser_csrf_cookie_name: str = Field(default="ai_trace_csrf", min_length=1)
    browser_csrf_header: str = Field(default="X-CSRF-Token", min_length=1)
    browser_session_cookie_secure: bool = Field(
        default=True,
        description="Require HTTPS for browser-session and CSRF cookies",
    )
    browser_session_ttl_minutes: int = Field(default=480, ge=5, le=10080)
    operator_console_dist_dir: str = Field(
        default="web/dist",
        min_length=1,
        description="Directory containing the built same-origin operator console",
    )
    api_rate_limit_enabled: bool = Field(default=False)
    api_rate_limit_requests_per_window: int = Field(default=240, ge=1, le=100000)
    api_rate_limit_window_seconds: int = Field(default=60, ge=1, le=3600)
    api_rate_limit_per_path: bool = Field(default=False)
    api_rate_limit_max_keys: int = Field(default=10000, ge=100, le=1000000)

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
    observability_scheduler_lease_seconds: int = Field(
        default=30,
        ge=10,
        le=300,
        description="Database-backed scheduler leadership lease duration",
    )
    observability_scheduler_run_detectors: bool = Field(default=True)
    observability_scheduler_run_policies: bool = Field(default=True)
    observability_scheduler_execute_policy_actions: bool = Field(default=True)
    runtime_control_lease_seconds: int = Field(
        default=60,
        ge=10,
        le=300,
        description="Runtime control delivery lease duration",
    )
    runtime_control_max_delivery_attempts: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Maximum lease attempts before a runtime control fails closed",
    )
    observability_scheduler_enable_notifications: bool = Field(
        default=False,
        description="Enable durable scheduler notification enqueue and delivery",
    )
    observability_active_session_inactivity_minutes: int = Field(
        default=30,
        ge=1,
        le=10080,
        description=(
            "Maximum age of coalesced session last_activity_at/started_at for active projections"
        ),
    )

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
    observability_notification_webhooks: list[SecretStr] = Field(default_factory=list)
    observability_notification_slack_webhooks: list[SecretStr] = Field(default_factory=list)
    observability_notification_pagerduty_routing_keys: list[SecretStr] = Field(default_factory=list)
    observability_notification_allowed_hosts: list[str] = Field(
        default_factory=list,
        description="Exact HTTPS host allowlist for outbound notification webhooks",
    )
    observability_notification_idempotent_webhooks: list[SecretStr] = Field(
        default_factory=list,
        description=(
            "Generic webhook URLs whose receivers honor the Idempotency-Key header"
        ),
    )
    observability_notification_fingerprint_key: SecretStr = Field(
        default=SecretStr(""),
        description="Secret HMAC key used to fingerprint notification destinations",
    )
    observability_notification_min_severity: Literal["info", "warning", "error", "critical"] = (
        Field(default="warning")
    )
    observability_notification_only_on_actionable: bool = Field(default=True)
    observability_notification_timeout_seconds: float = Field(default=5.0, ge=0.1, le=30.0)
    observability_notification_max_attempts: int = Field(default=3, ge=1, le=10)
    observability_notification_retry_backoff_seconds: float = Field(default=0.5, ge=0.0, le=10.0)
    observability_notification_outbox_batch_size: int = Field(default=25, ge=1, le=500)
    observability_notification_claim_seconds: int = Field(default=30, ge=10, le=300)
    observability_notification_retention_days: int = Field(default=30, ge=1, le=3650)

    # Runtime policy safety controls
    observability_shutdown_requires_approval: bool = Field(
        default=True,
        description="Require approval records before shutdown actions execute",
    )
    observability_shutdown_approval_max_age_minutes: int = Field(default=60, ge=1, le=10080)

    @field_validator(*_DELIMITED_LIST_FIELDS, mode="before")
    @classmethod
    def _parse_list_settings(cls, value: Any) -> Any:
        return _parse_list_setting(value)


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
