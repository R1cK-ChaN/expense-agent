import logging

from fastapi.testclient import TestClient

from app.telegram_webhook import DEFAULT_TEXT_MESSAGE_REPLY
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


def test_create_app_emits_function_batch_outcome_logs(capsys):
    from app import main as app_main

    logger = logging.getLogger("core.function_batch_handler")
    previous_level = logger.level
    previous_handlers = list(logger.handlers)
    previous_propagate = logger.propagate
    logger.handlers.clear()
    logger.setLevel(logging.NOTSET)
    try:
        app_main.create_app()
        assert logger.level == logging.INFO
        assert logger.propagate is False
        logger.info("function_batch_observability_test")
        assert "function_batch_observability_test" in capsys.readouterr().err
    finally:
        logger.handlers.clear()
        logger.handlers.extend(previous_handlers)
        logger.setLevel(previous_level)
        logger.propagate = previous_propagate


def test_configured_app_wires_transaction_service(monkeypatch):
    from app import main as app_main

    sheets_client = InMemorySheetsClient()
    reply_client = FakeTelegramReplyClient()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "telegram-token")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "webhook-secret")
    monkeypatch.setenv("PARSER_API_KEY", "parser-secret")
    monkeypatch.setenv("PARSER_MODEL", "parser-model")
    monkeypatch.setenv("STORAGE_BACKEND", "google_sheets")
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
    assert sheets_client.rows[1][1:15] == [
        "2026-05-20",
        "12.5",
        "SGD",
        "expense",
        "餐饮",
        "麦当劳",
        "",
        "午饭",
        "telegram",
        "42",
        "",
        "Ada",
        "12345",
        "9001",
    ]
    assert sheets_client.rows[1][15] == sheets_client.rows[1][16]
    assert sheets_client.rows[1][15].endswith("+08:00")


def test_spending_query_reads_google_sheet_without_writing(monkeypatch):
    from app import main as app_main

    sheets_client = InMemorySheetsClient()
    sheets_client.rows.extend(
        [
            [
                "txn-1", "2026-05-01", "12.5", "SGD", "expense", "餐饮",
                "", "", "", "telegram", "42", "", "Ada", "12345",
                "8001", "2026-05-01T10:00:00+08:00",
                "2026-05-01T10:00:00+08:00",
            ],
            [
                "txn-2", "2026-05-10", "7.5", "SGD", "expense", "交通",
                "", "", "", "telegram", "42", "", "Ada", "12345",
                "8002", "2026-05-10T10:00:00+08:00",
                "2026-05-10T10:00:00+08:00",
            ],
        ]
    )
    original_rows = [list(row) for row in sheets_client.rows]
    reply_client = FakeTelegramReplyClient()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "telegram-token")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "webhook-secret")
    monkeypatch.setenv("PARSER_API_KEY", "parser-secret")
    monkeypatch.setenv("PARSER_MODEL", "parser-model")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", "{}")
    monkeypatch.setenv("GOOGLE_SHEET_ID", "sheet-1")
    monkeypatch.setattr(app_main, "OpenAICompatibleLLMClient", FakeQueryLLMClient)
    monkeypatch.setattr(
        app_main,
        "build_google_sheets_values_client",
        lambda service_account_json: sheets_client,
    )

    app = app_main.create_app(telegram_reply_client=reply_client)
    response = TestClient(app).post(
        "/telegram/webhook",
        json={
            "update_id": 1001,
            "message": {
                "message_id": 9002,
                "date": 1779251400,
                "chat": {"id": 12345, "type": "private"},
                "from": {"id": 42, "is_bot": False, "first_name": "Ada"},
                "text": "5月花了多少？",
            },
        },
        headers={"X-Telegram-Bot-Api-Secret-Token": "webhook-secret"},
    )

    assert response.status_code == 200
    assert reply_client.sent_messages[0]["text"] == (
        "2026-05-01 至 2026-05-20 支出合计：20.00 SGD\n"
        "分类占比：\n"
        "- 餐饮：12.50 SGD（62.50%）\n"
        "- 交通：7.50 SGD（37.50%）"
    )
    assert sheets_client.rows == original_rows
    assert sheets_client.append_calls == []
    assert sheets_client.update_calls == []


