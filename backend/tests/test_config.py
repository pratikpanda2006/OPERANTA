"""
backend/tests/test_config.py
=============================
Unit tests for backend/app/core/config.py.

IMPORTANT:  These tests must run WITHOUT connecting to Supabase or any external
            database.  They test the Settings class configuration, URL masking
            logic, and SecretStr credential safety in complete isolation.

HOW TO RUN (from workspace root):
    backend\\.venv\\Scripts\\python.exe -m pytest backend/tests/test_config.py -v

All tests use environment-variable patching via pytest's `monkeypatch` fixture
so the real backend/.env file (if present) does not interfere.
"""

import pytest
from pydantic import SecretStr

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh_settings(monkeypatch, **env_vars):
    """
    Return a new Settings() instance with exactly the env vars provided,
    regardless of what is in backend/.env on disk.

    We clear DATABASE_URL, DIRECT_URL and other keys first so that a developer's
    local .env does not silently influence test outcomes.

    Why import inside the function?
        pydantic-settings reads env vars at instantiation time, not at import
        time.  By importing and constructing inside this helper (after monkeypatch
        has set the desired vars) we always get a clean instance reflecting only
        the vars we specified.
    """
    # Ensure the real .env on disk is never read by overriding the env-file path
    # to a non-existent location, then providing values purely via env vars.
    # pydantic-settings gives env vars higher priority than .env files, but we
    # also suppress the file entirely for hermeticity.
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("DIRECT_URL", "")

    for key, value in env_vars.items():
        monkeypatch.setenv(key, value)

    # Fresh import needed because `settings` at module level was already built.
    # We test the class directly rather than the singleton.
    from app.core.config import Settings
    return Settings(_env_file=None)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSettingsDefaults:
    """Verify default values when no .env is present and no env vars are set."""

    def test_database_url_default_is_none(self, monkeypatch):
        """DATABASE_URL should be None when no env var is provided."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("DIRECT_URL", raising=False)
        from app.core.config import Settings
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert s.database_url is None

    def test_direct_url_default_is_none(self, monkeypatch):
        """DIRECT_URL should be None when no env var is provided."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("DIRECT_URL", raising=False)
        from app.core.config import Settings
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert s.direct_url is None

    def test_default_pool_min(self, monkeypatch):
        """db_min_connections should default to 2."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("DIRECT_URL", raising=False)
        from app.core.config import Settings
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert s.db_min_connections == 2

    def test_default_pool_max(self, monkeypatch):
        """db_max_connections should default to 10."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("DIRECT_URL", raising=False)
        from app.core.config import Settings
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert s.db_max_connections == 10

    def test_default_active_merchant_id(self, monkeypatch):
        """active_merchant_id should default to 'merchant_northstar'."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("DIRECT_URL", raising=False)
        from app.core.config import Settings
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert s.active_merchant_id == "merchant_northstar"


class TestMaskConnectionString:
    """Verify that mask_connection_string() hides credentials in log output."""

    def test_mask_valid_url(self):
        """A well-formed postgresql URL should be masked, keeping host/port/db."""
        from app.core.config import Settings
        url = SecretStr(
            "postgresql://postgres.myproject:supersecretpassword"
            "@aws-0-ap-southeast-1.pooler.supabase.co:6543/postgres"
        )
        result = Settings.mask_connection_string(url)

        # Host, port, and database name must be preserved for diagnostics.
        assert "aws-0-ap-southeast-1.pooler.supabase.co" in result
        assert "6543" in result
        assert "/postgres" in result

        # The actual password and username must NOT appear.
        assert "supersecretpassword" not in result
        assert "postgres.myproject" not in result

        # The mask sentinel must be present.
        assert "***:***@" in result

    def test_mask_none_returns_not_configured(self):
        """None input should return the '[NOT CONFIGURED]' sentinel."""
        from app.core.config import Settings
        result = Settings.mask_connection_string(None)
        assert result == "[NOT CONFIGURED]"

    def test_mask_empty_secret_str(self):
        """An empty SecretStr should return '[NOT CONFIGURED]' (falsy check)."""
        from app.core.config import Settings
        result = Settings.mask_connection_string(SecretStr(""))
        assert result == "[NOT CONFIGURED]"

    def test_mask_malformed_url_no_host(self):
        """A URL that parses but has no hostname should return '[MALFORMED_URL]'."""
        from app.core.config import Settings
        # 'not-a-url' has no scheme, so urlsplit won't set scheme/hostname.
        result = Settings.mask_connection_string(SecretStr("not-a-url"))
        assert result == "[MALFORMED_URL]"

    def test_mask_scheme_preserved(self):
        """The scheme (postgresql/postgres) should appear in the masked output."""
        from app.core.config import Settings
        url = SecretStr(
            "postgresql://user:pass@db.example.com:5432/mydb"
        )
        result = Settings.mask_connection_string(url)
        assert result.startswith("postgresql://")

    def test_mask_does_not_contain_raw_password(self):
        """Ensure raw password 'hunter2' never leaks into the masked string."""
        from app.core.config import Settings
        url = SecretStr("postgresql://alice:hunter2@db.example.com:5432/prod")
        result = Settings.mask_connection_string(url)
        assert "hunter2" not in result
        assert "alice" not in result


class TestSettingsFromEnvVars:
    """Verify that env vars are correctly loaded into the Settings model."""

    def test_database_url_loaded_from_env(self, monkeypatch):
        """DATABASE_URL env var should be wrapped in SecretStr."""
        monkeypatch.setenv(
            "DATABASE_URL",
            "postgresql://user:pass@host:6543/db",
        )
        monkeypatch.delenv("DIRECT_URL", raising=False)
        from app.core.config import Settings
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert s.database_url is not None
        assert isinstance(s.database_url, SecretStr)

    def test_direct_url_loaded_from_env(self, monkeypatch):
        """DIRECT_URL env var should be wrapped in SecretStr."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv(
            "DIRECT_URL",
            "postgresql://user:pass@host:5432/db",
        )
        from app.core.config import Settings
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert s.direct_url is not None
        assert isinstance(s.direct_url, SecretStr)

    def test_pool_min_from_env(self, monkeypatch):
        """DB_MIN_CONNECTIONS env var should override the default."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("DIRECT_URL", raising=False)
        monkeypatch.setenv("DB_MIN_CONNECTIONS", "5")
        from app.core.config import Settings
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert s.db_min_connections == 5

    def test_pool_max_from_env(self, monkeypatch):
        """DB_MAX_CONNECTIONS env var should override the default."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("DIRECT_URL", raising=False)
        monkeypatch.setenv("DB_MAX_CONNECTIONS", "20")
        from app.core.config import Settings
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert s.db_max_connections == 20

    def test_active_merchant_id_from_env(self, monkeypatch):
        """ACTIVE_MERCHANT_ID env var should override the default."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("DIRECT_URL", raising=False)
        monkeypatch.setenv("ACTIVE_MERCHANT_ID", "merchant_test")
        from app.core.config import Settings
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert s.active_merchant_id == "merchant_test"


class TestSecretStrNotExposed:
    """
    Verify that pydantic's SecretStr protection prevents credential exposure
    via repr(), str(), and dict() serialisation.

    This is a critical security property: if log statements accidentally
    include a Settings field (e.g. in a formatted string), the password must
    not appear in plain text.
    """

    def test_database_url_not_exposed_in_repr(self, monkeypatch):
        """repr(settings.database_url) must not reveal the actual password."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:TOPSECRET@host:6543/db")
        monkeypatch.delenv("DIRECT_URL", raising=False)
        from app.core.config import Settings
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert "TOPSECRET" not in repr(s.database_url)

    def test_direct_url_not_exposed_in_repr(self, monkeypatch):
        """repr(settings.direct_url) must not reveal the actual password."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("DIRECT_URL", "postgresql://user:MYSECRET@host:5432/db")
        from app.core.config import Settings
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert "MYSECRET" not in repr(s.direct_url)

    def test_settings_repr_does_not_expose_password(self, monkeypatch):
        """repr(settings) itself must not contain the raw password."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:ANOTHER_SECRET@host:6543/db")
        monkeypatch.delenv("DIRECT_URL", raising=False)
        from app.core.config import Settings
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert "ANOTHER_SECRET" not in repr(s)


