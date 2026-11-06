from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE) if _ENV_FILE.is_file() else None,
        extra="ignore",
    )

    app_env: str = "development"
    app_secret_key: str = Field(min_length=16)
    database_url: str
    frontend_origin: str = "http://127.0.0.1:8080"
    # Comma-separated extra browser origins (the phone app, a tunnel hostname).
    frontend_origins: str = ""
    # Same list, under the name used in the hosting guide. Merged with FRONTEND_ORIGINS.
    allowed_origins: str = ""
    timezone: str = "Asia/Karachi"
    mfa_issuer: str = "KPRA Smart Attendance Lite"
    cookie_secure: bool = False
    cookie_samesite: str = "lax"
    trust_proxy: bool = False
    session_hours: int = 12
    idle_hours: int = 8

    bootstrap_admin_email: str | None = None
    bootstrap_admin_password: str | None = None
    bootstrap_admin_name: str = "Administrator"

    seed_dev_data: bool = False
    seed_manager_email: str | None = None
    seed_manager_password: str | None = None
    seed_manager_name: str = "Test Manager"

    login_rate_limit: int = 8
    login_rate_window_seconds: int = 900
    attendance_rate_limit: int = 30
    attendance_rate_window_seconds: int = 60

    timenex_integration_enabled: bool = False
    timenex_base_url: str = ""
    timenex_api_token: str = ""
    timenex_write_mode: str = "none"
    timenex_portal_url: str = "https://www.timenex.net:8443"
    timenex_username: str = ""
    timenex_password: str = ""
    timenex_portal_enabled: bool = False
    senseface_adms_enabled: bool = False

    @model_validator(mode="after")
    def production_guards(self) -> "Settings":
        self.database_url = normalize_database_url(self.database_url)
        samesite = self.cookie_samesite.strip().lower()
        if samesite not in {"lax", "strict", "none"}:
            raise ValueError("COOKIE_SAMESITE must be lax, strict, or none")
        self.cookie_samesite = samesite
        if samesite == "none" and not self.cookie_secure:
            raise ValueError("COOKIE_SECURE must be true when COOKIE_SAMESITE=none")
        if self.app_env == "production":
            if self.seed_dev_data:
                raise ValueError("SEED_DEV_DATA cannot be enabled when APP_ENV=production")
            if len(self.app_secret_key) < 32 or "CHANGE_ME" in self.app_secret_key:
                raise ValueError("APP_SECRET_KEY is not a production secret")
            if not self.cookie_secure:
                raise ValueError("COOKIE_SECURE must be true when APP_ENV=production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


def normalize_database_url(url: str) -> str:
    """Render gives postgres://. SQLAlchemy on this app uses the psycopg driver."""
    value = url.strip()
    if value.startswith("postgres://"):
        value = "postgresql://" + value[len("postgres://") :]
    if value.startswith("postgresql://") and not value.startswith("postgresql+"):
        value = "postgresql+psycopg://" + value[len("postgresql://") :]
    return value


def browser_origins(settings: Settings | None = None) -> list[str]:
    current = settings or get_settings()
    found: list[str] = []
    for raw in [current.frontend_origin, *current.frontend_origins.split(","), *current.allowed_origins.split(",")]:
        item = raw.strip().rstrip("/")
        if item and item not in found:
            found.append(item)
    return found