def test_configured_app_wires_postgres_as_authoritative_ledger(monkeypatch):
    from app import main as app_main

    repositories: list[FakePostgresTransactionRepository] = []
    reply_client = FakeTelegramReplyClient()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "telegram-token")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "webhook-secret")
    monkeypatch.setenv("PARSER_API_KEY", "parser-secret")
    monkeypatch.setenv("PARSER_MODEL", "parser-model")
    monkeypatch.setenv("STORAGE_BACKEND", "postgres")
    monkeypatch.setenv("DATABASE_URL", "postgres://user:password@localhost/db")
    monkeypatch.setattr(
        app_main,
        "OpenAICompatibleLLMClient",
        FakeLLMClient,
        raising=False,
    )
    monkeypatch.setattr(
        app_main,
        "PostgresTransactionRepository",
        lambda **kwargs: repositories.append(
            FakePostgresTransactionRepository(**kwargs)
        )
        or repositories[-1],
        raising=False,
    )
    monkeypatch.setattr(
        app_main,
        "build_google_sheets_values_client",
        lambda service_account_json: (_ for _ in ()).throw(
            AssertionError("Google Sheets must not be used by PostgreSQL runtime")
        ),
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
    assert len(repositories) == 1
    assert repositories[0].kwargs == {
        "database_url": "postgres://user:password@localhost/db",
        "timezone": "Asia/Singapore",
    }
    assert len(repositories[0].records) == 1
    assert repositories[0].records[0].source_message_id == "9001"


def test_function_batch_runtime_uses_gpt_5_5_responses_and_postgres(monkeypatch):
    from app import main as app_main

    response_clients = []
    repositories = []
    monkeypatch.setenv("FUNCTION_BATCHES_ENABLED", "true")
    monkeypatch.setenv("AGENT_MODEL", "gpt-5.5")
    monkeypatch.setenv("PARSER_API_KEY", "parser-secret")
    monkeypatch.delenv("PARSER_MODEL", raising=False)
    monkeypatch.setenv("STORAGE_BACKEND", "postgres")
    monkeypatch.setenv("DATABASE_URL", "postgres://user:password@localhost/db")
    monkeypatch.setattr(
        app_main,
        "OpenAIResponsesFunctionClient",
        lambda **kwargs: response_clients.append(kwargs) or FakeFunctionClient(),
    )
    monkeypatch.setattr(
        app_main,
        "PostgresTransactionRepository",
        lambda **kwargs: repositories.append(("ledger", kwargs))
        or FakeStatisticsRepository(),
    )
    monkeypatch.setattr(
        app_main,
        "PostgresFunctionBatchRepository",
        lambda **kwargs: repositories.append(("batch", kwargs))
        or FakeBatchStateRepository(),
    )
    monkeypatch.setattr(
        app_main,
        "PostgresPendingRequestRepository",
        lambda **kwargs: repositories.append(("pending", kwargs))
        or FakePendingStateRepository(),
    )

    handler = app_main._build_transaction_text_handler(app_main.load_settings())

    assert handler is not None
    assert response_clients == [{"api_key": "parser-secret", "model": "gpt-5.5"}]
    assert [name for name, _kwargs in repositories] == [
        "ledger",
        "batch",
        "pending",
    ]


def test_postgres_backend_without_database_url_preserves_health(monkeypatch):
    from app import main as app_main

    reply_client = FakeTelegramReplyClient()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "telegram-token")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "webhook-secret")
    monkeypatch.setenv("PARSER_API_KEY", "parser-secret")
    monkeypatch.setenv("PARSER_MODEL", "parser-model")
    monkeypatch.setenv("STORAGE_BACKEND", "postgres")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(
        app_main,
        "PostgresTransactionRepository",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("PostgreSQL repository should not be built")
        ),
        raising=False,
    )

    app = app_main.create_app(telegram_reply_client=reply_client)
    client = TestClient(app)

    assert client.get("/health").status_code == 200
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
            "text": DEFAULT_TEXT_MESSAGE_REPLY,
            "reply_to_message_id": "9001",
        }
    ]


