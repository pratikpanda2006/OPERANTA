import urllib.parse
from typing import Optional
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    MarginGuard Backend Configuration.
    Loads settings from environment variables and local backend/.env file.
    All sensitive credentials are protected via SecretStr to prevent accidental logging.
    """

    model_config = SettingsConfigDict(
        env_file="backend/.env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Supabase Transaction-Mode Pooler (Port 6543) - Application Runtime
    database_url: Optional[SecretStr] = Field(
        default=None,
        validation_alias="DATABASE_URL",
        description="Transaction pooler connection string (port 6543)",
    )

    # Supabase Session-Mode Pooler / Direct Host (Port 5432) - Migration Runner
    direct_url: Optional[SecretStr] = Field(
        default=None,
        validation_alias="DIRECT_URL",
        description="Session pooler / direct connection string (port 5432)",
    )

    # Database Pool Configuration
    db_min_connections: int = Field(default=2, ge=1, le=20)
    db_max_connections: int = Field(default=10, ge=1, le=50)

    # Multi-tenant Scoping & Demo Mode (Pilot merchant: Northstar Living)
    active_merchant_id: str = Field(default="merchant_northstar")
    demo_mode: bool = Field(
        default=False,
        validation_alias="DEMO_MODE",
        description="Explicit flag required to enable server-side fixed tenant access without production auth.",
    )
    demo_merchant_id: Optional[str] = Field(
        default="merchant_northstar",
        validation_alias="DEMO_MERCHANT_ID",
        description="Tenant ID resolved when DEMO_MODE is true. Never accepted from client input.",
    )

    # ---------------------------------------------------------------------------
    # OPERANTA Authentication & Session Configuration (M14.0)
    # ---------------------------------------------------------------------------
    # When DEMO_MODE is False, AUTH_SESSION_SECRET MUST be set. It is the HMAC
    # key used to sign session tokens. It is never exposed to the client.
    auth_session_secret: Optional[SecretStr] = Field(
        default=None,
        validation_alias="AUTH_SESSION_SECRET",
        description="HMAC secret used to sign OPERANTA session tokens.",
    )
    auth_token_ttl_minutes: int = Field(
        default=720,
        validation_alias="AUTH_TOKEN_TTL_MINUTES",
        description="Signed session token lifetime in minutes.",
    )

    # ---------------------------------------------------------------------------
    # Local-only SSL override for the migration runner.
    # ---------------------------------------------------------------------------
    # When set to True, migrate.py will disable TLS for the migration connection.
    # The runner enforces that this flag is ONLY honoured when DIRECT_URL resolves
    # to a loopback address (127.0.0.1 / ::1 / localhost).  Any attempt to set
    # this flag while pointing at a remote host (e.g. Supabase) will cause the
    # runner to exit with a non-zero status and log a clear error message.
    #
    # This flag MUST NOT be set in production or staging environments.
    # It exists solely to allow offline local PostgreSQL validation without TLS.
    migrate_allow_insecure_local: bool = Field(
        default=False,
        validation_alias="MIGRATE_ALLOW_INSECURE_LOCAL",
        description="Allow disabling TLS for migration connections to loopback addresses only.",
    )

    # ---------------------------------------------------------------------------
    # PayPal Sandbox Configuration (M12.0)
    # ---------------------------------------------------------------------------
    # Credentials are configured locally by the project owner in backend/.env.
    # They are NEVER read from the client, rendered in the frontend, or logged.
    paypal_mode: str = Field(
        default="sandbox",
        validation_alias="PAYPAL_MODE",
        description="PayPal environment: 'sandbox' for the hackathon demo.",
    )
    paypal_client_id: Optional[SecretStr] = Field(
        default=None,
        validation_alias="PAYPAL_CLIENT_ID",
        description="PayPal REST app Client ID (sandbox).",
    )
    paypal_client_secret: Optional[SecretStr] = Field(
        default=None,
        validation_alias="PAYPAL_CLIENT_SECRET",
        description="PayPal REST app Client Secret (sandbox). Never printed or exposed.",
    )
    paypal_webhook_id: Optional[SecretStr] = Field(
        default=None,
        validation_alias="PAYPAL_WEBHOOK_ID",
        description="PayPal webhook ID used to verify incoming webhook signatures. Set when a public HTTPS endpoint is registered.",
    )
    paypal_base_url: str = Field(
        default="https://api-m.sandbox.paypal.com",
        validation_alias="PAYPAL_BASE_URL",
        description="PayPal REST base URL (sandbox default).",
    )

    # Public base URL used to construct PayPal hosted-checkout return/cancel
    # callbacks. Safe local default; production must supply a public HTTPS URL
    # (validated at payload-build time; never hardcoded to production localhost).
    app_base_url: str = Field(
        default="http://127.0.0.1:8000",
        validation_alias="APP_BASE_URL",
        description="Public base URL of the backend used for PayPal return/cancel callbacks.",
    )

    # ---------------------------------------------------------------------------
    # Gemini Business Agent Configuration (M13.0)
    # ---------------------------------------------------------------------------
    # Credentials are configured locally in backend/.env. The API key is NEVER
    # read from the client, rendered in the frontend, or logged.
    gemini_api_key: Optional[SecretStr] = Field(
        default=None,
        validation_alias="GEMINI_API_KEY",
        description="Google AI Studio API key for the Gemini business agent. Never exposed to the client.",
    )
    gemini_model: str = Field(
        default="gemini-3.8-flash",
        validation_alias="GEMINI_MODEL",
        description="Gemini model identifier used for agent function calling. Keep configurable.",
    )

    # ---------------------------------------------------------------------------
    # OpenRouter (Secondary AI Provider) — M13.0.6
    # ---------------------------------------------------------------------------
    # OpenRouter is the fallback provider used when Gemini times out or returns
    # a provider error. It is optional; if unconfigured, Gemini remains the only
    # provider. Credentials are never read from the client or logged.
    openrouter_api_key: Optional[SecretStr] = Field(
        default=None,
        validation_alias="OPENROUTER_API_KEY",
        description="OpenRouter API key for the secondary AI provider. Never exposed to the client.",
    )
    openrouter_model: str = Field(
        default="openrouter/free",
        validation_alias="OPENROUTER_MODEL",
        description="OpenRouter model identifier used when Gemini is unavailable or times out.",
    )

    # ---------------------------------------------------------------------------
    # OPERANTA SaaS Registration & Demo Access (M15.0)
    # ---------------------------------------------------------------------------
    # The workspace access code that routes a registering user to the existing
    # Northstar demo workspace. Validated on the BACKEND only (constant-time
    # comparison). It is deliberately a shared hackathon entry code, not a login
    # password or a universal authentication bypass. Leaving the code blank
    # creates a brand-new isolated SaaS merchant workspace.
    demo_access_code: Optional[str] = Field(
        default=None,
        validation_alias="OPERANTA_DEMO_ACCESS_CODE",
        description="Workspace access code that grants access to the existing demo (Northstar) workspace.",
    )
    # Fernet (AES-GCM) encryption key used to protect merchant-specific PayPal
    # client secrets at rest. A SecretStr so it is never logged or exposed.
    paypal_credentials_encryption_key: Optional[SecretStr] = Field(
        default=None,
        validation_alias="PAYPAL_CREDENTIALS_ENCRYPTION_KEY",
        description="Encryption key for merchant-specific PayPal client secrets (Fernet/AES-GCM).",
    )

    @staticmethod
    def mask_connection_string(secret_url: Optional[SecretStr]) -> str:
        """
        Safely formats a database connection URL for logs by masking
        the username and password while preserving host, port, and database name.
        """
        if not secret_url:
            return "[NOT CONFIGURED]"
        raw = secret_url.get_secret_value()
        try:
            parsed = urllib.parse.urlsplit(raw)
            if not parsed.scheme or not parsed.hostname:
                return "[MALFORMED_URL]"

            # Build safe authority without user/password
            safe_netloc = parsed.hostname
            if parsed.port:
                safe_netloc = f"{safe_netloc}:{parsed.port}"

            return f"{parsed.scheme}://***:***@{safe_netloc}{parsed.path}"
        except Exception:
            return "[MASKED_URL]"

    @property
    def safe_database_url(self) -> str:
        """Masked string representation of DATABASE_URL safe for logging."""
        return self.mask_connection_string(self.database_url)

    @property
    def safe_direct_url(self) -> str:
        """Masked string representation of DIRECT_URL safe for logging."""
        return self.mask_connection_string(self.direct_url)


# Global settings instance singleton
settings = Settings()
