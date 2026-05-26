import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from integrations.google_sheets.schema import (
    TRANSACTION_HEADERS,
    TRANSACTIONS_SHEET_NAME,
    validate_transaction_headers,
)


def _last_column_name(column_count: int) -> str:
    name = ""
    while column_count:
        column_count, remainder = divmod(column_count - 1, 26)
        name = chr(ord("A") + remainder) + name
    return name


TRANSACTIONS_VALUE_RANGE = (
    f"{TRANSACTIONS_SHEET_NAME}!A:{_last_column_name(len(TRANSACTION_HEADERS))}"
)

ALLOWED_UPDATE_FIELDS = frozenset(
    {
        "date",
        "amount",
        "currency",
        "type",
        "category",
        "merchant",
        "payment_method",
        "note",
    }
)


class TransactionRepositoryError(Exception):
    """Raised when the transaction repository cannot complete an operation."""


class TransactionSheetSchemaError(TransactionRepositoryError):
    """Raised when the Transactions sheet does not match the code schema."""


class TransactionNotFoundError(TransactionRepositoryError):
    """Raised when an update target cannot be found."""


class InvalidTransactionUpdateError(TransactionRepositoryError, ValueError):
    """Raised when callers attempt to update immutable transaction fields."""


class SheetsValuesClient(Protocol):
    def get_values(self, spreadsheet_id: str, range_name: str) -> list[list[str]]:
        raise NotImplementedError

    def append_values(
        self,
        spreadsheet_id: str,
        range_name: str,
        values: list[list[str]],
    ) -> None:
        raise NotImplementedError

    def update_values(
        self,
        spreadsheet_id: str,
        range_name: str,
        values: list[list[str]],
    ) -> None:
        raise NotImplementedError


@dataclass(frozen=True)
class TransactionRecord:
    id: str
    date: str
    amount: Decimal
    currency: str
    type: str
    category: str
    merchant: str | None
    payment_method: str | None
    note: str | None
    telegram_user_id: str
    telegram_username: str | None
    telegram_user_display_name: str | None
    telegram_chat_id: str
    telegram_message_id: str
    created_at: str
    updated_at: str


