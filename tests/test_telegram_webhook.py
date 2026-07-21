from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.main import create_app
from app.telegram_webhook import UNSUPPORTED_MESSAGE_REPLY
from core.messages import ConversationKind, InboundMessage


WEBHOOK_SECRET = "webhook-secret"


def test_webhook_converts_private_text_message_and_replies():
    reply_client = FakeTelegramReplyClient()
    handled_messages: list[InboundMessage] = []
    received_at = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)
    app = create_app(
        telegram_reply_client=reply_client,
        telegram_webhook_secret=WEBHOOK_SECRET,
        telegram_text_handler=lambda message: handled_messages.append(message)
        or "fixed reply",
    )
    client = TestClient(app)

    response = client.post(
        "/telegram/webhook",
        json={
            "update_id": 1000,
            "message": {
                "message_id": 9001,
                "date": int(received_at.timestamp()),
                "chat": {"id": 12345, "type": "private"},
                "from": {
                    "id": 42,
                    "is_bot": False,
                    "first_name": "Ada",
                    "last_name": "Lovelace",
                    "username": "ada",
                },
                "text": "lunch 12.30",
            },
        },
        headers=secret_headers(),
    )

    assert response.status_code == 200
    assert handled_messages[0].conversation_kind is ConversationKind.PERSONAL
    assert response.json() == {"ok": True, "status": "replied"}
    assert handled_messages == [
        InboundMessage(
            source_platform="telegram",
            source_user_id="42",
            source_chat_id="12345",
            source_message_id="9001",
            message_text="lunch 12.30",
            received_at=received_at,
            source_username="ada",
            source_user_display_name="Ada Lovelace",
            conversation_kind=ConversationKind.PERSONAL,
        )
    ]
    assert reply_client.sent_messages == [
        {
            "chat_id": "12345",
            "text": "fixed reply",
            "reply_to_message_id": "9001",
        }
    ]


def test_webhook_derives_display_name_when_username_is_absent():
    reply_client = FakeTelegramReplyClient()
    handled_messages: list[InboundMessage] = []
    received_at = datetime(2026, 5, 20, 12, 3, tzinfo=timezone.utc)
    app = create_app(
        telegram_reply_client=reply_client,
        telegram_webhook_secret=WEBHOOK_SECRET,
        telegram_text_handler=lambda message: handled_messages.append(message)
        or "fixed reply",
    )
    client = TestClient(app)

    response = client.post(
        "/telegram/webhook",
        json={
            "update_id": 1001,
            "message": {
                "message_id": 9002,
                "date": int(received_at.timestamp()),
                "chat": {"id": 12345, "type": "private"},
                "from": {
                    "id": 42,
                    "is_bot": False,
                    "first_name": "Ada",
                    "last_name": "Lovelace",
                },
                "text": "lunch 12.30",
            },
        },
        headers=secret_headers(),
    )

    assert response.status_code == 200
    assert handled_messages[0].source_username is None
    assert handled_messages[0].source_user_display_name == "Ada Lovelace"


def test_webhook_processes_group_mention_and_strips_bot_username():
    reply_client = FakeTelegramReplyClient()
    handled_messages: list[InboundMessage] = []
    app = create_app(
        telegram_reply_client=reply_client,
        telegram_webhook_secret=WEBHOOK_SECRET,
        telegram_bot_username="ExpenseAgentBot",
        telegram_text_handler=lambda message: handled_messages.append(message)
        or "fixed group reply",
    )
    client = TestClient(app)
    received_at = datetime(2026, 5, 20, 12, 5, tzinfo=timezone.utc)

    response = client.post(
        "/telegram/webhook",
        json={
            "update_id": 1001,
            "message": {
                "message_id": 9002,
                "date": int(received_at.timestamp()),
                "chat": {"id": -100123, "type": "supergroup"},
                "from": {
                    "id": 42,
                    "is_bot": False,
                    "first_name": "Ada",
                    "last_name": "Lovelace",
                    "username": "ada",
                },
                "text": "@ExpenseAgentBot lunch 12.30",
            },
        },
        headers=secret_headers(),
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "status": "replied"}
    assert handled_messages == [
        InboundMessage(
            source_platform="telegram",
            source_user_id="42",
            source_chat_id="-100123",
            source_message_id="9002",
            message_text="lunch 12.30",
            received_at=received_at,
            source_username="ada",
            source_user_display_name="Ada Lovelace",
            conversation_kind=ConversationKind.GROUP,
        )
    ]
    assert reply_client.sent_messages == [
        {
            "chat_id": "-100123",
            "text": "fixed group reply",
            "reply_to_message_id": "9002",
        }
    ]


