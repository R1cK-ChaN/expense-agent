from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
from typing import Any

import pytest

from core.statistics import StatisticsQueryScope, StatisticsScopeMode

from integrations.google_sheets.repository import (
    InvalidTransactionUpdateError,
    TransactionNotFoundError,
    TransactionRecord,
    TransactionRepositoryError,
)
from integrations.postgres.repository import PostgresTransactionRepository
from integrations.postgres.repository import (
    SELECT_CONVERSATION_TRANSACTIONS_BY_DATE_RANGE_SQL,
    SELECT_RECENT_CONVERSATION_TRANSACTIONS_SQL,
    SELECT_TRANSACTION_BY_MESSAGE_ID_SQL,
    SELECT_TRANSACTION_BY_SOURCE_MESSAGE_SQL,
)


ROOT = Path(__file__).resolve().parents[1]


def test_legacy_duplicate_queries_also_recognize_function_batch_transactions():
    for sql in (
        SELECT_TRANSACTION_BY_MESSAGE_ID_SQL,
        SELECT_TRANSACTION_BY_SOURCE_MESSAGE_SQL,
    ):
        assert "from function_call_batches b" in sql
        assert "b.inbound_message_id = m.id" in sql


def test_append_transaction_persists_message_transaction_and_creation_event_atomically():
    database = InMemoryPostgresDatabase()
    repository = make_repository(database)

    record = make_record(amount=Decimal("12.30"))

    assert repository.append_transaction(record) == record

    assert len(database.users) == 1
    assert len(database.identities) == 1
    assert len(database.inbound_messages) == 1
    assert len(database.transactions) == 1
    assert len(database.transaction_events) == 1
    assert database.commits == 1
    assert database.rollbacks == 0

    message = next(iter(database.inbound_messages.values()))
    assert message["platform"] == "telegram"
    assert message["platform_chat_id"] == "12345"
    assert message["platform_message_id"] == "9001"
    assert message["provider_dedupe_key"] == "9001"
    assert message["provider_message_type"] == "text"

    transaction = next(iter(database.transactions.values()))
    assert transaction["external_id"] == "txn-1"
    assert transaction["created_from_message_id"] == message["id"]
    assert transaction["amount"] == Decimal("12.30")

    event = database.transaction_events[0]
    assert event["transaction_id"] == transaction["id"]
    assert event["message_id"] == message["id"]
    assert event["event_type"] == "created"
    assert event["old_values"] is None
    assert event["new_values"]["id"] == "txn-1"
    assert event["new_values"]["amount"] == "12.30"


def test_find_by_source_message_returns_existing_transaction_after_append():
    database = InMemoryPostgresDatabase()
    repository = make_repository(database)
    repository.append_transaction(make_record())

    record = repository.find_by_source_message(
        source_platform="telegram",
        user_id="42",
        chat_id="12345",
        message_id="9001",
    )

    assert record == make_record()


def test_duplicate_append_returns_existing_transaction_without_creating_another_row():
    database = InMemoryPostgresDatabase()
    repository = make_repository(database)
    repository.append_transaction(make_record(transaction_id="txn-1"))

    record = repository.append_transaction(
        make_record(transaction_id="txn-race", amount=Decimal("99.00"))
    )

    assert record == make_record(transaction_id="txn-1")
    assert len(database.inbound_messages) == 1
    assert len(database.transactions) == 1
    assert len(database.transaction_events) == 1


def test_append_transaction_reuses_identity_and_updates_display_metadata():
    database = InMemoryPostgresDatabase()
    repository = make_repository(database)
    repository.append_transaction(make_record(source_username="ada"))

    repository.append_transaction(
        make_record(
            transaction_id="txn-2",
            source_message_id="9002",
            source_username="ada-updated",
            source_user_display_name="Ada Updated",
        )
    )

    assert len(database.users) == 1
    assert len(database.identities) == 1
    identity = next(iter(database.identities.values()))
    assert identity["username"] == "ada-updated"
    assert identity["display_name"] == "Ada Updated"