class GoogleSheetsTransactionRepository:
    def __init__(
        self,
        *,
        sheet_id: str,
        sheets_client: SheetsValuesClient,
        timezone: str = "Asia/Singapore",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._sheet_id = sheet_id
        self._sheets_client = sheets_client
        self._timezone = timezone
        self._clock = clock or _utc_now

    def append_transaction(self, record: TransactionRecord) -> TransactionRecord:
        self._ensure_transaction_schema()
        row = _record_to_row(record)
        try:
            self._sheets_client.append_values(
                self._sheet_id,
                TRANSACTIONS_VALUE_RANGE,
                [row],
            )
        except Exception as error:
            raise TransactionRepositoryError(
                "Failed to append transaction to Google Sheets."
            ) from error

        return record

    def find_by_telegram_message(
        self,
        *,
        user_id: str,
        chat_id: str,
        message_id: str,
    ) -> TransactionRecord | None:
        for _row_number, record in self._load_records():
            if (
                record.telegram_user_id == str(user_id)
                and record.telegram_chat_id == str(chat_id)
                and record.telegram_message_id == str(message_id)
            ):
                return record

        return None

    def get_latest_transaction(self, *, user_id: str) -> TransactionRecord | None:
        matching_records = [
            record
            for _row_number, record in self._load_records()
            if record.telegram_user_id == str(user_id) and record.type == "expense"
        ]
        if not matching_records:
            return None

        return max(matching_records, key=_created_at_sort_key)

    def update_transaction(
        self,
        transaction_id: str,
        fields: Mapping[str, object],
    ) -> TransactionRecord:
        invalid_fields = sorted(set(fields) - ALLOWED_UPDATE_FIELDS)
        if invalid_fields:
            raise InvalidTransactionUpdateError(
                "Cannot update immutable transaction fields: "
                + ", ".join(invalid_fields)
            )

        for row_number, record in self._load_records():
            if record.id == transaction_id:
                updated_record = _apply_updates(
                    record,
                    fields,
                    updated_at=_format_timestamp(self._clock(), self._timezone),
                )
                self._update_row(row_number, updated_record)
                return updated_record

        raise TransactionNotFoundError(f"Transaction not found: {transaction_id}")

    def sum_monthly_expense(
        self,
        *,
        user_id: str,
        month: str,
        currency: str,
    ) -> Decimal:
        _validate_month(month)
        total = Decimal("0")

        for _row_number, record in self._load_records():
            if (
                record.telegram_user_id == str(user_id)
                and record.type == "expense"
                and record.currency == currency
                and record.date.startswith(f"{month}-")
            ):
                total += record.amount

        return total

    def _load_records(self) -> list[tuple[int, TransactionRecord]]:
        rows = self._load_validated_rows()

        records: list[tuple[int, TransactionRecord]] = []
        for row_number, row in enumerate(rows[1:], start=2):
            if any(cell != "" for cell in row):
                records.append((row_number, _row_to_record(row)))

        return records

    def _ensure_transaction_schema(self) -> None:
        self._load_validated_rows()

    def _load_validated_rows(self) -> list[list[str]]:
        rows = self._load_rows()
        _validate_transaction_sheet_schema(rows)
        return rows

    def _load_rows(self) -> list[list[str]]:
        try:
            return self._sheets_client.get_values(
                self._sheet_id,
                TRANSACTIONS_VALUE_RANGE,
            )
        except Exception as error:
            raise TransactionRepositoryError(
                "Failed to read transactions from Google Sheets."
            ) from error

    def _update_row(self, row_number: int, record: TransactionRecord) -> None:
        try:
            self._sheets_client.update_values(
                self._sheet_id,
                _transaction_row_range(row_number),
                [_record_to_row(record)],
            )
        except Exception as error:
            raise TransactionRepositoryError(
                "Failed to update transaction in Google Sheets."
            ) from error


class GoogleSheetsValuesClient:
    def __init__(self, service: Any) -> None:
        self._service = service

    def get_values(self, spreadsheet_id: str, range_name: str) -> list[list[str]]:
        response = (
            self._service.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=range_name)
            .execute()
        )
        return [
            [_cell_to_string(cell) for cell in row]
            for row in response.get("values", [])
        ]

    def append_values(
        self,
        spreadsheet_id: str,
        range_name: str,
        values: list[list[str]],
    ) -> None:
        (
            self._service.spreadsheets()
            .values()
            .append(
                spreadsheetId=spreadsheet_id,
                range=range_name,
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": values},
            )
            .execute()
        )

    def update_values(
        self,
        spreadsheet_id: str,
        range_name: str,
        values: list[list[str]],
    ) -> None:
        (
            self._service.spreadsheets()
            .values()
            .update(
                spreadsheetId=spreadsheet_id,
                range=range_name,
                valueInputOption="RAW",
                body={"values": values},
            )
            .execute()
        )


def build_google_sheets_values_client(
    service_account_json: str,
) -> GoogleSheetsValuesClient:
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError as error:
        raise TransactionRepositoryError(
            "Google Sheets dependencies are not installed."
        ) from error

    credentials = service_account.Credentials.from_service_account_info(
        json.loads(service_account_json),
        scopes=("https://www.googleapis.com/auth/spreadsheets",),
    )
    service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
    return GoogleSheetsValuesClient(service)


def _apply_updates(
    record: TransactionRecord,
    fields: Mapping[str, object],
    *,
    updated_at: str,
) -> TransactionRecord:
    values = _record_to_values(record)
    for field_name, value in fields.items():
        values[field_name] = _field_value_to_record_value(field_name, value)
    values["updated_at"] = updated_at

    return _values_to_record(values)


def _record_to_values(record: TransactionRecord) -> dict[str, object]:
    return {header: getattr(record, header) for header in TRANSACTION_HEADERS}


