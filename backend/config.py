"""Validated application settings; never print secret values."""

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-2.5-flash"

    openrouter_api_key: SecretStr | None = None
    openrouter_model: str = "openrouter/free"

    paypal_environment: str = "sandbox"
    paypal_client_id: SecretStr | None = None
    paypal_client_secret: SecretStr | None = None


def get_settings() -> Settings:
    settings = Settings()
    if settings.paypal_environment != "sandbox":
        raise ValueError(
            "Only the PayPal Sandbox environment is allowed during setup."
        )
    return settings
