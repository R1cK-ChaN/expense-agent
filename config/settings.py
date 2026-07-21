import os
from dataclasses import dataclass, field
from typing import Mapping

from integrations.google_sheets.schema import TRANSACTIONS_SHEET_NAME


DEFAULT_STORAGE_BACKEND = "google_sheets"
STORAGE_BACKEND_GOOGLE_SHEETS = "google_sheets"
STORAGE_BACKEND_POSTGRES = "postgres"

REQUIRED_SECRET_ENV_VARS = (
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_WEBHOOK_SECRET",
    "WECHAT_TOKEN",
    "PARSER_API_KEY",
    "GOOGLE_SERVICE_ACCOUNT_JSON",
    "DATABASE_URL",
)


@dataclass(frozen=True)
class Settings:
    service_name: str
    default_timezone: str
    default_currency: str
    parser_model: str | None
    agent_model: str
    function_batches_enabled: bool
    storage_backend: str
    google_sheet_id: str | None
    google_worksheet_name: str
    telegram_bot_username: str | None
    telegram_bot_token: str | None = field(repr=False)
    telegram_webhook_secret: str | None = field(repr=False)
    wechat_token: str | None = field(repr=False)
    parser_api_key: str | None = field(repr=False)
    google_service_account_json: str | None = field(repr=False)
    database_url: str | None = field(repr=False)

    def public_dict(self) -> dict[str, object]:
        return {
            "service_name": self.service_name,
            "default_timezone": self.default_timezone,
            "default_currency": self.default_currency,
            "parser_model": self.parser_model,
            "agent_model": self.agent_model,
            "function_batches_enabled": self.function_batches_enabled,
            "storage_backend": self.storage_backend,
            "google_sheet_id_configured": bool(self.google_sheet_id),
            "google_worksheet_name": self.google_worksheet_name,
            "telegram_bot_username": self.telegram_bot_username,
            "secrets": {
                name: _secret_status(getattr(self, _env_name_to_field(name)))
                for name in REQUIRED_SECRET_ENV_VARS
            },
        }


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    values = os.environ if env is None else env
    return Settings(
        service_name=values.get("SERVICE_NAME", "expense-agent"),
        default_timezone=values.get("DEFAULT_TIMEZONE", "Asia/Singapore"),
        default_currency=values.get("DEFAULT_CURRENCY", "SGD"),
        parser_model=_optional_value(values, "PARSER_MODEL"),
        agent_model=values.get("AGENT_MODEL", "gpt-5.5").strip() or "gpt-5.5",
        function_batches_enabled=_boolean_value(
            values,
            "FUNCTION_BATCHES_ENABLED",
        ),
        storage_backend=_storage_backend(values),
        google_sheet_id=_optional_value(values, "GOOGLE_SHEET_ID"),
        google_worksheet_name=values.get(
            "GOOGLE_WORKSHEET_NAME",
            TRANSACTIONS_SHEET_NAME,
        ),
        telegram_bot_username=_telegram_bot_username(values),
        telegram_bot_token=_optional_value(values, "TELEGRAM_BOT_TOKEN"),
        telegram_webhook_secret=_optional_value(
            values,
            "TELEGRAM_WEBHOOK_SECRET",
        ),
        wechat_token=_optional_value(values, "WECHAT_TOKEN"),
        parser_api_key=_optional_value(values, "PARSER_API_KEY"),
        google_service_account_json=_optional_value(
            values,
            "GOOGLE_SERVICE_ACCOUNT_JSON",
        ),
        database_url=_optional_value(values, "DATABASE_URL"),
    )


def _optional_value(values: Mapping[str, str], name: str) -> str | None:
    return values.get(name) or None


def _storage_backend(values: Mapping[str, str]) -> str:
    value = _optional_value(values, "STORAGE_BACKEND")
    if value is None:
        return DEFAULT_STORAGE_BACKEND
    return value.strip().lower() or DEFAULT_STORAGE_BACKEND


def _boolean_value(values: Mapping[str, str], name: str) -> bool:
    return values.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _telegram_bot_username(values: Mapping[str, str]) -> str | None:
    value = _optional_value(values, "TELEGRAM_BOT_USERNAME")
    if value is None:
        return None
    username = value.strip().removeprefix("@")
    return username or None


def _env_name_to_field(name: str) -> str:
    return name.lower()


def _secret_status(value: str | None) -> str:
    return "<set>" if value else "<unset>"
