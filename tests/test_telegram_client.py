import pytest

from integrations.telegram_client import TelegramBotClient, TelegramClientError


def test_telegram_client_posts_send_message_to_configured_bot_endpoint():
    transport = FakeJsonTransport({"ok": True})
    client = TelegramBotClient(bot_token="telegram-secret", transport=transport)

    client.send_message(
        chat_id="12345",
        text="hello",
        reply_to_message_id="9001",
    )

    assert transport.requests == [
        (
            "https://api.telegram.org/bottelegram-secret/sendMessage",
            {
                "chat_id": "12345",
                "text": "hello",
                "reply_to_message_id": 9001,
            },
        )
    ]


def test_telegram_client_omits_reply_to_message_id_when_unset():
    transport = FakeJsonTransport({"ok": True})
    client = TelegramBotClient(bot_token="telegram-secret", transport=transport)

    client.send_message(chat_id="12345", text="hello")

    assert transport.requests == [
        (
            "https://api.telegram.org/bottelegram-secret/sendMessage",
            {
                "chat_id": "12345",
                "text": "hello",
            },
        )
    ]


def test_telegram_client_maps_failed_api_responses_to_client_error():
    transport = FakeJsonTransport({"ok": False, "description": "chat not found"})
    client = TelegramBotClient(bot_token="telegram-secret", transport=transport)

    with pytest.raises(TelegramClientError, match="Telegram sendMessage failed"):
        client.send_message(chat_id="12345", text="hello")


def test_telegram_client_rejects_non_numeric_reply_to_message_id():
    transport = FakeJsonTransport({"ok": True})
    client = TelegramBotClient(bot_token="telegram-secret", transport=transport)

    with pytest.raises(TelegramClientError, match="reply_to_message_id"):
        client.send_message(
            chat_id="12345",
            text="hello",
            reply_to_message_id="not-a-number",
        )

    assert transport.requests == []


class FakeJsonTransport:
    def __init__(self, response: dict[str, object]) -> None:
        self._response = response
        self.requests: list[tuple[str, dict[str, object]]] = []

    def post_json(self, url: str, payload: dict[str, object]) -> dict[str, object]:
        self.requests.append((url, dict(payload)))
        return self._response