def test_missing_google_sheet_settings_preserve_health(monkeypatch):
    from app import main as app_main

    reply_client = FakeTelegramReplyClient()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "telegram-token")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "webhook-secret")
    monkeypatch.setenv("PARSER_API_KEY", "parser-secret")
    monkeypatch.setenv("PARSER_MODEL", "parser-model")
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_JSON", raising=False)
    monkeypatch.delenv("GOOGLE_SHEET_ID", raising=False)

    app = app_main.create_app(telegram_reply_client=reply_client)
    client = TestClient(app)

    assert client.get("/health").status_code == 200
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
            "text": DEFAULT_TEXT_MESSAGE_REPLY,
            "reply_to_message_id": "9001",
        }
    ]


def test_custom_telegram_handler_does_not_build_default_wechat_handler(monkeypatch):
    from app import main as app_main

    monkeypatch.setenv("PARSER_API_KEY", "parser-secret")
    monkeypatch.setenv("PARSER_MODEL", "parser-model")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", "{}")
    monkeypatch.setenv("GOOGLE_SHEET_ID", "sheet-1")
    monkeypatch.setattr(
        app_main,
        "build_google_sheets_values_client",
        lambda service_account_json: (_ for _ in ()).throw(
            AssertionError("default transaction handler should not be built")
        ),
        raising=False,
    )
    reply_client = FakeTelegramReplyClient()

    app = app_main.create_app(
        telegram_reply_client=reply_client,
        telegram_webhook_secret="webhook-secret",
        telegram_text_handler=lambda message: "custom reply",
    )
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
    assert reply_client.sent_messages[0]["text"] == "custom reply"


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


class FakeQueryLLMClient:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs

    def complete_json(self, *, system_prompt: str, user_prompt: str) -> str:
        return """{
            "intent": "query_monthly_total",
            "confidence": 0.9,
            "expense": null,
            "update_fields": {},
            "query": {
                "start_date": "2026-05-01",
                "end_date": "2026-05-20",
                "currency": "SGD"
            },
            "missing_fields": []
        }"""


class FakeFunctionClient:
    def select_functions(self, **kwargs):
        raise AssertionError("model should not be called during wiring")


class FakeStatisticsRepository:
    pass


class FakeBatchStateRepository:
    pass


class FakePendingStateRepository:
    def get(self, **kwargs):
        return None

    def upsert(self, request):
        pass

    def delete(self, **kwargs):
        pass


class InMemorySheetsClient:
    def __init__(self) -> None:
        self.rows = [transaction_header_row()]
        self.append_calls: list[list[list[str]]] = []
        self.update_calls: list[list[list[str]]] = []

    def get_values(self, spreadsheet_id: str, range_name: str) -> list[list[str]]:
        return [list(row) for row in self.rows]

    def append_values(
        self,
        spreadsheet_id: str,
        range_name: str,
        values: list[list[str]],
    ) -> None:
        self.append_calls.append(values)
        self.rows.extend(list(row) for row in values)

    def update_values(
        self,
        spreadsheet_id: str,
        range_name: str,
        values: list[list[str]],
    ) -> None:
        self.update_calls.append(values)
        raise AssertionError("update_values should not be called")


class FakePostgresTransactionRepository:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.records: list[object] = []

    def find_by_source_message(
        self,
        *,
        source_platform: str,
        user_id: str,
        chat_id: str,
        message_id: str,
    ) -> None:
        return None

    def get_latest_transaction(
        self,
        *,
        source_platform: str,
        user_id: str,
    ) -> None:
        return None

    def append_transaction(self, record: object) -> object:
        self.records.append(record)
        return record


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