def test_get_latest_transaction_returns_newest_expense_for_internal_user_identity():
    database = InMemoryPostgresDatabase()
    repository = make_repository(database)
    repository.append_transaction(
        make_record(
            transaction_id="old-expense",
            source_message_id="9001",
            created_at="2026-05-19T10:00:00+00:00",
            updated_at="2026-05-19T10:00:00+00:00",
        )
    )
    repository.append_transaction(
        make_record(
            transaction_id="other-user-expense",
            source_user_id="7",
            source_message_id="9002",
            created_at="2026-05-21T12:00:00+00:00",
            updated_at="2026-05-21T12:00:00+00:00",
        )
    )
    repository.append_transaction(
        make_record(
            transaction_id="new-expense",
            source_message_id="9003",
            created_at="2026-05-20T12:00:00+00:00",
            updated_at="2026-05-20T12:00:00+00:00",
        )
    )

    record = repository.get_latest_transaction(
        source_platform="telegram",
        user_id="42",
    )

    assert record is not None
    assert record.id == "new-expense"


def test_queries_and_updates_include_imported_transactions_without_source_message():
    database = InMemoryPostgresDatabase()
    database.seed_imported_transaction(
        external_id="imported-expense",
        transaction_date="2026-05-06",
        amount=Decimal("21.00"),
        created_at="2026-05-22T10:00:00+00:00",
        updated_at="2026-05-22T10:00:00+00:00",
    )
    repository = make_repository(database)

    latest_record = repository.get_latest_transaction(
        source_platform="telegram",
        user_id="42",
    )
    monthly_records = repository.list_monthly_expenses(
        source_platform="telegram",
        user_id="42",
        month="2026-05",
    )
    updated_record = repository.update_transaction(
        "imported-expense",
        {"note": "corrected import"},
    )

    assert latest_record is not None
    assert latest_record.id == "imported-expense"
    assert [record.id for record in monthly_records] == ["imported-expense"]
    assert updated_record.note == "corrected import"
    assert updated_record.source_platform == "telegram"
    assert updated_record.source_user_id == "42"
    assert updated_record.source_chat_id == ""
    assert updated_record.source_message_id == ""


def test_records_return_timestamps_in_repository_timezone():
    database = InMemoryPostgresDatabase()
    database.seed_imported_transaction(
        external_id="imported-expense",
        created_at=datetime(2026, 5, 19, 10, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 5, 20, 12, 30, tzinfo=timezone.utc),
    )
    repository = make_repository(database)

    record = repository.get_latest_transaction(
        source_platform="telegram",
        user_id="42",
    )

    assert record is not None
    assert record.created_at == "2026-05-19T18:00:00+08:00"
    assert record.updated_at == "2026-05-20T20:30:00+08:00"


def test_update_transaction_changes_allowed_fields_and_inserts_update_event():
    database = InMemoryPostgresDatabase()
    repository = make_repository(database)
    repository.append_transaction(make_record(amount=Decimal("12.30"), note="lunch"))

    record = repository.update_transaction(
        "txn-1",
        {
            "amount": Decimal("15.50"),
            "note": "corrected lunch",
        },
    )

    assert record.amount == Decimal("15.50")
    assert record.note == "corrected lunch"
    assert record.created_at == "2026-05-19T10:00:00+00:00"
    assert record.updated_at == "2026-05-20T20:30:00+08:00"

    assert len(database.transaction_events) == 2
    event = database.transaction_events[-1]
    assert event["event_type"] == "updated"
    assert event["message_id"] is None
    assert event["old_values"]["amount"] == "12.30"
    assert event["new_values"]["amount"] == "15.50"
    assert event["old_values"]["note"] == "lunch"
    assert event["new_values"]["note"] == "corrected lunch"


def test_update_transaction_rejects_disallowed_fields():
    database = InMemoryPostgresDatabase()
    repository = make_repository(database)
    repository.append_transaction(make_record())

    with pytest.raises(InvalidTransactionUpdateError):
        repository.update_transaction("txn-1", {"source_user_id": "7"})


def test_update_transaction_raises_not_found_for_missing_transaction():
    repository = make_repository(InMemoryPostgresDatabase())

    with pytest.raises(TransactionNotFoundError):
        repository.update_transaction("missing", {"note": "fixed"})


