from datetime import datetime, timezone
from decimal import Decimal

import pytest

from integrations.google_sheets.repository import (
    GoogleSheetsTransactionRepository,
    GoogleSheetsValuesClient,
    InvalidTransactionUpdateError,
    TransactionRecord,
    TransactionRepositoryError,
    TransactionSheetSchemaError,
)
from integrations.google_sheets.schema import transaction_header_row


def test_append_transaction_writes_canonical_row_order():
    sheets_client = InMemorySheetsClient()
    repository = GoogleSheetsTransactionRepository(
        sheet_id="sheet-1",
        sheets_client=sheets_client,
    )

    record = make_record(amount=Decimal("12.30"), merchant=None)

    assert repository.append_transaction(record) == record
    assert sheets_client.append_calls == [
        (
            "sheet-1",
            "Transactions!A:P",
            [
                [
                    "txn-1",
                    "2026-05-19",
                    "12.30",
                    "SGD",
                    "expense",
                    "餐饮",
                    "",
                    "card",
                    "lunch",
                    "42",
                    "ada",
                    "Ada Lovelace",
                    "12345",
                    "9001",
                    "2026-05-19T10:00:00+00:00",
                    "2026-05-19T10:00:00+00:00",
                ]
            ],
        )
    ]


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [["id", "amount", "date"]],
    ],
)
def test_append_transaction_validates_headers_before_mutating(rows):
    sheets_client = InMemorySheetsClient(rows)
    repository = GoogleSheetsTransactionRepository(
        sheet_id="sheet-1",
        sheets_client=sheets_client,
    )

    with pytest.raises(TransactionSheetSchemaError):
        repository.append_transaction(make_record())

    assert sheets_client.append_calls == []


def test_find_by_telegram_message_returns_existing_transaction():
    sheets_client = InMemorySheetsClient(
        [
            transaction_header_row(),
            make_row(
                transaction_id="txn-1",
                telegram_user_id="42",
                telegram_message_id="9001",
            ),
        ]
    )
    repository = GoogleSheetsTransactionRepository(
        sheet_id="sheet-1",
        sheets_client=sheets_client,
    )

    record = repository.find_by_telegram_message(
        user_id="42",
        chat_id="12345",
        message_id="9001",
    )

    assert record == make_record(
        transaction_id="txn-1",
        telegram_user_id="42",
        telegram_message_id="9001",
    )


def test_find_by_telegram_message_returns_none_when_missing():
    sheets_client = InMemorySheetsClient(
        [
            transaction_header_row(),
            make_row(telegram_user_id="42", telegram_message_id="9001"),
        ]
    )
    repository = GoogleSheetsTransactionRepository(
        sheet_id="sheet-1",
        sheets_client=sheets_client,
    )

    assert (
        repository.find_by_telegram_message(
            user_id="42",
            chat_id="12345",
            message_id="9002",
        )
        is None
    )


def test_find_by_telegram_message_matches_chat_id_for_idempotency():
    sheets_client = InMemorySheetsClient(
        [
            transaction_header_row(),
            make_row(
                transaction_id="private-message",
                telegram_chat_id="12345",
                telegram_message_id="9001",
            ),
            make_row(
                transaction_id="group-message",
                telegram_chat_id="-100123",
                telegram_message_id="9001",
            ),
        ]
    )
    repository = GoogleSheetsTransactionRepository(
        sheet_id="sheet-1",
        sheets_client=sheets_client,
    )

    record = repository.find_by_telegram_message(
        user_id="42",
        chat_id="-100123",
        message_id="9001",
    )

    assert record is not None
    assert record.id == "group-message"


def test_get_latest_transaction_returns_newest_user_expense_by_created_at():
    sheets_client = InMemorySheetsClient(
        [
            transaction_header_row(),
            make_row(
                transaction_id="old-expense",
                telegram_user_id="42",
                created_at="2026-05-19T10:00:00+00:00",
            ),
            make_row(
                transaction_id="other-user-expense",
                telegram_user_id="7",
                created_at="2026-05-20T15:00:00+00:00",
            ),
            make_row(
                transaction_id="new-income",
                telegram_user_id="42",
                transaction_type="income",
                created_at="2026-05-21T15:00:00+00:00",
            ),
            make_row(
                transaction_id="new-expense",
                telegram_user_id="42",
                created_at="2026-05-20T12:00:00+00:00",
            ),
        ]
    )
    repository = GoogleSheetsTransactionRepository(
        sheet_id="sheet-1",
        sheets_client=sheets_client,
    )

    record = repository.get_latest_transaction(user_id="42")

    assert record is not None
    assert record.id == "new-expense"


