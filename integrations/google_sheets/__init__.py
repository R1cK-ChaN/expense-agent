"""Google Sheets integration contract helpers."""

from integrations.google_sheets.repository import (
    GoogleSheetsTransactionRepository,
    GoogleSheetsValuesClient,
    InvalidTransactionUpdateError,
    SheetsValuesClient,
    TransactionNotFoundError,
    TransactionRecord,
    TransactionRepositoryError,
    TransactionSheetSchemaError,
    build_google_sheets_values_client,
)
from integrations.google_sheets.schema import (
    TRANSACTION_HEADERS,
    TRANSACTIONS_SHEET_NAME,
    HeaderValidationResult,
    transaction_header_row,
    validate_transaction_headers,
)

__all__ = [
    "GoogleSheetsTransactionRepository",
    "GoogleSheetsValuesClient",
    "HeaderValidationResult",
    "InvalidTransactionUpdateError",
    "SheetsValuesClient",
    "TRANSACTION_HEADERS",
    "TRANSACTIONS_SHEET_NAME",
    "TransactionNotFoundError",
    "TransactionRecord",
    "TransactionRepositoryError",
    "TransactionSheetSchemaError",
    "build_google_sheets_values_client",
    "transaction_header_row",
    "validate_transaction_headers",
]