def _values_to_record(values: Mapping[str, object]) -> TransactionRecord:
    return TransactionRecord(
        id=str(values["id"]),
        date=str(values["date"]),
        amount=_to_decimal(values["amount"]),
        currency=str(values["currency"]),
        type=str(values["type"]),
        category=str(values["category"]),
        merchant=_optional_string(values["merchant"]),
        payment_method=_optional_string(values["payment_method"]),
        note=_optional_string(values["note"]),
        telegram_user_id=str(values["telegram_user_id"]),
        telegram_username=_optional_string(values["telegram_username"]),
        telegram_user_display_name=_optional_string(
            values["telegram_user_display_name"]
        ),
        telegram_chat_id=str(values["telegram_chat_id"]),
        telegram_message_id=str(values["telegram_message_id"]),
        created_at=str(values["created_at"]),
        updated_at=str(values["updated_at"]),
    )


def _record_to_row(record: TransactionRecord) -> list[str]:
    return [
        _field_value_to_sheet_value(header, getattr(record, header))
        for header in TRANSACTION_HEADERS
    ]


def _row_to_record(row: Sequence[str]) -> TransactionRecord:
    if len(row) < len(TRANSACTION_HEADERS):
        raise TransactionSheetSchemaError(
            f"{TRANSACTIONS_SHEET_NAME} row is missing required transaction "
            "columns. Complete the sheet schema migration before reading rows."
        )

    values = [_cell_to_string(cell) for cell in _pad_row(row)]
    return TransactionRecord(
        id=values[0],
        date=values[1],
        amount=_to_decimal(values[2]),
        currency=values[3],
        type=values[4],
        category=values[5],
        merchant=_blank_to_none(values[6]),
        payment_method=_blank_to_none(values[7]),
        note=_blank_to_none(values[8]),
        telegram_user_id=values[9],
        telegram_username=_blank_to_none(values[10]),
        telegram_user_display_name=_blank_to_none(values[11]),
        telegram_chat_id=values[12],
        telegram_message_id=values[13],
        created_at=values[14],
        updated_at=values[15],
    )


def _field_value_to_record_value(field_name: str, value: object) -> object:
    if field_name == "amount":
        return _to_decimal(value)
    if field_name in {"merchant", "payment_method", "note"}:
        return _optional_string(value)
    return str(value)


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


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return _blank_to_none(str(value))


def _blank_to_none(value: str) -> str | None:
    return value if value else None


def _pad_row(row: Sequence[str]) -> list[str]:
    return [*row, *[""] * (len(TRANSACTION_HEADERS) - len(row))]


def _cell_to_string(value: object) -> str:
    return "" if value is None else str(value)


def _created_at_sort_key(record: TransactionRecord) -> datetime:
    try:
        return datetime.fromisoformat(record.created_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise TransactionRepositoryError(
            f"Invalid transaction created_at timestamp: {record.created_at}"
        ) from error


def _format_timestamp(timestamp: datetime, timezone_name: str) -> str:
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=ZoneInfo(timezone_name))
    return timestamp.astimezone(ZoneInfo(timezone_name)).isoformat()


def _validate_month(month: str) -> None:
    if (
        len(month) != 7
        or month[4] != "-"
        or not month[:4].isdigit()
        or not month[5:].isdigit()
    ):
        raise ValueError("month must use YYYY-MM format")

    try:
        datetime.strptime(month, "%Y-%m")
    except ValueError as error:
        raise ValueError("month must use YYYY-MM format") from error


def _validate_transaction_sheet_schema(rows: Sequence[Sequence[str]]) -> None:
    if not rows:
        raise TransactionSheetSchemaError(
            f"{TRANSACTIONS_SHEET_NAME} is missing the required header row."
        )

    validation = validate_transaction_headers(rows[0])
    if validation.is_valid:
        return

    raise TransactionSheetSchemaError(
        f"{TRANSACTIONS_SHEET_NAME} headers must match the canonical transaction "
        "schema."
    )


def _transaction_row_range(row_number: int) -> str:
    return (
        f"{TRANSACTIONS_SHEET_NAME}!A{row_number}:"
        f"{_last_column_name(len(TRANSACTION_HEADERS))}{row_number}"
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