def test_update_transaction_changes_allowed_fields_and_refreshes_updated_at():
    sheets_client = InMemorySheetsClient(
        [
            transaction_header_row(),
            make_row(
                transaction_id="txn-1",
                amount="12.30",
                note="lunch",
                created_at="2026-05-19T10:00:00+00:00",
                updated_at="2026-05-19T10:00:00+00:00",
            ),
        ]
    )
    repository = GoogleSheetsTransactionRepository(
        sheet_id="sheet-1",
        sheets_client=sheets_client,
        clock=lambda: datetime(2026, 5, 20, 12, 30, tzinfo=timezone.utc),
    )

    record = repository.update_transaction(
        "txn-1",
        {
            "amount": Decimal("15.50"),
            "note": "corrected lunch",
        },
    )

    assert record.amount == Decimal("15.50")
    assert record.note == "corrected lunch"
    assert record.telegram_user_id == "42"
    assert record.created_at == "2026-05-19T10:00:00+00:00"
    assert record.updated_at == "2026-05-20T20:30:00+08:00"
    assert sheets_client.update_calls == [
        (
            "sheet-1",
            "Transactions!A2:P2",
            [
                [
                    "txn-1",
                    "2026-05-19",
                    "15.50",
                    "SGD",
                    "expense",
                    "餐饮",
                    "coffee shop",
                    "card",
                    "corrected lunch",
                    "42",
                    "ada",
                    "Ada Lovelace",
                    "12345",
                    "9001",
                    "2026-05-19T10:00:00+00:00",
                    "2026-05-20T20:30:00+08:00",
                ]
            ],
        )
    ]


def test_update_transaction_rejects_disallowed_fields():
    sheets_client = InMemorySheetsClient(
        [
            transaction_header_row(),
            make_row(transaction_id="txn-1"),
        ]
    )
    repository = GoogleSheetsTransactionRepository(
        sheet_id="sheet-1",
        sheets_client=sheets_client,
    )

    with pytest.raises(InvalidTransactionUpdateError):
        repository.update_transaction(
            "txn-1",
            {"telegram_user_id": "7"},
        )

    assert sheets_client.update_calls == []


def test_sum_monthly_expense_filters_user_type_month_and_currency():
    sheets_client = InMemorySheetsClient(
        [
            transaction_header_row(),
            make_row(
                transaction_id="sgd-1",
                telegram_user_id="42",
                amount="10.50",
                currency="SGD",
                transaction_type="expense",
                date="2026-05-01",
            ),
            make_row(
                transaction_id="income",
                telegram_user_id="42",
                amount="99.00",
                currency="SGD",
                transaction_type="income",
                date="2026-05-02",
            ),
            make_row(
                transaction_id="april",
                telegram_user_id="42",
                amount="4.00",
                currency="SGD",
                transaction_type="expense",
                date="2026-04-30",
            ),
            make_row(
                transaction_id="other-user",
                telegram_user_id="7",
                amount="8.00",
                currency="SGD",
                transaction_type="expense",
                date="2026-05-03",
            ),
            make_row(
                transaction_id="usd",
                telegram_user_id="42",
                amount="6.00",
                currency="USD",
                transaction_type="expense",
                date="2026-05-04",
            ),
            make_row(
                transaction_id="sgd-2",
                telegram_user_id="42",
                amount="2.25",
                currency="SGD",
                transaction_type="expense",
                date="2026-05-31",
            ),
        ]
    )
    repository = GoogleSheetsTransactionRepository(
        sheet_id="sheet-1",
        sheets_client=sheets_client,
    )

    assert repository.sum_monthly_expense(
        user_id="42",
        month="2026-05",
        currency="SGD",
    ) == Decimal("12.75")


