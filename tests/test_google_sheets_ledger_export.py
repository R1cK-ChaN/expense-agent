from datetime import datetime, timezone
from decimal import Decimal

import pytest

from core.sheet_export import LedgerTransaction
from integrations.google_sheets.ledger_export import (
    GoogleSheetsLedgerExportRepository,
    LedgerExportSheetSchemaError,
    ledger_export_header_row,
)


def test_upsert_transaction_appends_user_facing_ledger_fields_only():
    sheets_client = InMemorySheetsClient()
    repository = GoogleSheetsLedgerExportRepository(
        spreadsheet_id="sheet-user-1",
        sheets_client=sheets_client,
    )

    repository.upsert_transaction(make_transaction())

    assert sheets_client.append_calls == [
        (
            "sheet-user-1",
            "Transactions!A:K",
            [
                [
                    "txn-1",
                    "2026-05-19",
                    "12.30",
                    "SGD",
                    "expense",
                    "Dining",
                    "coffee shop",
                    "card",
                    "lunch",
                    "2026-05-19T10:00:00+00:00",
                    "2026-05-19T10:00:00+00:00",
                ]
            ],
        )
    ]
    assert sheets_client.rows == [
        ledger_export_header_row(),
        [
            "txn-1",
            "2026-05-19",
            "12.30",
            "SGD",
            "expense",
            "Dining",
            "coffee shop",
            "card",
            "lunch",
            "2026-05-19T10:00:00+00:00",
            "2026-05-19T10:00:00+00:00",
        ],
    ]
    assert "source_user_id" not in sheets_client.rows[0]
    assert "parser_confidence" not in sheets_client.rows[0]
    assert "last_latitude" not in sheets_client.rows[0]


def test_upsert_transaction_updates_existing_row_by_transaction_id():
    sheets_client = InMemorySheetsClient(
        [
            ledger_export_header_row(),
            make_row(transaction_id="txn-1", amount="12.30"),
        ]
    )
    repository = GoogleSheetsLedgerExportRepository(
        spreadsheet_id="sheet-user-1",
        sheets_client=sheets_client,
    )

    repository.upsert_transaction(
        make_transaction(amount=Decimal("15.50"), updated_at="2026-05-20T12:00:00Z")
    )

    assert len(sheets_client.rows) == 2
    assert sheets_client.rows[1][0] == "txn-1"
    assert sheets_client.rows[1][2] == "15.50"
    assert sheets_client.rows[1][10] == "2026-05-20T12:00:00Z"
    assert sheets_client.append_calls == []
    assert sheets_client.update_calls == [
        (
            "sheet-user-1",
            "Transactions!A2:K2",
            [sheets_client.rows[1]],
        )
    ]


def test_upsert_transaction_rejects_unmigrated_export_sheet_headers():
    sheets_client = InMemorySheetsClient([["id", "date", "source_user_id"]])
    repository = GoogleSheetsLedgerExportRepository(
        spreadsheet_id="sheet-user-1",
        sheets_client=sheets_client,
    )

    with pytest.raises(LedgerExportSheetSchemaError):
        repository.upsert_transaction(make_transaction())

    assert sheets_client.append_calls == []
    assert sheets_client.update_calls == []


def make_transaction(
    *,
    transaction_id: str = "txn-1",
    amount: Decimal = Decimal("12.30"),
    updated_at: str = "2026-05-19T10:00:00+00:00",
) -> LedgerTransaction:
    return LedgerTransaction(
        id=transaction_id,
        date="2026-05-19",
        amount=amount,
        currency="SGD",
        type="expense",
        category="Dining",
        merchant="coffee shop",
        payment_method="card",
        note="lunch",
        created_at="2026-05-19T10:00:00+00:00",
        updated_at=updated_at,
    )


def make_row(
    *,
    transaction_id: str = "txn-1",
    amount: str = "12.30",
) -> list[str]:
    return [
        transaction_id,
        "2026-05-19",
        amount,
        "SGD",
        "expense",
        "Dining",
        "coffee shop",
        "card",
        "lunch",
        "2026-05-19T10:00:00+00:00",
        "2026-05-19T10:00:00+00:00",
    ]


class InMemorySheetsClient:
    def __init__(self, rows: list[list[str]] | None = None) -> None:
        self.rows = [list(row) for row in rows] if rows is not None else [
            ledger_export_header_row()
        ]
        self.append_calls: list[tuple[str, str, list[list[str]]]] = []
        self.update_calls: list[tuple[str, str, list[list[str]]]] = []

    def get_values(self, spreadsheet_id: str, range_name: str) -> list[list[str]]:
        return self.rows

    def append_values(
        self,
        spreadsheet_id: str,
        range_name: str,
        values: list[list[str]],
    ) -> None:
        self.append_calls.append((spreadsheet_id, range_name, values))
        self.rows.extend(values)

    def update_values(
        self,
        spreadsheet_id: str,
        range_name: str,
        values: list[list[str]],
    ) -> None:
        self.update_calls.append((spreadsheet_id, range_name, values))
        row_number = int(range_name.split("!A", 1)[1].split(":", 1)[0])
        self.rows[row_number - 1] = values[0]
