from datetime import datetime, timezone
from decimal import Decimal

from core.function_batch_executor import CreateExpenseCommand
from core.messages import InboundMessage
from integrations.google_sheets.repository import TransactionRecord
from integrations.postgres.function_batch_repository import (
    PostgresFunctionBatchRepository,
)


def test_begin_batch_claims_provider_message_and_persists_accepted_calls():
    connection = ScriptedConnection(
        [
            {"id": "identity-1", "user_id": "user-1"},
            {"id": "message-1"},
            None,
            {"id": "batch-1", "reply_text": None, "is_new": True},
        ]
    )
    repository = PostgresFunctionBatchRepository(
        connection_factory=lambda: connection,
        uuid_factory=SequentialIds(),
    )

    result = repository.begin_batch(
        message(),
        ({"function": "record_expense", "arguments": {"amount": "12.50"}},),
    )

    assert result.batch_id == "batch-1"
    assert result.stored_reply is None
    assert result.is_new is True
    assert connection.operations == [
        "postgres_repository.upsert_identity",
        "postgres_repository.insert_inbound_message",
        "function_batch_repository.select_legacy_transaction",
        "function_batch_repository.begin_batch",
    ]
    assert connection.commits == 1


def test_execute_writes_commits_all_creates_and_events_in_one_transaction():
    connection = ScriptedConnection(
        [
            transaction_row("db-1", "txn-1", Decimal("12.50")),
            None,
            transaction_row("db-2", "txn-2", Decimal("2.20")),
            None,
            None,
        ]
    )
    repository = PostgresFunctionBatchRepository(
        connection_factory=lambda: connection,
        uuid_factory=SequentialIds(),
    )
    commands = (
        CreateExpenseCommand(call_index=0, record=record("txn-1", "12.50")),
        CreateExpenseCommand(call_index=1, record=record("txn-2", "2.20")),
    )

    result = repository.execute_writes("batch-1", commands)

    assert list(result) == [0, 1]
    assert connection.operations == [
        "function_batch_repository.insert_transaction",
        "postgres_repository.insert_transaction_event",
        "function_batch_repository.insert_transaction",
        "postgres_repository.insert_transaction_event",
        "function_batch_repository.mark_writes_committed",
    ]
    assert connection.commits == 1
    assert connection.rollbacks == 0


def test_begin_batch_replays_legacy_transaction_instead_of_creating_batch():
    connection = ScriptedConnection(
        [
            {"id": "identity-1", "user_id": "user-1"},
            {"id": "message-1"},
            {
                "date": "2026-07-21",
                "amount": Decimal("12.50"),
                "currency": "SGD",
                "category": "餐饮",
                "merchant": "Toast Box",
                "note": None,
            },
        ]
    )
    repository = PostgresFunctionBatchRepository(
        connection_factory=lambda: connection,
        uuid_factory=SequentialIds(),
    )

    result = repository.begin_batch(
        message(),
        ({"function": "record_expense", "arguments": {"amount": "99"}},),
    )

    assert result.stored_reply == "已记录：2026-07-21 餐饮 12.50 SGD Toast Box"
    assert "function_batch_repository.begin_batch" not in connection.operations


def test_accept_calls_completes_selection_claim_once():
    connection = ScriptedConnection([{"id": "batch-1"}])
    repository = PostgresFunctionBatchRepository(
        connection_factory=lambda: connection,
        uuid_factory=SequentialIds(),
    )

    repository.accept_calls(
        "batch-1",
        ({"function": "record_expense", "arguments": {"amount": "12.50"}},),
    )

    assert connection.operations == ["function_batch_repository.accept_calls"]
    assert connection.commits == 1


def test_execute_writes_rolls_back_every_create_when_one_insert_fails():
    connection = ScriptedConnection(
        [transaction_row("db-1", "txn-1", Decimal("12.50")), None],
        fail_on_call=3,
    )
    repository = PostgresFunctionBatchRepository(
        connection_factory=lambda: connection,
        uuid_factory=SequentialIds(),
    )

    try:
        repository.execute_writes(
            "batch-1",
            (
                CreateExpenseCommand(0, record("txn-1", "12.50")),
                CreateExpenseCommand(1, record("txn-2", "2.20")),
            ),
        )
    except Exception:
        pass

    assert connection.commits == 0
    assert connection.rollbacks == 1


def message() -> InboundMessage:
    return InboundMessage(
        source_platform="telegram",
        source_user_id="42",
        source_chat_id="100",
        source_message_id="9001",
        message_text="test",
        received_at=datetime(2026, 7, 21, 2, 0, tzinfo=timezone.utc),
        source_username="ada",
        source_user_display_name="Ada",
    )


def record(transaction_id: str, amount: str) -> TransactionRecord:
    return TransactionRecord(
        id=transaction_id,
        date="2026-07-21",
        amount=Decimal(amount),
        currency="SGD",
        type="expense",
        category="餐饮",
        merchant=None,
        payment_method=None,
        note=None,
        source_platform="telegram",
        source_user_id="42",
        source_username="ada",
        source_user_display_name="Ada",
        source_chat_id="100",
        source_message_id="9001",
        created_at="2026-07-21T10:00:00+08:00",
        updated_at="2026-07-21T10:00:00+08:00",
    )


def transaction_row(database_id: str, external_id: str, amount: Decimal):
    return {
        "database_id": database_id,
        "id": external_id,
        "date": "2026-07-21",
        "amount": amount,
        "currency": "SGD",
        "type": "expense",
        "category": "餐饮",
        "merchant": None,
        "payment_method": None,
        "note": None,
        "created_at": "2026-07-21T10:00:00+08:00",
        "updated_at": "2026-07-21T10:00:00+08:00",
    }


class ScriptedConnection:
    def __init__(self, rows, *, fail_on_call=None):
        self.rows = list(rows)
        self.fail_on_call = fail_on_call
        self.operations = []
        self.commits = 0
        self.rollbacks = 0
        self.call_count = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.commits += 1
        else:
            self.rollbacks += 1

    def execute(self, query, params=None):
        self.call_count += 1
        if self.call_count == self.fail_on_call:
            raise RuntimeError("database failure")
        marker = next(line for line in query.splitlines() if line.startswith("-- "))
        self.operations.append(marker[3:])
        row = self.rows.pop(0) if self.rows else None
        return Cursor(row)


class Cursor:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row

    def fetchall(self):
        return [] if self.row is None else [self.row]


class SequentialIds:
    def __init__(self):
        self.value = 0

    def __call__(self):
        self.value += 1
        return f"00000000-0000-0000-0000-{self.value:012d}"