def test_list_monthly_expenses_filters_user_type_and_month_across_currencies():
    database = InMemoryPostgresDatabase()
    repository = make_repository(database)
    repository.append_transaction(
        make_record(
            transaction_id="sgd-1",
            source_message_id="9001",
            amount=Decimal("10.50"),
            currency="SGD",
            date="2026-05-01",
        )
    )
    repository.append_transaction(
        make_record(
            transaction_id="april",
            source_message_id="9002",
            amount=Decimal("4.00"),
            currency="SGD",
            date="2026-04-30",
        )
    )
    repository.append_transaction(
        make_record(
            transaction_id="other-user",
            source_user_id="7",
            source_message_id="9003",
            amount=Decimal("8.00"),
            currency="SGD",
            date="2026-05-03",
        )
    )
    repository.append_transaction(
        make_record(
            transaction_id="usd",
            source_message_id="9004",
            amount=Decimal("6.00"),
            currency="USD",
            date="2026-05-04",
        )
    )
    repository.append_transaction(
        make_record(
            transaction_id="cny",
            source_message_id="9005",
            amount=Decimal("30.00"),
            currency="CNY",
            date="2026-05-05",
        )
    )

    records = repository.list_monthly_expenses(
        source_platform="telegram",
        user_id="42",
        month="2026-05",
    )

    assert [(record.id, record.amount, record.currency) for record in records] == [
        ("sgd-1", Decimal("10.50"), "SGD"),
        ("usd", Decimal("6.00"), "USD"),
        ("cny", Decimal("30.00"), "CNY"),
    ]


def test_list_expenses_filters_inclusive_date_range():
    database = InMemoryPostgresDatabase()
    repository = make_repository(database)
    for transaction_id, transaction_date in (
        ("before", "2026-05-09"),
        ("start", "2026-05-10"),
        ("end", "2026-05-20"),
        ("after", "2026-05-21"),
    ):
        repository.append_transaction(
            make_record(
                transaction_id=transaction_id,
                source_message_id=transaction_id,
                date=transaction_date,
            )
        )

    records = repository.list_expenses(
        scope=StatisticsQueryScope(
            mode=StatisticsScopeMode.PERSONAL,
            source_platform="telegram",
            source_user_id="42",
            source_chat_id="12345",
        ),
        start_date="2026-05-10",
        end_date="2026-05-20",
    )

    assert [record.id for record in records] == ["start", "end"]


def test_list_expenses_preserves_legacy_personal_query_interface():
    database = InMemoryPostgresDatabase()
    repository = make_repository(database)
    repository.append_transaction(
        make_record(transaction_id="requester", source_message_id="requester")
    )
    repository.append_transaction(
        make_record(
            transaction_id="other-user",
            source_user_id="7",
            source_message_id="other-user",
        )
    )

    records = repository.list_expenses(
        source_platform="telegram",
        user_id="42",
        start_date="2026-05-01",
        end_date="2026-05-31",
    )

    assert [record.id for record in records] == ["requester"]


def test_conversation_queries_constrain_platform_and_chat_across_members():
    database = InMemoryPostgresDatabase()
    repository = make_repository(database)
    for transaction_id, user_id, platform, chat_id in (
        ("requester", "42", "telegram", "group-1"),
        ("other-member", "7", "telegram", "group-1"),
        ("other-chat", "8", "telegram", "group-2"),
        ("other-platform", "9", "wechat", "group-1"),
    ):
        repository.append_transaction(
            make_record(
                transaction_id=transaction_id,
                source_user_id=user_id,
                source_platform=platform,
                source_chat_id=chat_id,
                source_message_id=transaction_id,
                date="2026-07-10",
            )
        )

    scope = StatisticsQueryScope(
        mode=StatisticsScopeMode.CONVERSATION,
        source_platform="telegram",
        source_user_id="42",
        source_chat_id="group-1",
    )
    records = repository.list_expenses(
        scope=scope,
        start_date="2026-07-01",
        end_date="2026-07-21",
    )
    recent = repository.list_recent_expenses(
        scope=scope,
        category=None,
        merchant=None,
        limit=20,
    )

    assert {record.id for record in records} == {"requester", "other-member"}
    assert {record.id for record in recent} == {"requester", "other-member"}


