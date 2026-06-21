"""Environment-driven configuration."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # Database
    database_url: str = "postgresql+psycopg2://portal:portal@localhost:5432/scanguru_portal"

    # Auth
    jwt_secret: str = "change-me-in-production-openssl-rand-hex-32"
    jwt_algorithm: str = "HS256"
    jwt_access_token_minutes: int = 60

    # PHI encryption — Fernet key (32-byte url-safe base64)
    phi_encryption_key: str = ""

    # AI service (your existing Railway backend)
    ai_service_url: str = "https://adorable-simplicity-production.up.railway.app"
    ai_service_timeout_seconds: int = 300

    # Storage: firebase | s3 | local
    storage_backend: str = "local"
    firebase_credentials_b64: str = ""
    firebase_storage_bucket: str = ""
    s3_endpoint_url: str | None = None
    s3_bucket: str | None = None
    s3_region: str = "us-east-1"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None

    # CORS
    cors_allow_origins: str = "http://localhost:3000,https://scanguru.net,https://www.scanguru.net"

    # Free tier ("Heal for All")
    default_monthly_scan_quota: int = 100
    warn_threshold: float = 0.80

    # Environment
    env: str = "dev"


settings = Settings()
