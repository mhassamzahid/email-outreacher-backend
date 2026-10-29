from functools import lru_cache
from uuid import UUID

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration comes from environment variables (or a local .env file).

    Provider keys are optional here so the app boots without them; each integration
    checks for its own key when it is called.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Coldbrew API"
    environment: str = "development"

    # Comma-separated origins allowed to call the API (the Next.js frontend).
    cors_origins: str = Field(default="http://localhost:3000")

    # Shared Lead Intel database (Supabase Postgres).
    database_url: str | None = None
    supabase_url: str | None = None
    supabase_service_role_key: str | None = None
    # Where Supabase publishes the public keys its sign-in tokens are signed with. Defaults to the
    # project's standard address.
    supabase_jwks_url: str | None = None

    # Lead Intel user that new ICPs are recorded as created by (Lead Intel requires one). Until this
    # app has sign-in, every ICP added here is attributed to this account.
    lead_intel_icp_created_by: UUID | None = None

    # Claude, for scoring companies against an ICP (and later, writing emails).
    anthropic_api_key: str | None = None
    claude_scoring_model: str = "claude-haiku-5-5"
    # A researched company qualifies when it passes the ICP's rule and scores at least this.
    company_score_threshold: int = 60

    # Providers.
    apollo_api_key: str | None = None
    apify_token: str | None = None
    # Google Places API (New), for finding companies on Google Maps.
    google_places_api_key: str | None = None
    openai_api_key: str | None = None
    # Jina Reader, for websites that need a browser to render. Works without a key at a lower limit.
    jina_api_key: str | None = None
    # Requests per minute to allow Jina. Defaults sit just under its published limits
    # (20 without a key, 500 with one) so a burst never trips them.
    jina_rpm: int | None = None
    cheapsequencer_api_key: str | None = None
    cheapsequencer_webhook_secret: str | None = None

    @property
    def auth_issuer(self) -> str:
        return f"{(self.supabase_url or '').rstrip('/')}/auth/v1"

    @property
    def jwks_url(self) -> str:
        return self.supabase_jwks_url or f"{self.auth_issuer}/.well-known/jwks.json"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
