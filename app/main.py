from fastapi import FastAPI

from app.telegram_webhook import (
    TelegramReplyClient,
    TelegramTextHandler,
    create_telegram_webhook_router,
)
from app.wechat_webhook import (
    WeChatLocationHandler,
    WeChatTextHandler,
    create_wechat_webhook_router,
)
from config.settings import (
    STORAGE_BACKEND_GOOGLE_SHEETS,
    STORAGE_BACKEND_POSTGRES,
    Settings,
    load_settings,
)
from core.intent_parser import IntentParser
from core.transaction_service import TransactionService
from integrations.exchange_rates import FrankfurterExchangeRateProvider
from integrations.google_sheets.repository import (
    GoogleSheetsTransactionRepository,
    build_google_sheets_values_client,
)
from integrations.llm_client import OpenAICompatibleLLMClient
from integrations.postgres.repository import PostgresTransactionRepository
from integrations.telegram_client import TelegramBotClient


def create_app(
    *,
    telegram_reply_client: TelegramReplyClient | None = None,
    telegram_webhook_secret: str | None = None,
    telegram_bot_username: str | None = None,
    telegram_text_handler: TelegramTextHandler | None = None,
    wechat_token: str | None = None,
    wechat_text_handler: WeChatTextHandler | None = None,
    wechat_location_handler: WeChatLocationHandler | None = None,
) -> FastAPI:
    settings = load_settings()
    application = FastAPI(title="Expense Agent")
    if telegram_reply_client is None and settings.telegram_bot_token is not None:
        telegram_reply_client = TelegramBotClient(
            bot_token=settings.telegram_bot_token,
        )
    if telegram_text_handler is None and wechat_text_handler is None:
        default_text_handler = _build_transaction_text_handler(settings)
        telegram_text_handler = default_text_handler
        wechat_text_handler = default_text_handler

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
    application.include_router(
        create_wechat_webhook_router(
            wechat_token=(
                wechat_token if wechat_token is not None else settings.wechat_token
            ),
            wechat_text_handler=wechat_text_handler,
            wechat_location_handler=wechat_location_handler,
        )
    )

    return application


def _build_transaction_text_handler(settings: Settings) -> TelegramTextHandler | None:
    if not _parser_configured(settings):
        return None

    repository = _build_transaction_repository(settings)
    if repository is None:
        return None

    llm_client = OpenAICompatibleLLMClient(
        api_key=settings.parser_api_key,
        model=settings.parser_model,
    )
    service = TransactionService(
        parser=IntentParser(llm_client=llm_client),
        repository=repository,
        exchange_rate_provider=FrankfurterExchangeRateProvider(),
        timezone=settings.default_timezone,
        default_currency=settings.default_currency,
    )
    return service.handle_message


def _parser_configured(settings: Settings) -> bool:
    return all(
        (
            settings.parser_api_key,
            settings.parser_model,
        )
    )


def _build_transaction_repository(
    settings: Settings,
) -> GoogleSheetsTransactionRepository | PostgresTransactionRepository | None:
    if settings.storage_backend == STORAGE_BACKEND_GOOGLE_SHEETS:
        if not settings.google_service_account_json or not settings.google_sheet_id:
            return None
        sheets_client = build_google_sheets_values_client(
            settings.google_service_account_json
        )
        return GoogleSheetsTransactionRepository(
            sheet_id=settings.google_sheet_id,
            sheets_client=sheets_client,
            timezone=settings.default_timezone,
        )

    if settings.storage_backend == STORAGE_BACKEND_POSTGRES:
        if not settings.database_url:
            return None
        return PostgresTransactionRepository(
            database_url=settings.database_url,
            timezone=settings.default_timezone,
        )

    return None


app = create_app()
