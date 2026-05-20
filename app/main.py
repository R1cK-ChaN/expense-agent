from fastapi import FastAPI

from app.telegram_webhook import (
    TelegramReplyClient,
    TelegramTextHandler,
    create_telegram_webhook_router,
)
from config.settings import load_settings
from integrations.telegram_client import TelegramBotClient


def create_app(
    *,
    telegram_reply_client: TelegramReplyClient | None = None,
    telegram_webhook_secret: str | None = None,
    telegram_text_handler: TelegramTextHandler | None = None,
) -> FastAPI:
    settings = load_settings()
    application = FastAPI(title="Expense Agent")
    if telegram_reply_client is None and settings.telegram_bot_token is not None:
        telegram_reply_client = TelegramBotClient(
            bot_token=settings.telegram_bot_token,
        )

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
            telegram_text_handler=telegram_text_handler,
        )
    )

    return application


app = create_app()
