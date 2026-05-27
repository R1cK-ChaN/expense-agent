#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from integrations.google_sheets.repository import (  # noqa: E402
    GoogleSheetsTransactionRepository,
    TransactionRecord,
    build_google_sheets_values_client,
)
from integrations.postgres.repository import PostgresTransactionRepository  # noqa: E402
from scripts.migration_utils import (  # noqa: E402
    FieldDifference,
    compare_record_fields,
    identity_key,
    latest_transaction,
    record_month,
)


@dataclass(frozen=True)
class RecordMismatch:
    transaction_id: str
    field_differences: Mapping[str, FieldDifference]


@dataclass(frozen=True)
class AggregateMismatch:
    key: tuple[str, ...]
    expected: str
    actual: str


@dataclass(frozen=True)
class VerificationReport:
    source_count: int
    target_count: int
    missing_ids: tuple[str, ...]
    extra_ids: tuple[str, ...]
    record_mismatches: tuple[RecordMismatch, ...]
    monthly_total_mismatches: tuple[AggregateMismatch, ...]
    currency_count_mismatches: tuple[AggregateMismatch, ...]
    category_count_mismatches: tuple[AggregateMismatch, ...]
    merchant_count_mismatches: tuple[AggregateMismatch, ...]
    latest_transaction_mismatches: tuple[AggregateMismatch, ...]

    @property
    def is_success(self) -> bool:
        return not any(
            (
                self.missing_ids,
                self.extra_ids,
                self.record_mismatches,
                self.monthly_total_mismatches,
                self.currency_count_mismatches,
                self.category_count_mismatches,
                self.merchant_count_mismatches,
                self.latest_transaction_mismatches,
            )
        )


def compare_backfill_records(
    source_records: Sequence[TransactionRecord],
    target_records: Sequence[TransactionRecord],
) -> VerificationReport:
    source_by_id = _records_by_id(source_records)
    target_by_id = _records_by_id(target_records)
    source_ids = set(source_by_id)
    target_ids = set(target_by_id)

    record_mismatches: list[RecordMismatch] = []
    for transaction_id in sorted(source_ids & target_ids):
        differences = compare_record_fields(
            source_by_id[transaction_id],
            target_by_id[transaction_id],
        )
        if differences:
            record_mismatches.append(
                RecordMismatch(
                    transaction_id=transaction_id,
                    field_differences=differences,
                )
            )

    return VerificationReport(
        source_count=len(source_records),
        target_count=len(target_records),
        missing_ids=tuple(sorted(source_ids - target_ids)),
        extra_ids=tuple(sorted(target_ids - source_ids)),
        record_mismatches=tuple(record_mismatches),
        monthly_total_mismatches=_aggregate_mismatches(
            _monthly_totals(source_records),
            _monthly_totals(target_records),
        ),
        currency_count_mismatches=_aggregate_mismatches(
            _count_by(source_records, lambda record: (record.currency,)),
            _count_by(target_records, lambda record: (record.currency,)),
        ),
        category_count_mismatches=_aggregate_mismatches(
            _count_by(source_records, lambda record: (record.category,)),
            _count_by(target_records, lambda record: (record.category,)),
        ),
        merchant_count_mismatches=_aggregate_mismatches(
            _count_by(
                source_records,
                lambda record: (record.merchant or "<blank>",),
            ),
            _count_by(
                target_records,
                lambda record: (record.merchant or "<blank>",),
            ),
        ),
        latest_transaction_mismatches=_aggregate_mismatches(
            _latest_transaction_ids(source_records, tie_breaker="first"),
            _latest_transaction_ids(target_records, tie_breaker="last"),
        ),
    )


def format_verification_report(report: VerificationReport) -> str:
    status = "verification passed" if report.is_success else "verification failed"
    lines = [
        f"{status}: {report.source_count} Google Sheets row(s), "
        f"{report.target_count} PostgreSQL row(s)"
    ]
    if report.missing_ids:
        lines.append("missing transaction ids: " + ", ".join(report.missing_ids))
    if report.extra_ids:
        lines.append(
            "extra PostgreSQL transaction ids: " + ", ".join(report.extra_ids)
        )
    if report.record_mismatches:
        lines.append(
            f"record mismatches: {len(report.record_mismatches)} transaction(s)"
        )
    _append_aggregate_summary(
        lines,
        "monthly total mismatches",
        report.monthly_total_mismatches,
    )
    _append_aggregate_summary(
        lines,
        "currency count mismatches",
        report.currency_count_mismatches,
    )
    _append_aggregate_summary(
        lines,
        "category count mismatches",
        report.category_count_mismatches,
    )
    _append_aggregate_summary(
        lines,
        "merchant count mismatches",
        report.merchant_count_mismatches,
    )
    _append_aggregate_summary(
        lines,
        "latest transaction mismatches",
        report.latest_transaction_mismatches,
    )
    return "\n".join(lines)


