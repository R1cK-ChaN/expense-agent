from dataclasses import dataclass
from typing import Sequence


TRANSACTIONS_SHEET_NAME = "Transactions"

TRANSACTION_HEADERS = (
    "id",
    "date",
    "amount",
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
)


@dataclass(frozen=True)
class HeaderValidationResult:
    is_valid: bool
    missing_headers: tuple[str, ...]
    reordered_headers: tuple[str, ...]
    unexpected_headers: tuple[str, ...]


def transaction_header_row() -> list[str]:
    return list(TRANSACTION_HEADERS)


def validate_transaction_headers(
    header_row: Sequence[str],
) -> HeaderValidationResult:
    actual_headers = tuple(header_row)
    missing_headers = tuple(
        header for header in TRANSACTION_HEADERS if header not in actual_headers
    )
    reordered_headers = _reordered_headers(actual_headers, missing_headers)
    unexpected_headers = _unexpected_headers(actual_headers)

    return HeaderValidationResult(
        is_valid=actual_headers == TRANSACTION_HEADERS,
        missing_headers=missing_headers,
        reordered_headers=reordered_headers,
        unexpected_headers=unexpected_headers,
    )


def _reordered_headers(
    actual_headers: tuple[str, ...],
    missing_headers: tuple[str, ...],
) -> tuple[str, ...]:
    if missing_headers:
        return ()

    return tuple(
        expected_header
        for index, expected_header in enumerate(TRANSACTION_HEADERS)
        if index >= len(actual_headers) or actual_headers[index] != expected_header
    )


def _unexpected_headers(actual_headers: tuple[str, ...]) -> tuple[str, ...]:
    seen_headers: set[str] = set()
    unexpected_headers: list[str] = []

    for header in actual_headers:
        if header not in TRANSACTION_HEADERS or header in seen_headers:
            unexpected_headers.append(header)
        seen_headers.add(header)

    return tuple(unexpected_headers)