def test_conversation_sql_supports_legacy_and_function_batch_origins():
    for sql in (
        SELECT_CONVERSATION_TRANSACTIONS_BY_DATE_RANGE_SQL,
        SELECT_RECENT_CONVERSATION_TRANSACTIONS_SQL,
    ):
        assert "t.created_from_message_id" in sql
        assert "t.function_batch_id" in sql
        assert "coalesce(m.platform, batch_m.platform)" in sql
        assert "coalesce(m.platform_chat_id, batch_m.platform_chat_id)" in sql


def test_list_transactions_returns_all_records_for_backfill_verification():
    database = InMemoryPostgresDatabase()
    repository = make_repository(database)
    repository.append_transaction(
        make_record(transaction_id="txn-1", source_message_id="9001")
    )
    repository.append_transaction(
        make_record(transaction_id="txn-2", source_message_id="9002")
    )

    records = repository.list_transactions()

    assert [record.id for record in records] == ["txn-1", "txn-2"]


def test_list_monthly_expenses_rejects_non_padded_month():
    repository = make_repository(InMemoryPostgresDatabase())

    with pytest.raises(ValueError, match="YYYY-MM"):
        repository.list_monthly_expenses(
            source_platform="telegram",
            user_id="42",
            month="2026-5",
        )


def test_append_transaction_rolls_back_and_maps_database_failures():
    database = InMemoryPostgresDatabase(
        fail_on={"postgres_repository.insert_transaction_event"}
    )
    repository = make_repository(database)

    with pytest.raises(TransactionRepositoryError) as error:
        repository.append_transaction(make_record())

    assert isinstance(error.value.__cause__, RuntimeError)
    assert database.users == {}
    assert database.identities == {}
    assert database.inbound_messages == {}
    assert database.transactions == {}
    assert database.transaction_events == []
    assert database.commits == 0
    assert database.rollbacks == 1


def test_find_by_source_message_maps_database_failures():
    database = InMemoryPostgresDatabase(
        fail_on={"postgres_repository.select_transaction_by_source_message"}
    )
    repository = make_repository(database)

    with pytest.raises(TransactionRepositoryError) as error:
        repository.find_by_source_message(
            source_platform="telegram",
            user_id="42",
            chat_id="12345",
            message_id="9001",
        )

    assert isinstance(error.value.__cause__, RuntimeError)


def test_transactions_schema_preserves_domain_external_id():
    sql = (ROOT / "migrations" / "0002_add_transaction_external_id.sql").read_text()

    assert "add column external_id text" in sql
    assert "set external_id = id::text" in sql
    assert "alter column external_id set not null" in sql
    assert "transactions_external_id_key unique (external_id)" in sql


def make_repository(database: "InMemoryPostgresDatabase") -> PostgresTransactionRepository:
    return PostgresTransactionRepository(
        connection_factory=database.connect,
        timezone="Asia/Singapore",
        clock=lambda: datetime(2026, 5, 20, 12, 30, tzinfo=timezone.utc),
        uuid_factory=SequentialIds(),
    )


def make_record(
    *,
    transaction_id: str = "txn-1",
    date: str = "2026-05-19",
    amount: Decimal = Decimal("12.30"),
    currency: str = "SGD",
    transaction_type: str = "expense",
    category: str = "餐饮",
    merchant: str | None = "coffee shop",
    payment_method: str | None = "card",
    note: str | None = "lunch",
    source_platform: str = "telegram",
    source_user_id: str = "42",
    source_username: str | None = "ada",
    source_user_display_name: str | None = "Ada Lovelace",
    source_chat_id: str = "12345",
    source_message_id: str = "9001",
    created_at: str = "2026-05-19T10:00:00+00:00",
    updated_at: str = "2026-05-19T10:00:00+00:00",
) -> TransactionRecord:
    return TransactionRecord(
        id=transaction_id,
        date=date,
        amount=amount,
        currency=currency,
        type=transaction_type,
        category=category,
        merchant=merchant,
        payment_method=payment_method,
        note=note,
        source_platform=source_platform,
        source_user_id=source_user_id,
        source_username=source_username,
        source_user_display_name=source_user_display_name,
        source_chat_id=source_chat_id,
        source_message_id=source_message_id,
        created_at=created_at,
        updated_at=updated_at,
    )


class SequentialIds:
    def __init__(self) -> None:
        self._value = 0

    def __call__(self) -> str:
        self._value += 1
        return f"00000000-0000-0000-0000-{self._value:012d}"


