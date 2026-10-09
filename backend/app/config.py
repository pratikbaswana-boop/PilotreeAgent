"""Application settings loaded from env vars (pydantic-settings).

Follows §3.13 Config Table of DESIGN.md. Only M1-relevant settings are
exercised now; later milestones use the rest as they are implemented.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Database ───────────────────────────────────────────────────
    database_url: str
    database_session_pool_url: str
    database_pool_size: int = 20
    database_session_pool_size: int = 3
    listen_reconnect_grace_seconds: int = 15

    # ── OIDC / JWT auth ────────────────────────────────────────────
    oidc_issuer: str
    oidc_client_id: str
    oidc_jwks_uri: str
    jwt_clock_skew_seconds: int = 30
    local_development_auth: bool = False
    local_development_secret: str = ""

    # ── Job queue (M3+) ────────────────────────────────────────────
    worker_concurrency: int = 8
    job_succeeded_retention_days: int = 7
    action_sending_timeout_seconds: int = 30
    job_lease_seconds: int = 60
    job_heartbeat_seconds: int = 20
    job_max_attempts: int = 5
    job_backoff_base_ms: int = 500
    job_backoff_max_ms: int = 60000

    # ── HTTP timeouts (M5+) ────────────────────────────────────────
    http_connect_timeout_ms: int = 2000
    http_read_timeout_ms: int = 8000
    llm_read_timeout_ms: int = 20000
    request_deadline_budget_ms: int = 30000
    retry_max_attempts: int = 3
    retry_backoff_base_ms: int = 200

    # ── Circuit breaker (M7+) ──────────────────────────────────────
    breaker_failure_threshold: int = 5
    breaker_window_seconds: int = 60
    breaker_cooldown_seconds: int = 30
    breaker_cooldown_max_seconds: int = 300
    breaker_cache_ttl_seconds: int = 2

    # ── Config cache / rate limits / bulkheads (M7+) ───────────────
    config_cache_ttl_seconds: int = 10
    rate_limit_gemini_rps: int = 5
    rate_limit_slack_rps: int = 1
    rate_limit_linear_rps: int = 2
    rate_limit_sheets_rps: int = 2
    bulkhead_gemini_concurrency: int = 10
    bulkhead_slack_concurrency: int = 5
    bulkhead_linear_concurrency: int = 5
    bulkhead_sheets_concurrency: int = 3

    # ── SSE (M7) ───────────────────────────────────────────────────
    sse_max_connection_seconds: int = 600
    sse_reconnect_backoff_base_ms: int = 1000
    sse_reconnect_backoff_max_ms: int = 30000

    # ── Claims / retention ─────────────────────────────────────────
    claim_ttl_seconds: int = 300
    events_retention_days: int = 7
    checkpoint_retention_days: int = 90

    # ── Screeners / LLM (M5+) ──────────────────────────────────────
    presidio_thread_pool_size: int = 4
    presidio_score_threshold: float = 0.6
    regex_scan_thread_pool_size: int = 2
    gemini_model_primary: str = "gemini-2.5-flash"
    gemini_model_fallback: str = "gemini-2.0-flash"
    prompt_version: str = "v1"

    # ── Gemini API key ─────────────────────────────────────────────
    gemini_api_key: str = ""


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached Settings (loaded once at startup)."""
    return Settings()
