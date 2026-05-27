#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.sheet_export_service import DatabaseToGoogleSheetsSyncService  # noqa: E402
from integrations.google_sheets.ledger_export import (  # noqa: E402
    GoogleSheetsLedgerExportRepository,
)
from integrations.google_sheets.repository import (  # noqa: E402
    build_google_sheets_values_client,
)
from integrations.postgres.sheet_export_repository import (  # noqa: E402
    PostgresSheetExportRepository,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sync PostgreSQL-backed transactions to per-user Google Sheets.",
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="PostgreSQL connection URL. Defaults to DATABASE_URL.",
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
        "--timezone",
        default=os.environ.get("DEFAULT_TIMEZONE", "Asia/Singapore"),
        help="Repository timezone for exported timestamps.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Maximum transaction events to sync per configured user.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.database_url:
        parser.error("--database-url or DATABASE_URL is required")

    service_account_json = _service_account_json(args)
    if not service_account_json:
        parser.error(
            "--service-account-json, --service-account-json-file, "
            "or GOOGLE_SERVICE_ACCOUNT_JSON is required"
        )
    if args.batch_size <= 0:
        parser.error("--batch-size must be positive")

    sheets_client = build_google_sheets_values_client(service_account_json)
    export_repository = PostgresSheetExportRepository(
        database_url=args.database_url,
        timezone=args.timezone,
    )

    service = DatabaseToGoogleSheetsSyncService(
        export_repository=export_repository,
        sheet_repository_factory=lambda spreadsheet_id: (
            GoogleSheetsLedgerExportRepository(
                spreadsheet_id=spreadsheet_id,
                sheets_client=sheets_client,
            )
        ),
        batch_size=args.batch_size,
    )
    result = service.sync_once()

    print(
        "sheet sync complete: "
        f"{result.export_count} configured export(s), "
        f"{result.synced_transaction_count} transaction event(s) synced, "
        f"{result.failure_count} failure(s)"
    )
    return 1 if result.failure_count else 0


def _service_account_json(args: argparse.Namespace) -> str | None:
    if args.service_account_json_file is not None:
        return args.service_account_json_file.read_text()
    return args.service_account_json


if __name__ == "__main__":
    raise SystemExit(main())