class InMemoryPostgresDatabase:
    def __init__(self, *, fail_on: set[str] | None = None) -> None:
        self.fail_on = fail_on or set()
        self.users: dict[str, dict[str, Any]] = {}
        self.identities: dict[str, dict[str, Any]] = {}
        self.inbound_messages: dict[str, dict[str, Any]] = {}
        self.transactions: dict[str, dict[str, Any]] = {}
        self.transaction_events: list[dict[str, Any]] = []
        self.commits = 0
        self.rollbacks = 0
        self.calls: list[str] = []

    def connect(self) -> "InMemoryPostgresConnection":
        return InMemoryPostgresConnection(self)

    def snapshot(self) -> dict[str, Any]:
        return {
            "users": deepcopy(self.users),
            "identities": deepcopy(self.identities),
            "inbound_messages": deepcopy(self.inbound_messages),
            "transactions": deepcopy(self.transactions),
            "transaction_events": deepcopy(self.transaction_events),
        }

    def restore(self, snapshot: dict[str, Any]) -> None:
        self.users = snapshot["users"]
        self.identities = snapshot["identities"]
        self.inbound_messages = snapshot["inbound_messages"]
        self.transactions = snapshot["transactions"]
        self.transaction_events = snapshot["transaction_events"]

    def seed_imported_transaction(
        self,
        *,
        external_id: str,
        platform: str = "telegram",
        platform_user_id: str = "42",
        username: str | None = "ada",
        display_name: str | None = "Ada Lovelace",
        transaction_date: str = "2026-05-19",
        amount: Decimal = Decimal("12.30"),
        currency: str = "SGD",
        transaction_type: str = "expense",
        category: str = "餐饮",
        merchant: str | None = "coffee shop",
        payment_method: str | None = "card",
        note: str | None = "imported",
        created_at: str | datetime = "2026-05-19T10:00:00+00:00",
        updated_at: str | datetime = "2026-05-19T10:00:00+00:00",
    ) -> None:
        user_id = "import-user"
        identity_id = "import-identity"
        self.users[user_id] = {
            "id": user_id,
            "created_at": created_at,
            "updated_at": updated_at,
        }
        self.identities[identity_id] = {
            "id": identity_id,
            "user_id": user_id,
            "platform": platform,
            "platform_user_id": platform_user_id,
            "username": username,
            "display_name": display_name,
            "created_at": created_at,
            "updated_at": updated_at,
        }
        self.transactions["import-transaction"] = {
            "id": "import-transaction",
            "external_id": external_id,
            "user_id": user_id,
            "created_from_message_id": None,
            "transaction_date": transaction_date,
            "amount": amount,
            "currency": currency,
            "transaction_type": transaction_type,
            "category": category,
            "merchant": merchant,
            "payment_method": payment_method,
            "note": note,
            "created_at": created_at,
            "updated_at": updated_at,
        }


