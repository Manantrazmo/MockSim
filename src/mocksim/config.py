from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        enable_decoding=False,  # let field_validators parse comma-separated lists
    )

    # ── Server ────────────────────────────────────────────────────
    mocksim_host: str = "0.0.0.0"
    mocksim_port: int = 8080

    # ── Database ──────────────────────────────────────────────────
    database_url: str

    # ── Trazmo integration ────────────────────────────────────────
    # Default webhook targets — overridden per-tenant via subscription API
    trazmo_webhook_base_url: str = "http://localhost:8000"
    trazmo_pos_webhook_path: str = "/webhooks/pos"
    trazmo_bank_webhook_path: str = "/webhooks/bank"

    @property
    def trazmo_pos_webhook_url(self) -> str:
        return self.trazmo_webhook_base_url.rstrip("/") + self.trazmo_pos_webhook_path

    @property
    def trazmo_bank_webhook_url(self) -> str:
        return self.trazmo_webhook_base_url.rstrip("/") + self.trazmo_bank_webhook_path

    # ── Security ──────────────────────────────────────────────────
    webhook_signing_secret: str
    mocksim_admin_token: str
    mocksim_allow_http: bool = False  # True only for local dev

    # ── Active regions ────────────────────────────────────────────
    enabled_regions: list[str] = ["PK"]

    @field_validator("enabled_regions", mode="before")
    @classmethod
    def parse_regions(cls, v: object) -> list[str]:
        if isinstance(v, str):
            return [r.strip().upper() for r in v.split(",") if r.strip()]
        return v  # type: ignore[return-value]

    # ── Webhook delivery ──────────────────────────────────────────
    default_webhook_timeout_seconds: int = 10
    webhook_retry_schedule: list[int] = [60, 300, 900, 3600, 21600, 86400, 86400]

    @field_validator("webhook_retry_schedule", mode="before")
    @classmethod
    def parse_retry_schedule(cls, v: object) -> list[int]:
        if isinstance(v, str):
            return [int(x.strip()) for x in v.split(",") if x.strip()]
        return v  # type: ignore[return-value]

    # ── Rate limits ───────────────────────────────────────────────
    pos_api_tps_limit: int = 50
    bank_api_tps_limit: int = 50
    admin_api_tps_limit: int = 20

    # ── Sim clock ─────────────────────────────────────────────────
    sim_clock_advance_slice_days: int = 1
    sim_clock_advance_budget_seconds: int = 30
    sim_clock_advance_async_threshold_days: int = 7

    # ── Observability ─────────────────────────────────────────────
    otlp_endpoint: str | None = None
    log_level: str = "INFO"

    # ── Auth (Phase G) ────────────────────────────────────────────
    # itsdangerous secret for signing session cookies. Don't reuse
    # the admin token — separate concerns.
    mocksim_session_secret: str = "dev-session-secret-change-in-prod-min-32-chars"
    # First-time bootstrap password for the default admin user. Read
    # only when admin_users is empty on startup. After that, change the
    # password via the dashboard (Phase H).
    mocksim_bootstrap_password: str = "admin"

    # ── Trazmo cross-system integration ───────────────────────────
    # DSN for trazmo-platform's postgres. The cross-system onboarding
    # endpoint (POST /admin/onboard-sme) writes entity / sme_profile /
    # merchant_profile / acquirer_mapping rows directly into this DB.
    # Empty = onboarding endpoint refuses to run.
    trazmo_database_url: str = ""


settings = Settings()