class TestPoolSizeConstraints:
    """Verify that invalid pool size values are rejected by pydantic validation."""

    def test_min_connections_too_low_raises(self, monkeypatch):
        """db_min_connections < 1 should raise a ValidationError."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("DIRECT_URL", raising=False)
        monkeypatch.setenv("DB_MIN_CONNECTIONS", "0")
        from app.core.config import Settings
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            Settings(_env_file=None)  # type: ignore[call-arg]

    def test_max_connections_too_high_raises(self, monkeypatch):
        """db_max_connections > 50 should raise a ValidationError."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("DIRECT_URL", raising=False)
        monkeypatch.setenv("DB_MAX_CONNECTIONS", "51")
        from app.core.config import Settings
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            Settings(_env_file=None)  # type: ignore[call-arg]

    def test_min_connections_boundary_one_ok(self, monkeypatch):
        """db_min_connections = 1 (minimum allowed) should be accepted."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("DIRECT_URL", raising=False)
        monkeypatch.setenv("DB_MIN_CONNECTIONS", "1")
        from app.core.config import Settings
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert s.db_min_connections == 1

    def test_max_connections_boundary_fifty_ok(self, monkeypatch):
        """db_max_connections = 50 (maximum allowed) should be accepted."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("DIRECT_URL", raising=False)
        monkeypatch.setenv("DB_MAX_CONNECTIONS", "50")
        from app.core.config import Settings
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert s.db_max_connections == 50


