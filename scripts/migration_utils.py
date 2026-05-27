from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal

from integrations.google_sheets.repository import TransactionRecord


COMPARISON_FIELDS = (
    "id",
    "date",
    "amount",
    "currency",
    "type",
    "category",
    "merchant",
    "payment_method",
    "note",
    "source_platform",
    "source_user_id",
    "source_chat_id",
    "source_message_id",
    "created_at",
    "updated_at",
)
TIMESTAMP_FIELDS = frozenset({"created_at", "updated_at"})


@dataclass(frozen=True)
class FieldDifference:
    expected: str
    actual: str


def source_key(record: TransactionRecord) -> tuple[str, str, str]:
    return (
        record.source_platform,
        record.source_chat_id,
        record.source_message_id,
    )


def identity_key(record: TransactionRecord) -> tuple[str, str]:
    return (record.source_platform, record.source_user_id)


def record_month(record: TransactionRecord) -> str:
    return record.date[:7]


def records_equivalent(
    expected: TransactionRecord,
    actual: TransactionRecord,
) -> bool:
    return not compare_record_fields(expected, actual)


def compare_record_fields(
    expected: TransactionRecord,
    actual: TransactionRecord,
) -> dict[str, FieldDifference]:
    differences: dict[str, FieldDifference] = {}
    for field_name in COMPARISON_FIELDS:
        expected_value = getattr(expected, field_name)
        actual_value = getattr(actual, field_name)
        if _values_equal(field_name, expected_value, actual_value):
            continue
        differences[field_name] = FieldDifference(
            expected=_display_value(expected_value),
            actual=_display_value(actual_value),
        )
    return differences


def latest_transaction(
    records: list[TransactionRecord],
    *,
    tie_breaker: Literal["first", "last"] = "first",
) -> TransactionRecord | None:
    expenses = [record for record in records if record.type == "expense"]
    if not expenses:
        return None
    latest_timestamp = max(timestamp_sort_key(record.created_at) for record in expenses)
    matching_records = [
        record
        for record in expenses
        if timestamp_sort_key(record.created_at) == latest_timestamp
    ]
    if tie_breaker == "last":
        return matching_records[-1]
    return matching_records[0]


def timestamp_sort_key(value: object) -> datetime:
    parsed = _parse_timestamp(value)
    if parsed is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    return _timestamp_to_utc(parsed)


def _values_equal(field_name: str, expected: object, actual: object) -> bool:
    if field_name == "amount":
        return Decimal(str(expected)) == Decimal(str(actual))
    if field_name in TIMESTAMP_FIELDS:
        expected_timestamp = _parse_timestamp(expected)
        actual_timestamp = _parse_timestamp(actual)
        if expected_timestamp is not None and actual_timestamp is not None:
            return _timestamp_to_utc(expected_timestamp) == _timestamp_to_utc(
                actual_timestamp
            )
    return expected == actual


def _parse_timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _timestamp_to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _display_value(value: Any) -> str:
    if value is None:
        return "<blank>"
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)