def test_sum_monthly_expense_rejects_non_padded_month():
    repository = GoogleSheetsTransactionRepository(
        sheet_id="sheet-1",
        sheets_client=InMemorySheetsClient(),
    )

    with pytest.raises(ValueError, match="YYYY-MM"):
        repository.sum_monthly_expense(
            user_id="42",
            month="2026-5",
            currency="SGD",
        )


def test_repository_rejects_invalid_sheet_headers_before_reading_rows():
    sheets_client = InMemorySheetsClient(
        [
            [
                "id",
                "amount",
                "date",
                "currency",
                "type",
                "category",
                "merchant",
                "payment_method",
                "note",
                "telegram_user_id",
                "telegram_username",
                "telegram_user_display_name",
                "telegram_chat_id",
                "telegram_message_id",
                "created_at",
                "updated_at",
            ]
        ]
    )
    repository = GoogleSheetsTransactionRepository(
        sheet_id="sheet-1",
        sheets_client=sheets_client,
    )

    with pytest.raises(TransactionSheetSchemaError):
        repository.find_by_telegram_message(
            user_id="42",
            chat_id="12345",
            message_id="9001",
        )


def test_repository_rejects_old_width_rows_after_header_migration():
    sheets_client = InMemorySheetsClient(
        [
            transaction_header_row(),
            [
                "txn-1",
                "2026-05-19",
                "12.30",
                "SGD",
                "expense",
                "餐饮",
                "coffee shop",
                "card",
                "lunch",
                "42",
                "9001",
                "2026-05-19T10:00:00+00:00",
                "2026-05-19T10:00:00+00:00",
            ],
        ]
    )
    repository = GoogleSheetsTransactionRepository(
        sheet_id="sheet-1",
        sheets_client=sheets_client,
    )

    with pytest.raises(TransactionSheetSchemaError):
        repository.find_by_telegram_message(
            user_id="42",
            chat_id="12345",
            message_id="9001",
        )


@pytest.mark.parametrize(
    "operation",
    [
        lambda repository: repository.append_transaction(make_record()),
        lambda repository: repository.find_by_telegram_message(
            user_id="42",
            chat_id="12345",
            message_id="9001",
        ),
        lambda repository: repository.get_latest_transaction(user_id="42"),
        lambda repository: repository.update_transaction(
            "txn-1",
            {"note": "fixed"},
        ),
        lambda repository: repository.sum_monthly_expense(
            user_id="42",
            month="2026-05",
            currency="SGD",
        ),
    ],
)
def test_repository_maps_sheets_client_failures_to_repository_errors(operation):
    repository = GoogleSheetsTransactionRepository(
        sheet_id="sheet-1",
        sheets_client=FailingSheetsClient(),
    )

    with pytest.raises(TransactionRepositoryError) as error:
        operation(repository)

    assert isinstance(error.value.__cause__, RuntimeError)


def test_google_sheets_values_client_wraps_google_values_api():
    service = FakeGoogleSheetsService(
        get_response={"values": [["id"]]},
        append_response={"updates": {"updatedRows": 1}},
        update_response={"updatedRows": 1},
    )
    client = GoogleSheetsValuesClient(service)

    assert client.get_values("sheet-1", "Transactions!A:P") == [["id"]]
    client.append_values("sheet-1", "Transactions!A:P", [["txn-1"]])
    client.update_values("sheet-1", "Transactions!A2:P2", [["txn-1"]])

    assert service.calls == [
        (
            "get",
            {
                "spreadsheetId": "sheet-1",
                "range": "Transactions!A:P",
            },
        ),
        (
            "append",
            {
                "spreadsheetId": "sheet-1",
                "range": "Transactions!A:P",
                "valueInputOption": "RAW",
                "insertDataOption": "INSERT_ROWS",
                "body": {"values": [["txn-1"]]},
            },
        ),
        (
            "update",
            {
                "spreadsheetId": "sheet-1",
                "range": "Transactions!A2:P2",
                "valueInputOption": "RAW",
                "body": {"values": [["txn-1"]]},
            },
        ),
    ]


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
    telegram_user_id: str = "42",
    telegram_username: str | None = "ada",
    telegram_user_display_name: str | None = "Ada Lovelace",
    telegram_chat_id: str = "12345",
    telegram_message_id: str = "9001",
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
        telegram_user_id=telegram_user_id,
        telegram_username=telegram_username,
        telegram_user_display_name=telegram_user_display_name,
        telegram_chat_id=telegram_chat_id,
        telegram_message_id=telegram_message_id,
        created_at=created_at,
        updated_at=updated_at,
    )


