import json
from collections.abc import Mapping
from typing import Protocol
from urllib import error, request


TELEGRAM_API_BASE_URL = "https://api.telegram.org"


class TelegramClientError(Exception):
    """Raised when the Telegram Bot API client cannot complete a request."""


class JsonTransport(Protocol):
    def post_json(
        self,
        url: str,
        payload: dict[str, object],
    ) -> Mapping[str, object]:
        raise NotImplementedError


class TelegramBotClient:
    def __init__(
        self,
        *,
        bot_token: str,
        transport: JsonTransport | None = None,
        api_base_url: str = TELEGRAM_API_BASE_URL,
    ) -> None:
        if not bot_token:
            raise ValueError("bot_token must be configured")

        self._bot_token = bot_token
        self._transport = transport or UrllibJsonTransport()
        self._api_base_url = api_base_url.rstrip("/")

    def send_message(
        self,
        *,
        chat_id: str,
        text: str,
        reply_to_message_id: int | str | None = None,
    ) -> None:
        payload: dict[str, object] = {
            "chat_id": chat_id,
            "text": text,
        }
        if reply_to_message_id is not None:
            payload["reply_to_message_id"] = _integer_value(
                "reply_to_message_id",
                reply_to_message_id,
            )

        try:
            response = self._transport.post_json(
                self._method_url("sendMessage"),
                payload,
            )
        except Exception:
            raise TelegramClientError("Telegram sendMessage failed.") from None

        if response.get("ok") is not True:
            raise TelegramClientError("Telegram sendMessage failed.")

    def _method_url(self, method_name: str) -> str:
        return f"{self._api_base_url}/bot{self._bot_token}/{method_name}"


def _integer_value(field_name: str, value: int | str) -> int:
    if isinstance(value, bool):
        raise TelegramClientError(f"{field_name} must be an integer.")
    try:
        return int(value)
    except (TypeError, ValueError):
        raise TelegramClientError(f"{field_name} must be an integer.") from None


class UrllibJsonTransport:
    def __init__(self, *, timeout_seconds: float = 10.0) -> None:
        self._timeout_seconds = timeout_seconds

    def post_json(
        self,
        url: str,
        payload: dict[str, object],
    ) -> Mapping[str, object]:
        request_body = json.dumps(payload).encode("utf-8")
        telegram_request = request.Request(
            url,
            data=request_body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with request.urlopen(
                telegram_request,
                timeout=self._timeout_seconds,
            ) as response:
                response_body = response.read().decode("utf-8")
        except error.URLError:
            raise TelegramClientError("Telegram API request failed.") from None

        try:
            decoded_response = json.loads(response_body)
        except json.JSONDecodeError:
            raise TelegramClientError("Telegram API returned invalid JSON.") from None

        if not isinstance(decoded_response, Mapping):
            raise TelegramClientError("Telegram API returned invalid JSON.")
        return decoded_response