class TestSafeProperties:
    """Verify that the safe_database_url and safe_direct_url properties work correctly."""

    def test_safe_database_url_when_not_set(self, monkeypatch):
        """safe_database_url should return '[NOT CONFIGURED]' when DATABASE_URL is absent."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("DIRECT_URL", raising=False)
        from app.core.config import Settings
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert s.safe_database_url == "[NOT CONFIGURED]"

    def test_safe_direct_url_when_not_set(self, monkeypatch):
        """safe_direct_url should return '[NOT CONFIGURED]' when DIRECT_URL is absent."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("DIRECT_URL", raising=False)
        from app.core.config import Settings
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert s.safe_direct_url == "[NOT CONFIGURED]"

    def test_safe_database_url_masks_password(self, monkeypatch):
        """safe_database_url should mask credentials while preserving host info."""
        monkeypatch.setenv(
            "DATABASE_URL",
            "postgresql://dbuser:verysecret@db.example.com:6543/appdb",
        )
        monkeypatch.delenv("DIRECT_URL", raising=False)
        from app.core.config import Settings
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        safe = s.safe_database_url
        assert "verysecret" not in safe
        assert "dbuser" not in safe
        assert "db.example.com" in safe
        assert "6543" in safe


class TestGeminiConfig:
    """Gemini business-agent configuration tests (M13.0.2)."""

    def test_gemini_default_model_is_gemini_3_8_flash(self):
        """The default model must be the current supported Gemini model."""
        from app.core.config import Settings
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert s.gemini_model == "gemini-3.8-flash"

    def test_gemini_model_env_override(self, monkeypatch):
        """GEMINI_MODEL should override the default model."""
        monkeypatch.setenv("GEMINI_MODEL", "gemini-4.9-flash")
        from app.core.config import Settings
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert s.gemini_model == "gemini-4.9-flash"

    def test_gemini_model_default_when_env_unset(self, monkeypatch):
        """Removing GEMINI_MODEL falls back to the default."""
        monkeypatch.delenv("GEMINI_MODEL", raising=False)
        from app.core.config import Settings
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert s.gemini_model == "gemini-3.8-flash"

    def test_gemini_missing_key_is_none(self, monkeypatch):
        """With no GEMINI_API_KEY, the setting defaults to None."""
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        from app.core.config import Settings
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert s.gemini_api_key is None

    def test_gemini_api_key_wrapped_in_secretstr(self, monkeypatch):
        """A configured GEMINI_API_KEY must be a SecretStr (never exposed)."""
        monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
        from app.core.config import Settings
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert isinstance(s.gemini_api_key, SecretStr)
        # SecretStr must not leak the raw value via str().
        assert "not-a-real-key" not in str(s.gemini_api_key)
        assert s.gemini_api_key.get_secret_value() == "not-a-real-key"

    def test_gemini_model_does_not_leak_key(self, monkeypatch):
        """Model setting is independent of the API key and always returns str."""
        monkeypatch.setenv("GEMINI_MODEL", "gemini-3.8-flash")
        monkeypatch.setenv("GEMINI_API_KEY", "another-non-secret")
        from app.core.config import Settings
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert isinstance(s.gemini_model, str)
        assert s.gemini_model == "gemini-3.8-flash"