def make_row(
    *,
    transaction_id: str = "txn-1",
    date: str = "2026-05-19",
    amount: str = "12.30",
    currency: str = "SGD",
    transaction_type: str = "expense",
    category: str = "餐饮",
    merchant: str = "coffee shop",
    payment_method: str = "card",
    note: str = "lunch",
    telegram_user_id: str = "42",
    telegram_username: str = "ada",
    telegram_user_display_name: str = "Ada Lovelace",
    telegram_chat_id: str = "12345",
    telegram_message_id: str = "9001",
    created_at: str = "2026-05-19T10:00:00+00:00",
    updated_at: str = "2026-05-19T10:00:00+00:00",
) -> list[str]:
    return [
        transaction_id,
        date,
        amount,
        currency,
        transaction_type,
        category,
        merchant,
        payment_method,
        note,
        telegram_user_id,
        telegram_username,
        telegram_user_display_name,
        telegram_chat_id,
        telegram_message_id,
        created_at,
        updated_at,
    ]


class InMemorySheetsClient:
    def __init__(self, rows: list[list[str]] | None = None) -> None:
        self.rows = (
            [list(row) for row in rows]
            if rows is not None
            else [transaction_header_row()]
        )
        self.append_calls: list[tuple[str, str, list[list[str]]]] = []
        self.update_calls: list[tuple[str, str, list[list[str]]]] = []

    def get_values(self, spreadsheet_id: str, range_name: str) -> list[list[str]]:
        return [list(row) for row in self.rows]

    def append_values(
        self,
        spreadsheet_id: str,
        range_name: str,
        values: list[list[str]],
    ) -> None:
        self.append_calls.append((spreadsheet_id, range_name, values))
        self.rows.extend(list(row) for row in values)

    def update_values(
        self,
        spreadsheet_id: str,
        range_name: str,
        values: list[list[str]],
    ) -> None:
        self.update_calls.append((spreadsheet_id, range_name, values))
        row_number = int(range_name.split("A", maxsplit=1)[1].split(":", maxsplit=1)[0])
        self.rows[row_number - 1] = list(values[0])


class FailingSheetsClient:
    def get_values(self, spreadsheet_id: str, range_name: str) -> list[list[str]]:
        raise RuntimeError("google api unavailable")

    def append_values(
        self,
        spreadsheet_id: str,
        range_name: str,
        values: list[list[str]],
    ) -> None:
        raise RuntimeError("google api unavailable")

    def update_values(
        self,
        spreadsheet_id: str,
        range_name: str,
        values: list[list[str]],
    ) -> None:
        raise RuntimeError("google api unavailable")


class FakeGoogleSheetsService:
    def __init__(
        self,
        *,
        get_response: dict[str, object],
        append_response: dict[str, object],
        update_response: dict[str, object],
    ) -> None:
        self.get_response = get_response
        self.append_response = append_response
        self.update_response = update_response
        self.calls: list[tuple[str, dict[str, object]]] = []

    def spreadsheets(self) -> "FakeGoogleSheetsService":
        return self

    def values(self) -> "FakeGoogleSheetsService":
        return self

    def get(self, **kwargs: object) -> "FakeRequest":
        self.calls.append(("get", kwargs))
        return FakeRequest(self.get_response)

    def append(self, **kwargs: object) -> "FakeRequest":
        self.calls.append(("append", kwargs))
        return FakeRequest(self.append_response)

    def update(self, **kwargs: object) -> "FakeRequest":
        self.calls.append(("update", kwargs))
        return FakeRequest(self.update_response)


class FakeRequest:
    def __init__(self, response: dict[str, object]) -> None:
        self.response = response

    def execute(self) -> dict[str, object]:
        return self.response
