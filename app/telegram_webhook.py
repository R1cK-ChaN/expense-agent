import hmac
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from fastapi import APIRouter, Header, HTTPException


UNSUPPORTED_MESSAGE_REPLY = (
    "Sorry, I can only handle private text messages right now."
)
DEFAULT_TEXT_MESSAGE_REPLY = (
    "I received your message. Expense parsing is not enabled yet."
)


@dataclass(frozen=True)
class TelegramInboundMessage:
    telegram_user_id: str
    chat_id: str
    message_id: str
    message_text: str
    received_at: datetime


class TelegramReplyClient(Protocol):
    def send_message(
        self,
        *,
        chat_id: str,
        text: str,
        reply_to_message_id: str | None = None,
    ) -> None:
        raise NotImplementedError


TelegramTextHandler = Callable[[TelegramInboundMessage], str]


def create_telegram_webhook_router(
    *,
    telegram_reply_client: TelegramReplyClient | None,
    telegram_webhook_secret: str | None,
    telegram_text_handler: TelegramTextHandler | None = None,
) -> APIRouter:
    router = APIRouter()
    text_handler = telegram_text_handler or _default_text_handler

    @router.post("/telegram/webhook")
    def telegram_webhook(
        update: dict[str, Any],
        telegram_secret_token: str | None = Header(
            default=None,
            alias="X-Telegram-Bot-Api-Secret-Token",
        ),
    ) -> dict[str, object]:
        _verify_webhook_secret(telegram_webhook_secret, telegram_secret_token)

        message = _mapping_value(update.get("message"))
        if message is None:
            return _ignored_response("unsupported_update")

        chat = _mapping_value(message.get("chat"))
        if chat is None:
            return _ignored_response("invalid_message")

        if _string_value(chat.get("type")) != "private":
            return _ignored_response("unsupported_chat_type")

        chat_id = _string_value(chat.get("id"))
        message_id = _string_value(message.get("message_id"))
        if chat_id is None or message_id is None:
            return _ignored_response("invalid_message")

        text = message.get("text")
        if not isinstance(text, str) or text == "":
            _send_reply(
                telegram_reply_client,
                chat_id=chat_id,
                text=UNSUPPORTED_MESSAGE_REPLY,
                reply_to_message_id=message_id,
            )
            return {
                "ok": True,
                "status": "replied",
                "reason": "unsupported_message_type",
            }

        inbound_message = _inbound_message_from_telegram(message, chat_id, message_id)
        if inbound_message is None:
            return _ignored_response("invalid_message")

        _require_reply_client(telegram_reply_client)
        _send_reply(
            telegram_reply_client,
            chat_id=inbound_message.chat_id,
            text=text_handler(inbound_message),
            reply_to_message_id=inbound_message.message_id,
        )
        return {"ok": True, "status": "replied"}

    return router


def _default_text_handler(message: TelegramInboundMessage) -> str:
    return DEFAULT_TEXT_MESSAGE_REPLY


def _verify_webhook_secret(
    configured_secret: str | None,
    supplied_secret: str | None,
) -> None:
    if not configured_secret:
        raise HTTPException(
            status_code=503,
            detail="Telegram webhook secret is not configured.",
        )

    if supplied_secret is None or not hmac.compare_digest(
        supplied_secret,
        configured_secret,
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid Telegram webhook secret.",
        )


def _inbound_message_from_telegram(
    message: Mapping[str, Any],
    chat_id: str,
    message_id: str,
) -> TelegramInboundMessage | None:
    from_user = _mapping_value(message.get("from"))
    text = message.get("text")
    telegram_user_id = (
        _string_value(from_user.get("id")) if from_user is not None else None
    )
    received_at = _telegram_timestamp(message.get("date"))

    if telegram_user_id is None or received_at is None or not isinstance(text, str):
        return None

    return TelegramInboundMessage(
        telegram_user_id=telegram_user_id,
        chat_id=chat_id,
        message_id=message_id,
        message_text=text,
        received_at=received_at,
    )


def _send_reply(
    telegram_reply_client: TelegramReplyClient | None,
    *,
    chat_id: str,
    text: str,
    reply_to_message_id: str,
) -> None:
    reply_client = _require_reply_client(telegram_reply_client)

    try:
        reply_client.send_message(
            chat_id=chat_id,
            text=text,
            reply_to_message_id=reply_to_message_id,
        )
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail="Failed to send Telegram reply.",
        ) from error


def _require_reply_client(
    telegram_reply_client: TelegramReplyClient | None,
) -> TelegramReplyClient:
    if telegram_reply_client is None:
        raise HTTPException(
            status_code=503,
            detail="Telegram bot token is not configured.",
        )
    return telegram_reply_client


def _ignored_response(reason: str) -> dict[str, object]:
    return {"ok": True, "status": "ignored", "reason": reason}


def _mapping_value(value: object) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    return None


def _string_value(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _telegram_timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value), timezone.utc)
    except (OverflowError, TypeError, ValueError):
        return None