def load_google_sheet_records(
    *,
    sheet_id: str,
    service_account_json: str,
    timezone: str,
) -> list[TransactionRecord]:
    sheets_client = build_google_sheets_values_client(service_account_json)
    repository = GoogleSheetsTransactionRepository(
        sheet_id=sheet_id,
        sheets_client=sheets_client,
        timezone=timezone,
    )
    return repository.list_transactions()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify Google Sheets to PostgreSQL transaction backfill.",
    )
    parser.add_argument(
        "--sheet-id",
        default=os.environ.get("GOOGLE_SHEET_ID"),
        help="Google Sheet ID. Defaults to GOOGLE_SHEET_ID.",
    )
    parser.add_argument(
        "--service-account-json",
        default=os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON"),
        help="Google service account JSON. Defaults to GOOGLE_SERVICE_ACCOUNT_JSON.",
    )
    parser.add_argument(
        "--service-account-json-file",
        type=Path,
        help="Path to a Google service account JSON file.",
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="PostgreSQL connection URL. Defaults to DATABASE_URL.",
    )
    parser.add_argument(
        "--timezone",
        default=os.environ.get("DEFAULT_TIMEZONE", "Asia/Singapore"),
        help="Repository timezone for timestamp comparison.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.sheet_id:
        parser.error("--sheet-id or GOOGLE_SHEET_ID is required")
    if not args.database_url:
        parser.error("--database-url or DATABASE_URL is required")

    service_account_json = _service_account_json(args)
    if not service_account_json:
        parser.error(
            "--service-account-json, --service-account-json-file, "
            "or GOOGLE_SERVICE_ACCOUNT_JSON is required"
        )

    source_records = load_google_sheet_records(
        sheet_id=args.sheet_id,
        service_account_json=service_account_json,
        timezone=args.timezone,
    )
    target_records = PostgresTransactionRepository(
        database_url=args.database_url,
        timezone=args.timezone,
    ).list_transactions()

    report = compare_backfill_records(source_records, target_records)
    print(format_verification_report(report))
    return 0 if report.is_success else 1


def _records_by_id(
    records: Sequence[TransactionRecord],
) -> dict[str, TransactionRecord]:
    return {record.id: record for record in records}


def _monthly_totals(
    records: Sequence[TransactionRecord],
) -> dict[tuple[str, ...], Decimal]:
    totals: defaultdict[tuple[str, ...], Decimal] = defaultdict(lambda: Decimal("0"))
    for record in records:
        if record.type != "expense":
            continue
        totals[
            (
                record.source_platform,
                record.source_user_id,
                record_month(record),
                record.currency,
            )
        ] += record.amount
    return dict(totals)


def _count_by(
    records: Sequence[TransactionRecord],
    key_factory: Callable[[TransactionRecord], tuple[str, ...]],
) -> Counter[tuple[str, ...]]:
    return Counter(key_factory(record) for record in records)


def _latest_transaction_ids(
    records: Sequence[TransactionRecord],
    *,
    tie_breaker: Literal["first", "last"],
) -> dict[tuple[str, ...], str]:
    by_identity: defaultdict[tuple[str, str], list[TransactionRecord]] = defaultdict(
        list
    )
    for record in records:
        by_identity[identity_key(record)].append(record)

    latest: dict[tuple[str, ...], str] = {}
    for key, identity_records in by_identity.items():
        record = latest_transaction(identity_records, tie_breaker=tie_breaker)
        if record is not None:
            latest[key] = record.id
    return latest


def _aggregate_mismatches(
    expected: Mapping[tuple[str, ...], Any],
    actual: Mapping[tuple[str, ...], Any],
) -> tuple[AggregateMismatch, ...]:
    mismatches: list[AggregateMismatch] = []
    for key in sorted(set(expected) | set(actual)):
        expected_value = expected.get(key, 0)
        actual_value = actual.get(key, 0)
        if expected_value == actual_value:
            continue
        mismatches.append(
            AggregateMismatch(
                key=tuple(str(part) for part in key),
                expected=_format_aggregate_value(expected_value),
                actual=_format_aggregate_value(actual_value),
            )
        )
    return tuple(mismatches)


def _append_aggregate_summary(
    lines: list[str],
    label: str,
    mismatches: Sequence[AggregateMismatch],
) -> None:
    if not mismatches:
        return
    first = mismatches[0]
    lines.append(
        f"{label}: {len(mismatches)} key(s); first "
        f"{'/'.join(first.key)} expected {first.expected} actual {first.actual}"
    )


def _service_account_json(args: argparse.Namespace) -> str | None:
    if args.service_account_json_file is not None:
        return args.service_account_json_file.read_text()
    return args.service_account_json


def _format_aggregate_value(value: Any) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


if __name__ == "__main__":
    raise SystemExit(main())
