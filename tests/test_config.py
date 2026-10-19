"""Tests for safe application configuration."""

import pytest
from pydantic import SecretStr

from backend.config import Settings, get_settings


def test_settings_can_load_without_credentials():
    settings = Settings(_env_file=None)
    assert settings.gemini_api_key is None
    assert settings.paypal_client_secret is None
    assert settings.paypal_environment == "sandbox"


def test_secret_fields_are_masked():
    settings = Settings(
        _env_file=None,
        gemini_api_key="synthetic-test-key",
        paypal_client_secret="synthetic-test-secret",
    )
    assert isinstance(settings.gemini_api_key, SecretStr)
    assert isinstance(settings.paypal_client_secret, SecretStr)
    assert "synthetic-test-secret" not in repr(settings)


def test_setup_rejects_live_paypal_environment(monkeypatch):
    monkeypatch.setenv("PAYPAL_ENVIRONMENT", "live")
    with pytest.raises(ValueError, match="Sandbox"):
        get_settings()
