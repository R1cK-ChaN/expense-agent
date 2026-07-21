from collections.abc import Sequence
from decimal import Decimal

from core.sheet_export import LedgerTransaction
from integrations.google_sheets.repository import (
    SheetsValuesClient,
    TransactionRepositoryError,
)


LEDGER_PROJECTION_SHEET_NAME = "Ledger"
LEDGER_EXPORT_HEADERS = (
    "id",
    "date",
    "amount",
    "currency",
    "type",
    "category",
    "merchant",
    "payment_method",
    "note",
    "created_at",
    "updated_at",
)


def _last_column_name(column_count: int) -> str:
    name = ""
    while column_count:
        column_count, remainder = divmod(column_count - 1, 26)
        name = chr(ord("A") + remainder) + name
    return name


LEDGER_EXPORT_VALUE_RANGE = (
    f"{LEDGER_PROJECTION_SHEET_NAME}!A:"
    f"{_last_column_name(len(LEDGER_EXPORT_HEADERS))}"
)


class LedgerExportSheetSchemaError(TransactionRepositoryError):
    """Raised when the export sheet does not match the ledger projection."""


class GoogleSheetsLedgerExportRepository:
    def __init__(
        self,
        *,
        spreadsheet_id: str,
        sheets_client: SheetsValuesClient,
    ) -> None:
        self._spreadsheet_id = spreadsheet_id
        self._sheets_client = sheets_client

    def upsert_transaction(self, transaction: LedgerTransaction) -> None:
        rows = self._load_validated_rows()
        row = _transaction_to_row(transaction)

        for row_number, existing_row in enumerate(rows[1:], start=2):
            if _row_transaction_id(existing_row) == transaction.id:
                self._update_row(row_number, row)
                return

        self._append_row(row)

    def _load_validated_rows(self) -> list[list[str]]:
        try:
            rows = self._sheets_client.get_values(
                self._spreadsheet_id,
                LEDGER_EXPORT_VALUE_RANGE,
            )
        except Exception as error:
            raise TransactionRepositoryError(
                "Failed to read Google Sheets ledger export."
            ) from error

        _validate_export_headers(rows)
        return rows

    def _append_row(self, row: list[str]) -> None:
        try:
            self._sheets_client.append_values(
                self._spreadsheet_id,
                LEDGER_EXPORT_VALUE_RANGE,
                [row],
            )
        except Exception as error:
            raise TransactionRepositoryError(
                "Failed to append Google Sheets ledger export row."
            ) from error

    def _update_row(self, row_number: int, row: list[str]) -> None:
        try:
            self._sheets_client.update_values(
                self._spreadsheet_id,
                _ledger_row_range(row_number),
                [row],
            )
        except Exception as error:
            raise TransactionRepositoryError(
                "Failed to update Google Sheets ledger export row."
            ) from error


def ledger_export_header_row() -> list[str]:
    return list(LEDGER_EXPORT_HEADERS)


def _transaction_to_row(transaction: LedgerTransaction) -> list[str]:
    return [
        _field_value_to_sheet_value(field_name, getattr(transaction, field_name))
        for field_name in LEDGER_EXPORT_HEADERS
    ]


def _field_value_to_sheet_value(field_name: str, value: object) -> str:
    if value is None:
        return ""
    if field_name == "amount":
        return format(_to_decimal(value), "f")
    return str(value)


def _to_decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception as error:
        raise TransactionRepositoryError(f"Invalid transaction amount: {value}") from error


def _row_transaction_id(row: Sequence[str]) -> str | None:
    if not row:
        return None
    transaction_id = str(row[0])
    return transaction_id or None


def _validate_export_headers(rows: Sequence[Sequence[str]]) -> None:
    if not rows:
        raise LedgerExportSheetSchemaError(
            f"{LEDGER_PROJECTION_SHEET_NAME} is missing the required "
            "projection header row."
        )
    if tuple(rows[0]) == LEDGER_EXPORT_HEADERS:
        return
    raise LedgerExportSheetSchemaError(
        f"{LEDGER_PROJECTION_SHEET_NAME} headers must match the ledger "
        "projection schema."
    )


def _ledger_row_range(row_number: int) -> str:
    return (
        f"{LEDGER_PROJECTION_SHEET_NAME}!A{row_number}:"
        f"{_last_column_name(len(LEDGER_EXPORT_HEADERS))}{row_number}"
    )
