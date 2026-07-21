import logging

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
from core.function_batch_executor import FunctionBatchExecutor
from core.function_batch_handler import FunctionBatchHandler
from core.function_selector import FunctionSelector
from core.pending_requests import PendingRequestService
from core.statistics import StatisticsService
from core.transaction_service import TransactionService
from integrations.exchange_rates import FrankfurterExchangeRateProvider
from integrations.google_sheets.repository import (
    GoogleSheetsTransactionRepository,
    build_google_sheets_values_client,
)
from integrations.llm_client import (
    OpenAICompatibleLLMClient,
    OpenAIResponsesFunctionClient,
)
from integrations.postgres.function_batch_repository import (
    PostgresFunctionBatchRepository,
    PostgresPendingRequestRepository,
)
from integrations.postgres.repository import PostgresTransactionRepository
from integrations.telegram_client import TelegramBotClient


def _configure_function_batch_logging() -> None:
    outcome_logger = logging.getLogger("core.function_batch_handler")
    outcome_logger.setLevel(logging.INFO)
    if not outcome_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(levelname)s %(name)s %(message)s")
        )
        outcome_logger.addHandler(handler)
    outcome_logger.propagate = False


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
    _configure_function_batch_logging()
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
    if settings.function_batches_enabled:
        return _build_function_batch_text_handler(settings)

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


def _build_function_batch_text_handler(
    settings: Settings,
) -> TelegramTextHandler | None:
    if (
        settings.storage_backend != STORAGE_BACKEND_POSTGRES
        or not settings.database_url
        or not settings.parser_api_key
    ):
        return None

    ledger_repository = PostgresTransactionRepository(
        database_url=settings.database_url,
        timezone=settings.default_timezone,
    )
    batch_repository = PostgresFunctionBatchRepository(
        database_url=settings.database_url,
    )
    pending_requests = PendingRequestService(
        repository=PostgresPendingRequestRepository(
            database_url=settings.database_url,
        )
    )
    exchange_rates = FrankfurterExchangeRateProvider()
    executor = FunctionBatchExecutor(
        repository=batch_repository,
        statistics=StatisticsService(
            repository=ledger_repository,
            currency=settings.default_currency,
            exchange_rate_provider=exchange_rates,
        ),
        pending_requests=pending_requests,
        timezone=settings.default_timezone,
        default_currency=settings.default_currency,
        id_factory=_new_transaction_id,
    )
    handler = FunctionBatchHandler(
        selector=FunctionSelector(
            llm_client=OpenAIResponsesFunctionClient(
                api_key=settings.parser_api_key,
                model=settings.agent_model,
            )
        ),
        executor=executor,
        repository=batch_repository,
        pending_requests=pending_requests,
        timezone=settings.default_timezone,
        default_currency=settings.default_currency,
    )
    return handler.handle_message


def _new_transaction_id() -> str:
    from uuid import uuid4

    return f"txn-{uuid4()}"


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
    """Build the selected authoritative ledger for bot reads and writes."""

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

    # Unsupported values disable transaction handling while preserving health.
    # Deployment validation rejects unsupported production configuration.
    return None


app = create_app()
