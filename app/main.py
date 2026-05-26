from fastapi import FastAPI

from app.telegram_webhook import (
    TelegramReplyClient,
    TelegramTextHandler,
    create_telegram_webhook_router,
)
from config.settings import Settings, load_settings
from core.intent_parser import IntentParser
from core.transaction_service import TransactionService
from integrations.google_sheets.repository import (
    GoogleSheetsTransactionRepository,
    build_google_sheets_values_client,
)
from integrations.llm_client import OpenAICompatibleLLMClient
from integrations.telegram_client import TelegramBotClient


def create_app(
    *,
    telegram_reply_client: TelegramReplyClient | None = None,
    telegram_webhook_secret: str | None = None,
    telegram_bot_username: str | None = None,
    telegram_text_handler: TelegramTextHandler | None = None,
) -> FastAPI:
    settings = load_settings()
    application = FastAPI(title="Expense Agent")
    if telegram_reply_client is None and settings.telegram_bot_token is not None:
        telegram_reply_client = TelegramBotClient(
            bot_token=settings.telegram_bot_token,
        )
    if telegram_text_handler is None:
        telegram_text_handler = _build_transaction_text_handler(settings)

    @application.get("/health")
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "service": settings.service_name,
        }

    application.include_router(
        create_telegram_webhook_router(
            telegram_reply_client=telegram_reply_client,
            telegram_webhook_secret=(
                telegram_webhook_secret
                if telegram_webhook_secret is not None
                else settings.telegram_webhook_secret
            ),
            telegram_bot_username=(
                telegram_bot_username
                if telegram_bot_username is not None
                else settings.telegram_bot_username
            ),
            telegram_text_handler=telegram_text_handler,
        )
    )

    return application


def _build_transaction_text_handler(settings: Settings) -> TelegramTextHandler | None:
    if not _transaction_service_configured(settings):
        return None

    llm_client = OpenAICompatibleLLMClient(
        api_key=settings.parser_api_key,
        model=settings.parser_model,
    )
    sheets_client = build_google_sheets_values_client(
        settings.google_service_account_json
    )
    service = TransactionService(
        parser=IntentParser(llm_client=llm_client),
        repository=GoogleSheetsTransactionRepository(
            sheet_id=settings.google_sheet_id,
            sheets_client=sheets_client,
            timezone=settings.default_timezone,
        ),
        timezone=settings.default_timezone,
        default_currency=settings.default_currency,
    )
    return service.handle_telegram_message


def _transaction_service_configured(settings: Settings) -> bool:
    return all(
        (
            settings.parser_api_key,
            settings.parser_model,
            settings.google_service_account_json,
            settings.google_sheet_id,
        )
    )


app = create_app()
