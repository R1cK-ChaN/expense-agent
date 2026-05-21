from fastapi.testclient import TestClient

from integrations.google_sheets.schema import transaction_header_row


def test_asgi_app_imports_without_external_credentials():
    from app.main import app

    assert app.title == "Expense Agent"


def test_health_endpoint_returns_service_identity():
    from app.main import app

    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "expense-agent",
    }


def test_configured_app_wires_transaction_service(monkeypatch):
    from app import main as app_main

    sheets_client = InMemorySheetsClient()
    reply_client = FakeTelegramReplyClient()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "telegram-token")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "webhook-secret")
    monkeypatch.setenv("PARSER_API_KEY", "parser-secret")
    monkeypatch.setenv("PARSER_MODEL", "parser-model")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", "{}")
    monkeypatch.setenv("GOOGLE_SHEET_ID", "sheet-1")
    monkeypatch.setattr(
        app_main,
        "OpenAICompatibleLLMClient",
        FakeLLMClient,
        raising=False,
    )
    monkeypatch.setattr(
        app_main,
        "build_google_sheets_values_client",
        lambda service_account_json: sheets_client,
        raising=False,
    )

    app = app_main.create_app(telegram_reply_client=reply_client)
    client = TestClient(app)
    response = client.post(
        "/telegram/webhook",
        json={
            "update_id": 1000,
            "message": {
                "message_id": 9001,
                "date": 1779251400,
                "chat": {"id": 12345, "type": "private"},
                "from": {"id": 42, "is_bot": False, "first_name": "Ada"},
                "text": "午饭 12.5 麦当劳",
            },
        },
        headers={"X-Telegram-Bot-Api-Secret-Token": "webhook-secret"},
    )

    assert response.status_code == 200
    assert reply_client.sent_messages == [
        {
            "chat_id": "12345",
            "text": "已记录：2026-05-20 餐饮 12.5 SGD 麦当劳",
            "reply_to_message_id": "9001",
        }
    ]
    assert sheets_client.rows[1][1:11] == [
        "2026-05-20",
        "12.5",
        "SGD",
        "expense",
        "餐饮",
        "麦当劳",
        "",
        "午饭",
        "42",
        "9001",
    ]


class FakeLLMClient:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs

    def complete_json(self, *, system_prompt: str, user_prompt: str) -> str:
        return """{
            "intent": "create_expense",
            "confidence": 0.9,
            "expense": {
                "date": "2026-05-20",
                "amount": "12.5",
                "currency": "SGD",
                "category": "餐饮",
                "merchant": "麦当劳",
                "payment_method": null,
                "note": "午饭"
            },
            "update_fields": {},
            "query": null,
            "missing_fields": []
        }"""


class InMemorySheetsClient:
    def __init__(self) -> None:
        self.rows = [transaction_header_row()]

    def get_values(self, spreadsheet_id: str, range_name: str) -> list[list[str]]:
        return [list(row) for row in self.rows]

    def append_values(
        self,
        spreadsheet_id: str,
        range_name: str,
        values: list[list[str]],
    ) -> None:
        self.rows.extend(list(row) for row in values)

    def update_values(
        self,
        spreadsheet_id: str,
        range_name: str,
        values: list[list[str]],
    ) -> None:
        raise AssertionError("update_values should not be called")


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
