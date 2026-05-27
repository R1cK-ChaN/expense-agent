from config.settings import REQUIRED_SECRET_ENV_VARS, load_settings
from integrations.google_sheets.schema import TRANSACTIONS_SHEET_NAME


def test_settings_track_required_secret_environment_names():
    assert REQUIRED_SECRET_ENV_VARS == (
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_WEBHOOK_SECRET",
        "WECHAT_TOKEN",
        "PARSER_API_KEY",
        "GOOGLE_SERVICE_ACCOUNT_JSON",
    )


def test_settings_default_to_canonical_transactions_sheet_name():
    settings = load_settings({})

    assert settings.google_worksheet_name == TRANSACTIONS_SHEET_NAME


def test_settings_do_not_expose_secret_values():
    settings = load_settings(
        {
            "TELEGRAM_BOT_TOKEN": "telegram-secret",
            "TELEGRAM_WEBHOOK_SECRET": "webhook-secret",
            "WECHAT_TOKEN": "wechat-secret",
            "PARSER_API_KEY": "parser-secret",
            "GOOGLE_SERVICE_ACCOUNT_JSON": "google-secret",
        }
    )

    public_settings = settings.public_dict()

    assert "telegram-secret" not in repr(public_settings)
    assert "webhook-secret" not in repr(public_settings)
    assert "wechat-secret" not in repr(public_settings)
    assert "parser-secret" not in repr(public_settings)
    assert "google-secret" not in repr(public_settings)
    assert public_settings["secrets"] == {
        "TELEGRAM_BOT_TOKEN": "<set>",
        "TELEGRAM_WEBHOOK_SECRET": "<set>",
        "WECHAT_TOKEN": "<set>",
        "PARSER_API_KEY": "<set>",
        "GOOGLE_SERVICE_ACCOUNT_JSON": "<set>",
    }


def test_settings_repr_does_not_expose_secret_values():
    settings = load_settings(
        {
            "TELEGRAM_BOT_TOKEN": "telegram-secret",
            "TELEGRAM_WEBHOOK_SECRET": "webhook-secret",
            "WECHAT_TOKEN": "wechat-secret",
            "PARSER_API_KEY": "parser-secret",
            "GOOGLE_SERVICE_ACCOUNT_JSON": "google-secret",
        }
    )

    settings_repr = repr(settings)

    assert "telegram-secret" not in settings_repr
    assert "webhook-secret" not in settings_repr
    assert "wechat-secret" not in settings_repr
    assert "parser-secret" not in settings_repr
    assert "google-secret" not in settings_repr


def test_settings_load_telegram_bot_token_without_public_exposure():
    settings = load_settings(
        {
            "TELEGRAM_BOT_TOKEN": "telegram-secret",
            "TELEGRAM_WEBHOOK_SECRET": "webhook-secret",
        }
    )

    assert settings.telegram_bot_token == "telegram-secret"
    assert settings.telegram_webhook_secret == "webhook-secret"
    assert "telegram-secret" not in repr(settings.public_dict())
    assert "webhook-secret" not in repr(settings.public_dict())
    assert settings.public_dict()["secrets"]["TELEGRAM_BOT_TOKEN"] == "<set>"
    assert settings.public_dict()["secrets"]["TELEGRAM_WEBHOOK_SECRET"] == "<set>"


def test_settings_load_optional_telegram_bot_username():
    settings = load_settings({"TELEGRAM_BOT_USERNAME": "@ExpenseAgentBot"})

    assert settings.telegram_bot_username == "ExpenseAgentBot"
    assert settings.public_dict()["telegram_bot_username"] == "ExpenseAgentBot"


def test_blank_optional_environment_values_are_unconfigured():
    settings = load_settings(
        {
            "TELEGRAM_BOT_TOKEN": "",
            "TELEGRAM_WEBHOOK_SECRET": "",
            "WECHAT_TOKEN": "",
            "PARSER_API_KEY": "",
            "GOOGLE_SERVICE_ACCOUNT_JSON": "",
            "GOOGLE_SHEET_ID": "",
        }
    )

    assert settings.google_sheet_id is None
    assert settings.public_dict()["google_sheet_id_configured"] is False
    assert settings.public_dict()["secrets"] == {
        "TELEGRAM_BOT_TOKEN": "<unset>",
        "TELEGRAM_WEBHOOK_SECRET": "<unset>",
        "WECHAT_TOKEN": "<unset>",
        "PARSER_API_KEY": "<unset>",
        "GOOGLE_SERVICE_ACCOUNT_JSON": "<unset>",
    }


def test_settings_load_wechat_token_without_public_exposure():
    settings = load_settings({"WECHAT_TOKEN": "wechat-secret"})

    assert settings.wechat_token == "wechat-secret"
    assert "wechat-secret" not in repr(settings)
    assert settings.public_dict()["secrets"]["WECHAT_TOKEN"] == "<set>"
