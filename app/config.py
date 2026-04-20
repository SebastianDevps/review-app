"""Application settings loaded from environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # GitHub App (filled automatically via /setup)
    github_app_id: str = ""
    github_app_private_key: str = ""
    github_webhook_secret: str = ""

    # Anthropic
    anthropic_api_key: str = "sk-ant-mock"  # optional when mock_ai=true
    mock_ai: bool = False   # MOCK_AI=true → skip Anthropic calls (local testing)

    # Plane
    plane_api_key: str
    plane_base_url: str = "https://api.plane.so"

    # Database
    database_url: str = "postgresql+asyncpg://reviewapp:reviewapp@postgres:5432/reviewapp"

    # Redis / Celery
    redis_url: str = "redis://redis:6379/0"
    celery_broker_url: str = "redis://redis:6379/0"
    celery_result_backend: str = "redis://redis:6379/1"

    # App
    app_env: str = "development"
    app_secret_key: str = "change-me"
    log_level: str = "INFO"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()  # type: ignore[call-arg]
