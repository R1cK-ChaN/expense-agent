from decimal import Decimal

import pytest

from integrations.google_sheets.repository import TransactionRecord
from scripts.backfill_google_sheets_to_postgres import (
    BackfillPreflightError,
    backfill_records,
)


def test_backfill_records_imports_missing_transactions_in_execute_mode():
    target = FakeTargetRepository()
    records = [
        make_record(transaction_id="txn-1", source_message_id="9001"),
        make_record(
            transaction_id="txn-2",
            source_user_id="7",
            source_message_id="9002",
        ),
    ]

    result = backfill_records(records, target, dry_run=False)

    assert result.total == 2
    assert result.imported == 2
    assert result.existing == 0
    assert result.pending == 0
    assert result.dry_run is False
    assert target.appended == records


def test_backfill_records_dry_run_reports_pending_without_writes():
    target = FakeTargetRepository()
    records = [make_record(transaction_id="txn-1")]

    result = backfill_records(records, target, dry_run=True)

    assert result.total == 1
    assert result.imported == 0
    assert result.pending == 1
    assert result.dry_run is True
    assert target.appended == []


def test_backfill_records_skips_existing_equivalent_rows_for_idempotency():
    record = make_record(transaction_id="txn-1")
    target = FakeTargetRepository(existing=[record])

    result = backfill_records([record], target, dry_run=False)

    assert result.total == 1
    assert result.imported == 0
    assert result.existing == 1
    assert target.appended == []


def test_backfill_records_ignores_existing_mutable_identity_display_names():
    source = make_record(
        transaction_id="txn-1",
        source_username="old-name",
        source_user_display_name="Old Name",
    )
    existing = make_record(
        transaction_id="txn-1",
        source_username="new-name",
        source_user_display_name="New Name",
    )
    target = FakeTargetRepository(existing=[existing])

    result = backfill_records([source], target, dry_run=False)

    assert result.imported == 0
    assert result.existing == 1
    assert target.appended == []


def test_backfill_records_rejects_conflicting_existing_source_message():
    source = make_record(transaction_id="txn-1", amount=Decimal("12.30"))
    existing = make_record(transaction_id="txn-1", amount=Decimal("99.00"))
    target = FakeTargetRepository(existing=[existing])

    with pytest.raises(BackfillPreflightError) as error:
        backfill_records([source], target, dry_run=False)

    assert "conflicts with existing PostgreSQL transaction" in str(error.value)
    assert target.appended == []


def test_backfill_records_rejects_duplicate_sheet_transaction_ids_before_writes():
    target = FakeTargetRepository()
    records = [
        make_record(transaction_id="txn-1", source_message_id="9001"),
        make_record(transaction_id="txn-1", source_message_id="9002"),
    ]

    with pytest.raises(BackfillPreflightError) as error:
        backfill_records(records, target, dry_run=False)

    assert "duplicate transaction id" in str(error.value)
    assert target.appended == []


def test_backfill_records_rejects_duplicate_provider_message_key_before_writes():
    target = FakeTargetRepository()
    records = [
        make_record(
            transaction_id="txn-1",
            source_user_id="42",
            source_message_id="9001",
        ),
        make_record(
            transaction_id="txn-2",
            source_user_id="7",
            source_message_id="9001",
        ),
    ]

    with pytest.raises(BackfillPreflightError) as error:
        backfill_records(records, target, dry_run=False)

    assert "duplicate source message tuple" in str(error.value)
    assert target.appended == []


def test_backfill_records_rejects_existing_transaction_id_conflict_before_writes():
    source = make_record(
        transaction_id="txn-1",
        source_message_id="9001",
        amount=Decimal("12.30"),
    )
    existing = make_record(
        transaction_id="txn-1",
        source_message_id="9002",
        amount=Decimal("99.00"),
    )
    target = FakeTargetRepository(existing=[existing])

    with pytest.raises(BackfillPreflightError) as error:
        backfill_records([source], target, dry_run=False)

    assert "transaction id txn-1" in str(error.value)
    assert target.appended == []


def test_backfill_records_rejects_rows_without_source_message_metadata():
    target = FakeTargetRepository()
    records = [make_record(source_message_id="")]

    with pytest.raises(BackfillPreflightError) as error:
        backfill_records(records, target, dry_run=False)

    assert "missing source message metadata" in str(error.value)
    assert target.appended == []


class FakeTargetRepository:
    def __init__(self, existing: list[TransactionRecord] | None = None) -> None:
        self.appended: list[TransactionRecord] = []
        self._records_by_source = {
            _source_key(record): record for record in existing or []
        }
        self._records_by_id = {record.id: record for record in existing or []}

    def list_transactions(self) -> list[TransactionRecord]:
        return list(self._records_by_id.values())

    def append_transaction(self, record: TransactionRecord) -> TransactionRecord:
        self.appended.append(record)
        self._records_by_source[_source_key(record)] = record
        self._records_by_id[record.id] = record
        return record


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


def _source_key(record: TransactionRecord) -> tuple[str, str, str]:
    return (
        record.source_platform,
        record.source_chat_id,
        record.source_message_id,
    )
