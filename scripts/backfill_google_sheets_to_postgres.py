#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence


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
    records_equivalent,
    source_key,
)


@dataclass(frozen=True)
class BackfillIssue:
    transaction_id: str
    message: str


@dataclass(frozen=True)
class BackfillPlan:
    total: int
    pending: int
    existing: int
    issues: tuple[BackfillIssue, ...]
    records_to_import: tuple[TransactionRecord, ...]

    @property
    def is_valid(self) -> bool:
        return not self.issues


@dataclass(frozen=True)
class BackfillResult:
    total: int
    imported: int
    existing: int
    pending: int
    dry_run: bool


class BackfillPreflightError(Exception):
    def __init__(self, issues: Sequence[BackfillIssue]) -> None:
        self.issues = tuple(issues)
        super().__init__(_format_issues(self.issues))


class BackfillTargetRepository(Protocol):
    def list_transactions(self) -> list[TransactionRecord]:
        raise NotImplementedError

    def append_transaction(self, record: TransactionRecord) -> TransactionRecord:
        raise NotImplementedError


def plan_backfill(
    records: Sequence[TransactionRecord],
    target: BackfillTargetRepository,
) -> BackfillPlan:
    source_records = tuple(records)
    issues = list(validate_backfill_records(source_records))
    if issues:
        return BackfillPlan(
            total=len(source_records),
            pending=0,
            existing=0,
            issues=tuple(issues),
            records_to_import=(),
        )

    target_records = target.list_transactions()
    target_by_id = {record.id: record for record in target_records}
    target_by_source = {
        source_key(record): record
        for record in target_records
        if record.source_platform
        and record.source_chat_id
        and record.source_message_id
    }
    existing_count = 0
    records_to_import: list[TransactionRecord] = []
    for record in source_records:
        existing_by_source = target_by_source.get(source_key(record))
        existing_by_id = target_by_id.get(record.id)
        if (
            existing_by_source is not None
            and existing_by_id is not None
            and existing_by_source.id != existing_by_id.id
        ):
            issues.append(
                BackfillIssue(
                    transaction_id=record.id,
                    message=(
                        "source message matches PostgreSQL transaction "
                        f"{existing_by_source.id}, but transaction id matches "
                        f"{existing_by_id.id}"
                    ),
                )
            )
            continue

        existing = existing_by_source or existing_by_id
        if existing is None:
            records_to_import.append(record)
            continue
        if records_equivalent(record, existing):
            existing_count += 1
            continue

        conflict_target = (
            f"source message {record.source_platform}/"
            f"{record.source_chat_id}/{record.source_message_id}"
            if existing_by_source is not None
            else f"transaction id {record.id}"
        )
        issues.append(
            BackfillIssue(
                transaction_id=record.id,
                message=(
                    "conflicts with existing PostgreSQL transaction for "
                    f"{conflict_target}"
                ),
            )
        )

    return BackfillPlan(
        total=len(source_records),
        pending=len(records_to_import),
        existing=existing_count,
        issues=tuple(issues),
        records_to_import=tuple(records_to_import),
    )


def backfill_records(
    records: Sequence[TransactionRecord],
    target: BackfillTargetRepository,
    *,
    dry_run: bool = True,
) -> BackfillResult:
    plan = plan_backfill(records, target)
    if not plan.is_valid:
        raise BackfillPreflightError(plan.issues)

    if dry_run:
        return BackfillResult(
            total=plan.total,
            imported=0,
            existing=plan.existing,
            pending=plan.pending,
            dry_run=True,
        )

    imported = 0
    for record in plan.records_to_import:
        saved_record = target.append_transaction(record)
        if not records_equivalent(record, saved_record):
            raise BackfillPreflightError(
                [
                    BackfillIssue(
                        transaction_id=record.id,
                        message=(
                            "append returned a different PostgreSQL transaction"
                        ),
                    )
                ]
            )
        imported += 1

    return BackfillResult(
        total=plan.total,
        imported=imported,
        existing=plan.existing,
        pending=0,
        dry_run=False,
    )


def validate_backfill_records(
    records: Sequence[TransactionRecord],
) -> tuple[BackfillIssue, ...]:
    issues: list[BackfillIssue] = []
    seen_ids: dict[str, str] = {}
    seen_sources: dict[tuple[str, str, str], str] = {}

    for record in records:
        if not record.id:
            issues.append(BackfillIssue("<blank>", "missing transaction id"))
            continue

        if record.id in seen_ids:
            issues.append(
                BackfillIssue(
                    record.id,
                    f"duplicate transaction id also used by {seen_ids[record.id]}",
                )
            )
        else:
            seen_ids[record.id] = record.id

        if record.type != "expense":
            issues.append(
                BackfillIssue(
                    record.id,
                    f"unsupported transaction type for PostgreSQL import: {record.type}",
                )
            )

        if record.amount <= 0:
            issues.append(
                BackfillIssue(record.id, "amount must be positive for PostgreSQL")
            )

        if not all(
            (
                record.source_platform,
                record.source_user_id,
                record.source_chat_id,
                record.source_message_id,
            )
        ):
            issues.append(
                BackfillIssue(
                    record.id,
                    "missing source message metadata required for idempotent import",
                )
            )
            continue

        key = source_key(record)
        if key in seen_sources:
            issues.append(
                BackfillIssue(
                    record.id,
                    "duplicate source message tuple also used by "
                    f"{seen_sources[key]}",
                )
            )
        else:
            seen_sources[key] = record.id

    return tuple(issues)


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
        description="Backfill Expense Agent Google Sheets transactions into PostgreSQL.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Write missing rows to PostgreSQL. Without this flag, only dry-run.",
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

    records = load_google_sheet_records(
        sheet_id=args.sheet_id,
        service_account_json=service_account_json,
        timezone=args.timezone,
    )
    target = PostgresTransactionRepository(
        database_url=args.database_url,
        timezone=args.timezone,
    )

    try:
        result = backfill_records(records, target, dry_run=not args.execute)
    except BackfillPreflightError as error:
        print(str(error), file=sys.stderr)
        return 1

    print(_format_result(result))
    return 0


def _service_account_json(args: argparse.Namespace) -> str | None:
    if args.service_account_json_file is not None:
        return args.service_account_json_file.read_text()
    return args.service_account_json


def _format_result(result: BackfillResult) -> str:
    if result.dry_run:
        return (
            "dry run complete: "
            f"{result.total} source transaction(s), "
            f"{result.pending} pending import, "
            f"{result.existing} already present"
        )
    return (
        "backfill complete: "
        f"{result.imported} imported, "
        f"{result.existing} already present, "
        f"{result.total} source transaction(s)"
    )


def _format_issues(issues: Sequence[BackfillIssue]) -> str:
    lines = ["backfill preflight failed:"]
    for issue in issues:
        lines.append(f"- {issue.transaction_id}: {issue.message}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
