import os
from dataclasses import dataclass, field
from typing import Mapping

from integrations.google_sheets.schema import TRANSACTIONS_SHEET_NAME


REQUIRED_SECRET_ENV_VARS = (
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_WEBHOOK_SECRET",
    "PARSER_API_KEY",
    "GOOGLE_SERVICE_ACCOUNT_JSON",
)


@dataclass(frozen=True)
class Settings:
    service_name: str
    default_timezone: str
    default_currency: str
    parser_model: str | None
    google_sheet_id: str | None
    google_worksheet_name: str
    telegram_bot_token: str | None = field(repr=False)
    telegram_webhook_secret: str | None = field(repr=False)
    parser_api_key: str | None = field(repr=False)
    google_service_account_json: str | None = field(repr=False)

    def public_dict(self) -> dict[str, object]:
        return {
            "service_name": self.service_name,
            "default_timezone": self.default_timezone,
            "default_currency": self.default_currency,
            "parser_model": self.parser_model,
            "google_sheet_id_configured": bool(self.google_sheet_id),
            "google_worksheet_name": self.google_worksheet_name,
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
        google_sheet_id=_optional_value(values, "GOOGLE_SHEET_ID"),
        google_worksheet_name=values.get(
            "GOOGLE_WORKSHEET_NAME",
            TRANSACTIONS_SHEET_NAME,
        ),
        telegram_bot_token=_optional_value(values, "TELEGRAM_BOT_TOKEN"),
        telegram_webhook_secret=_optional_value(
            values,
            "TELEGRAM_WEBHOOK_SECRET",
        ),
        parser_api_key=_optional_value(values, "PARSER_API_KEY"),
        google_service_account_json=_optional_value(
            values,
            "GOOGLE_SERVICE_ACCOUNT_JSON",
        ),
    )


def _optional_value(values: Mapping[str, str], name: str) -> str | None:
    return values.get(name) or None


def _env_name_to_field(name: str) -> str:
    return name.lower()


def _secret_status(value: str | None) -> str:
    return "<set>" if value else "<unset>"