class InMemoryPostgresConnection:
    def __init__(self, database: InMemoryPostgresDatabase) -> None:
        self.database = database
        self._snapshot: dict[str, Any] | None = None

    def __enter__(self) -> "InMemoryPostgresConnection":
        self._snapshot = self.database.snapshot()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if exc_type is None:
            self.database.commits += 1
            return
        assert self._snapshot is not None
        self.database.restore(self._snapshot)
        self.database.rollbacks += 1

    def execute(
        self,
        query: str,
        params: dict[str, Any] | None = None,
    ) -> "InMemoryCursor":
        params = params or {}
        operation = _operation_name(query)
        self.database.calls.append(operation)
        if operation in self.database.fail_on:
            raise RuntimeError(f"{operation} failed")

        rows = getattr(self, _method_name(operation))(params)
        return InMemoryCursor(rows)

    def _postgres_repository_select_identity(
        self,
        params: dict[str, Any],
    ) -> list[dict[str, Any]]:
        identity = self._find_identity(params["platform"], params["platform_user_id"])
        return [] if identity is None else [identity]

    def _postgres_repository_upsert_identity(
        self,
        params: dict[str, Any],
    ) -> list[dict[str, Any]]:
        identity = self._find_identity(params["platform"], params["platform_user_id"])
        if identity is not None:
            identity["username"] = params["username"]
            identity["display_name"] = params["display_name"]
            identity["updated_at"] = params["updated_at"]
            return [{"id": identity["id"], "user_id": identity["user_id"]}]

        self.database.users[params["user_id"]] = {
            "id": params["user_id"],
            "created_at": params["created_at"],
            "updated_at": params["updated_at"],
        }
        self.database.identities[params["identity_id"]] = {
            "id": params["identity_id"],
            "user_id": params["user_id"],
            "platform": params["platform"],
            "platform_user_id": params["platform_user_id"],
            "username": params["username"],
            "display_name": params["display_name"],
            "created_at": params["created_at"],
            "updated_at": params["updated_at"],
        }
        return [{"id": params["identity_id"], "user_id": params["user_id"]}]

    def _postgres_repository_insert_user(
        self,
        params: dict[str, Any],
    ) -> list[dict[str, Any]]:
        self.database.users[params["id"]] = {
            "id": params["id"],
            "created_at": params["created_at"],
            "updated_at": params["updated_at"],
        }
        return []

    def _postgres_repository_insert_identity(
        self,
        params: dict[str, Any],
    ) -> list[dict[str, Any]]:
        self.database.identities[params["id"]] = {
            "id": params["id"],
            "user_id": params["user_id"],
            "platform": params["platform"],
            "platform_user_id": params["platform_user_id"],
            "username": params["username"],
            "display_name": params["display_name"],
            "created_at": params["created_at"],
            "updated_at": params["updated_at"],
        }
        return []

    def _postgres_repository_update_identity(
        self,
        params: dict[str, Any],
    ) -> list[dict[str, Any]]:
        identity = self.database.identities[params["id"]]
        identity["username"] = params["username"]
        identity["display_name"] = params["display_name"]
        identity["updated_at"] = params["updated_at"]
        return []

    def _postgres_repository_insert_inbound_message(
        self,
        params: dict[str, Any],
    ) -> list[dict[str, Any]]:
        existing = self._find_message(
            params["platform"],
            params["platform_chat_id"],
            params["provider_dedupe_key"],
        )
        if existing is not None:
            return [{"id": existing["id"]}]

        self.database.inbound_messages[params["id"]] = {
            "id": params["id"],
            "user_id": params["user_id"],
            "identity_id": params["identity_id"],
            "platform": params["platform"],
            "platform_chat_id": params["platform_chat_id"],
            "platform_message_id": params["platform_message_id"],
            "provider_dedupe_key": params["provider_dedupe_key"],
            "provider_message_type": params["provider_message_type"],
            "provider_event_type": params["provider_event_type"],
            "normalized_text": params["normalized_text"],
            "received_at": params["received_at"],
        }
        return [{"id": params["id"]}]

    def _postgres_repository_select_transaction_by_message_id(
        self,
        params: dict[str, Any],
    ) -> list[dict[str, Any]]:
        transaction = self._find_transaction_by_message_id(params["message_id"])
        return [] if transaction is None else [self._transaction_row(transaction)]

    def _postgres_repository_insert_transaction(
        self,
        params: dict[str, Any],
    ) -> list[dict[str, Any]]:
        self.database.transactions[params["id"]] = {
            "id": params["id"],
            "external_id": params["external_id"],
            "user_id": params["user_id"],
            "created_from_message_id": params["created_from_message_id"],
            "transaction_date": params["transaction_date"],
            "amount": params["amount"],
            "currency": params["currency"],
            "transaction_type": params["transaction_type"],
            "category": params["category"],
            "merchant": params["merchant"],
            "payment_method": params["payment_method"],
            "note": params["note"],
            "created_at": params["created_at"],
            "updated_at": params["updated_at"],
        }
        return [{"id": params["id"]}]

    def _postgres_repository_insert_transaction_event(
        self,
        params: dict[str, Any],
    ) -> list[dict[str, Any]]:
        self.database.transaction_events.append(
            {
                "id": params["id"],
                "transaction_id": params["transaction_id"],
                "message_id": params["message_id"],
                "event_type": params["event_type"],
                "old_values": _decode_jsonb_param(params["old_values"]),
                "new_values": _decode_jsonb_param(params["new_values"]),
                "created_at": params["created_at"],
            }
        )
        return []

    def _postgres_repository_select_transaction_by_source_message(
        self,
        params: dict[str, Any],
    ) -> list[dict[str, Any]]:
        message = self._find_message(
            params["platform"],
            params["platform_chat_id"],
            params["provider_dedupe_key"],
        )
        if message is None:
            return []
        identity = self.database.identities[message["identity_id"]]
        if identity["platform_user_id"] != params["platform_user_id"]:
            return []
        transaction = self._find_transaction_by_message_id(message["id"])
        return [] if transaction is None else [self._transaction_row(transaction)]

    def _postgres_repository_select_latest_transaction(
        self,
        params: dict[str, Any],
    ) -> list[dict[str, Any]]:
        identity = self._find_identity(params["platform"], params["platform_user_id"])
        if identity is None:
            return []
        transactions = [
            transaction
            for transaction in self.database.transactions.values()
            if transaction["user_id"] == identity["user_id"]
            and transaction["transaction_type"] == "expense"
        ]
        transactions.sort(key=lambda row: _sort_timestamp(row["created_at"]), reverse=True)
        return [] if not transactions else [self._transaction_row(transactions[0])]

    def _postgres_repository_select_transaction_for_update(
        self,
        params: dict[str, Any],
    ) -> list[dict[str, Any]]:
        transaction = self._find_transaction_by_external_id(params["external_id"])
        return [] if transaction is None else [self._transaction_row(transaction)]

    def _postgres_repository_update_transaction(
        self,
        params: dict[str, Any],
    ) -> list[dict[str, Any]]:
        transaction = self._find_transaction_by_external_id(params["external_id"])
        assert transaction is not None
        for field_name, value in params["fields"].items():
            transaction[field_name] = value
        transaction["updated_at"] = params["updated_at"]
        return [self._transaction_row(transaction)]

    def _postgres_repository_select_monthly_transactions(
        self,
        params: dict[str, Any],
    ) -> list[dict[str, Any]]:
        identity = self._find_identity(params["platform"], params["platform_user_id"])
        if identity is None:
            return []
        rows = [
            self._transaction_row(transaction)
            for transaction in self.database.transactions.values()
            if transaction["user_id"] == identity["user_id"]
            and transaction["transaction_type"] == "expense"
            and params["month_start"]
            <= transaction["transaction_date"]
            < params["month_end"]
        ]
        rows.sort(key=lambda row: (row["date"], _sort_timestamp(row["created_at"])))
        return rows

    def _postgres_repository_select_transactions_by_date_range(
        self,
        params: dict[str, Any],
    ) -> list[dict[str, Any]]:
        identity = self._find_identity(params["platform"], params["platform_user_id"])
        if identity is None:
            return []
        rows = [
            self._transaction_row(transaction)
            for transaction in self.database.transactions.values()
            if transaction["user_id"] == identity["user_id"]
            and transaction["transaction_type"] == "expense"
            and params["start_date"]
            <= transaction["transaction_date"]
            <= params["end_date"]
        ]
        rows.sort(key=lambda row: (row["date"], _sort_timestamp(row["created_at"])))
        return rows

    def _postgres_repository_select_conversation_transactions_by_date_range(
        self,
        params: dict[str, Any],
    ) -> list[dict[str, Any]]:
        rows = [
            self._transaction_row(transaction)
            for transaction in self.database.transactions.values()
            if transaction["transaction_type"] == "expense"
            and params["start_date"]
            <= transaction["transaction_date"]
            <= params["end_date"]
            and self._transaction_matches_conversation(transaction, params)
        ]
        rows.sort(key=lambda row: (row["date"], _sort_timestamp(row["created_at"])))
        return rows

    def _postgres_repository_select_recent_conversation_transactions(
        self,
        params: dict[str, Any],
    ) -> list[dict[str, Any]]:
        rows = [
            self._transaction_row(transaction)
            for transaction in self.database.transactions.values()
            if transaction["transaction_type"] == "expense"
            and self._transaction_matches_conversation(transaction, params)
            and (
                params["category"] is None
                or transaction["category"] == params["category"]
            )
            and (
                params["merchant"] is None
                or params["merchant"].lower()
                in (transaction["merchant"] or "").lower()
            )
        ]
        rows.sort(
            key=lambda row: (row["date"], _sort_timestamp(row["created_at"])),
            reverse=True,
        )
        return rows[: params["limit"]]

    def _transaction_matches_conversation(
        self,
        transaction: dict[str, Any],
        params: dict[str, Any],
    ) -> bool:
        message_id = transaction["created_from_message_id"]
        if message_id is None:
            return False
        message = self.database.inbound_messages[message_id]
        return (
            message["platform"] == params["platform"]
            and message["platform_chat_id"] == params["platform_chat_id"]
        )

    def _postgres_repository_select_all_transactions(
        self,
        params: dict[str, Any],
    ) -> list[dict[str, Any]]:
        rows = [
            self._transaction_row(transaction)
            for transaction in self.database.transactions.values()
        ]
        rows.sort(key=lambda row: (row["date"], _sort_timestamp(row["created_at"])))
        return rows

    def _find_identity(
        self,
        platform: str,
        platform_user_id: str,
    ) -> dict[str, Any] | None:
        for identity in self.database.identities.values():
            if (
                identity["platform"] == platform
                and identity["platform_user_id"] == platform_user_id
            ):
                return identity
        return None

    def _find_message(
        self,
        platform: str,
        platform_chat_id: str,
        provider_dedupe_key: str,
    ) -> dict[str, Any] | None:
        for message in self.database.inbound_messages.values():
            if (
                message["platform"] == platform
                and message["platform_chat_id"] == platform_chat_id
                and message["provider_dedupe_key"] == provider_dedupe_key
            ):
                return message
        return None

    def _find_transaction_by_message_id(
        self,
        message_id: str,
    ) -> dict[str, Any] | None:
        for transaction in self.database.transactions.values():
            if transaction["created_from_message_id"] == message_id:
                return transaction
        return None

    def _find_transaction_by_external_id(
        self,
        external_id: str,
    ) -> dict[str, Any] | None:
        for transaction in self.database.transactions.values():
            if transaction["external_id"] == external_id:
                return transaction
        return None

    def _transaction_row(self, transaction: dict[str, Any]) -> dict[str, Any]:
        message_id = transaction["created_from_message_id"]
        if message_id is None:
            identity = self._find_identity_by_user_id(transaction["user_id"])
            assert identity is not None
            return {
                "id": transaction["external_id"],
                "date": transaction["transaction_date"],
                "amount": transaction["amount"],
                "currency": transaction["currency"],
                "type": transaction["transaction_type"],
                "category": transaction["category"],
                "merchant": transaction["merchant"],
                "payment_method": transaction["payment_method"],
                "note": transaction["note"],
                "source_platform": identity["platform"],
                "source_user_id": identity["platform_user_id"],
                "source_username": identity["username"],
                "source_user_display_name": identity["display_name"],
                "source_chat_id": "",
                "source_message_id": "",
                "created_at": transaction["created_at"],
                "updated_at": transaction["updated_at"],
                "database_id": transaction["id"],
            }

        message = self.database.inbound_messages[message_id]
        identity = self.database.identities[message["identity_id"]]
        return {
            "id": transaction["external_id"],
            "date": transaction["transaction_date"],
            "amount": transaction["amount"],
            "currency": transaction["currency"],
            "type": transaction["transaction_type"],
            "category": transaction["category"],
            "merchant": transaction["merchant"],
            "payment_method": transaction["payment_method"],
            "note": transaction["note"],
            "source_platform": message["platform"],
            "source_user_id": identity["platform_user_id"],
            "source_username": identity["username"],
            "source_user_display_name": identity["display_name"],
            "source_chat_id": message["platform_chat_id"],
            "source_message_id": message["platform_message_id"],
            "created_at": transaction["created_at"],
            "updated_at": transaction["updated_at"],
            "database_id": transaction["id"],
        }

    def _find_identity_by_user_id(self, user_id: str) -> dict[str, Any] | None:
        for identity in self.database.identities.values():
            if identity["user_id"] == user_id:
                return identity
        return None


class InMemoryCursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def fetchone(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self._rows)


def _operation_name(query: str) -> str:
    for line in query.splitlines():
        line = line.strip()
        if line:
            return line.removeprefix("-- ")
    raise AssertionError("query is missing operation comment")


def _method_name(operation: str) -> str:
    return "_" + operation.replace(".", "_")


def _sort_timestamp(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _decode_jsonb_param(value: str | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return json.loads(value)
