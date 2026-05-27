from integrations.google_sheets.schema import (
    TRANSACTION_HEADERS,
    TRANSACTIONS_SHEET_NAME,
    transaction_header_row,
    validate_transaction_headers,
)


def test_transactions_sheet_name_is_canonical():
    assert TRANSACTIONS_SHEET_NAME == "Transactions"


def test_transaction_headers_define_exact_required_order():
    assert TRANSACTION_HEADERS == (
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
        "source_username",
        "source_user_display_name",
        "source_chat_id",
        "source_message_id",
        "created_at",
        "updated_at",
    )
    assert transaction_header_row() == list(TRANSACTION_HEADERS)


def test_transaction_header_validation_accepts_exact_headers():
    result = validate_transaction_headers(TRANSACTION_HEADERS)

    assert result.is_valid is True
    assert result.missing_headers == ()
    assert result.reordered_headers == ()


def test_transaction_header_validation_detects_missing_headers():
    headers = tuple(
        header for header in TRANSACTION_HEADERS if header != "payment_method"
    )

    result = validate_transaction_headers(headers)

    assert result.is_valid is False
    assert result.missing_headers == ("payment_method",)
    assert result.reordered_headers == ()


def test_transaction_header_validation_detects_reordered_headers():
    headers = list(TRANSACTION_HEADERS)
    headers[1], headers[2] = headers[2], headers[1]

    result = validate_transaction_headers(headers)

    assert result.is_valid is False
    assert result.missing_headers == ()
    assert result.reordered_headers == ("date", "amount")


def test_transaction_header_validation_reports_duplicate_headers_as_unexpected():
    result = validate_transaction_headers((*TRANSACTION_HEADERS, "amount"))

    assert result.is_valid is False
    assert result.unexpected_headers == ("amount",)
