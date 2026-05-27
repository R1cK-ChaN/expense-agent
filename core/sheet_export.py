from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class LedgerTransaction:
    id: str
    date: str
    amount: Decimal
    currency: str
    type: str
    category: str
    merchant: str | None
    payment_method: str | None
    note: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class SheetExportConfig:
    user_id: str
    spreadsheet_id: str
    enabled: bool
    last_synced_event_id: str | None
    last_synced_at: str | None
    last_error: str | None


@dataclass(frozen=True)
class SheetExportEvent:
    event_id: str
    user_id: str
    transaction: LedgerTransaction
    event_created_at: str