def test_webhook_ignores_group_messages_without_bot_mention():
    reply_client = FakeTelegramReplyClient()
    app = create_app(
        telegram_reply_client=reply_client,
        telegram_webhook_secret=WEBHOOK_SECRET,
        telegram_bot_username="ExpenseAgentBot",
    )
    client = TestClient(app)
    received_at = datetime(2026, 5, 20, 12, 6, tzinfo=timezone.utc)

    response = client.post(
        "/telegram/webhook",
        json={
            "update_id": 1001,
            "message": {
                "message_id": 9003,
                "date": int(received_at.timestamp()),
                "chat": {"id": -100123, "type": "group"},
                "from": {"id": 42, "is_bot": False, "first_name": "Ada"},
                "text": "lunch 12.30",
            },
        },
        headers=secret_headers(),
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "status": "ignored",
        "reason": "bot_not_mentioned",
    }
    assert reply_client.sent_messages == []


def test_webhook_replies_to_private_non_text_messages_as_unsupported():
    reply_client = FakeTelegramReplyClient()
    app = create_app(
        telegram_reply_client=reply_client,
        telegram_webhook_secret=WEBHOOK_SECRET,
    )
    client = TestClient(app)
    received_at = datetime(2026, 5, 20, 12, 10, tzinfo=timezone.utc)

    response = client.post(
        "/telegram/webhook",
        json={
            "update_id": 1002,
            "message": {
                "message_id": 9003,
                "date": int(received_at.timestamp()),
                "chat": {"id": 12345, "type": "private"},
                "from": {"id": 42, "is_bot": False, "first_name": "Ada"},
                "photo": [{"file_id": "file-1"}],
            },
        },
        headers=secret_headers(),
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "status": "replied",
        "reason": "unsupported_message_type",
    }
    assert reply_client.sent_messages == [
        {
            "chat_id": "12345",
            "text": UNSUPPORTED_MESSAGE_REPLY,
            "reply_to_message_id": "9003",
        }
    ]


def test_webhook_rejects_invalid_secret_before_replying():
    reply_client = FakeTelegramReplyClient()
    app = create_app(
        telegram_reply_client=reply_client,
        telegram_webhook_secret=WEBHOOK_SECRET,
    )
    client = TestClient(app)

    response = client.post(
        "/telegram/webhook",
        json={
            "update_id": 1003,
            "message": {
                "message_id": 9004,
                "date": int(
                    datetime(2026, 5, 20, 12, 15, tzinfo=timezone.utc).timestamp()
                ),
                "chat": {"id": 12345, "type": "private"},
                "from": {"id": 42, "is_bot": False, "first_name": "Ada"},
                "text": "lunch 12.30",
            },
        },
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-secret"},
    )

    assert response.status_code == 401
    assert reply_client.sent_messages == []


def test_webhook_rejects_text_before_handler_when_reply_client_is_not_configured(
    monkeypatch,
):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    handled_messages: list[InboundMessage] = []
    app = create_app(
        telegram_webhook_secret=WEBHOOK_SECRET,
        telegram_text_handler=lambda message: handled_messages.append(message)
        or "fixed reply",
    )
    client = TestClient(app)

    response = client.post(
        "/telegram/webhook",
        json={
            "update_id": 1004,
            "message": {
                "message_id": 9005,
                "date": int(
                    datetime(2026, 5, 20, 12, 20, tzinfo=timezone.utc).timestamp()
                ),
                "chat": {"id": 12345, "type": "private"},
                "from": {"id": 42, "is_bot": False, "first_name": "Ada"},
                "text": "lunch 12.30",
            },
        },
        headers=secret_headers(),
    )

    assert response.status_code == 503
    assert handled_messages == []


def test_webhook_rejects_requests_when_secret_is_not_configured():
    reply_client = FakeTelegramReplyClient()
    app = create_app(
        telegram_reply_client=reply_client,
        telegram_webhook_secret="",
    )
    client = TestClient(app)

    response = client.post(
        "/telegram/webhook",
        json={
            "update_id": 1005,
            "message": {
                "message_id": 9006,
                "date": int(
                    datetime(2026, 5, 20, 12, 25, tzinfo=timezone.utc).timestamp()
                ),
                "chat": {"id": 12345, "type": "private"},
                "from": {"id": 42, "is_bot": False, "first_name": "Ada"},
                "text": "lunch 12.30",
            },
        },
        headers=secret_headers(),
    )

    assert response.status_code == 503
    assert reply_client.sent_messages == []


def secret_headers() -> dict[str, str]:
    return {"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET}


class FakeTelegramReplyClient:
    def __init__(self) -> None:
        self.sent_messages: list[dict[str, str | None]] = []

    def send_message(
        self,
        *,
        chat_id: str,
        text: str,
        reply_to_message_id: str | None = None,
    ) -> None:
        self.sent_messages.append(
            {
                "chat_id": chat_id,
                "text": text,
                "reply_to_message_id": reply_to_message_id,
            }
        )
