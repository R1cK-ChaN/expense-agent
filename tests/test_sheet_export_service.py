from datetime import datetime, timezone
from decimal import Decimal

from core.sheet_export import (
    LedgerTransaction,
    SheetExportConfig,
    SheetExportEvent,
)
from core.sheet_export_service import DatabaseToGoogleSheetsSyncService


def test_sync_routes_each_user_pending_events_to_configured_spreadsheet():
    export_repository = FakeSheetExportRepository(
        configs=[
            make_config(user_id="user-1", spreadsheet_id="sheet-1"),
            make_config(user_id="user-2", spreadsheet_id="sheet-2"),
        ],
        pending_events={
            "user-1": [
                make_event(
                    event_id="event-1",
                    user_id="user-1",
                    transaction=make_transaction(transaction_id="txn-1"),
                )
            ],
            "user-2": [
                make_event(
                    event_id="event-2",
                    user_id="user-2",
                    transaction=make_transaction(transaction_id="txn-2"),
                )
            ],
        },
    )
    sheets = FakeSheetRepositoryFactory()
    service = DatabaseToGoogleSheetsSyncService(
        export_repository=export_repository,
        sheet_repository_factory=sheets,
        clock=lambda: datetime(2026, 5, 20, 12, 30, tzinfo=timezone.utc),
    )

    result = service.sync_once()

    assert result.export_count == 2
    assert result.synced_transaction_count == 2
    assert result.failure_count == 0
    assert sheets.repositories["sheet-1"].upserts == [make_transaction("txn-1")]
    assert sheets.repositories["sheet-2"].upserts == [make_transaction("txn-2")]
    assert export_repository.synced == [
        ("user-1", "event-1", datetime(2026, 5, 20, 12, 30, tzinfo=timezone.utc)),
        ("user-2", "event-2", datetime(2026, 5, 20, 12, 30, tzinfo=timezone.utc)),
    ]


def test_sync_failure_records_retryable_state_without_advancing_cursor():
    export_repository = FakeSheetExportRepository(
        configs=[make_config(user_id="user-1", spreadsheet_id="sheet-1")],
        pending_events={
            "user-1": [
                make_event(
                    event_id="event-1",
                    user_id="user-1",
                    transaction=make_transaction(transaction_id="txn-1"),
                )
            ],
        },
    )
    sheets = FakeSheetRepositoryFactory(fail_spreadsheets={"sheet-1"})
    service = DatabaseToGoogleSheetsSyncService(
        export_repository=export_repository,
        sheet_repository_factory=sheets,
        clock=lambda: datetime(2026, 5, 20, 12, 30, tzinfo=timezone.utc),
    )

    result = service.sync_once()

    assert result.export_count == 1
    assert result.synced_transaction_count == 0
    assert result.failure_count == 1
    assert export_repository.synced == []
    assert export_repository.failures == [
        (
            "user-1",
            "Google Sheets write failed for sheet-1",
            datetime(2026, 5, 20, 12, 30, tzinfo=timezone.utc),
        )
    ]
    assert export_repository.pending_events["user-1"][0].transaction.id == "txn-1"


def test_later_scheduled_run_retries_event_after_sheet_failure():
    export_repository = FakeSheetExportRepository(
        configs=[make_config(user_id="user-1", spreadsheet_id="sheet-1")],
        pending_events={
            "user-1": [
                make_event(
                    event_id="event-1",
                    user_id="user-1",
                    transaction=make_transaction(transaction_id="txn-1"),
                )
            ],
        },
    )
    sheets = FakeSheetRepositoryFactory(fail_spreadsheets={"sheet-1"})
    service = DatabaseToGoogleSheetsSyncService(
        export_repository=export_repository,
        sheet_repository_factory=sheets,
        clock=lambda: datetime(2026, 5, 20, 12, 30, tzinfo=timezone.utc),
    )

    first_result = service.sync_once()
    sheets.fail_spreadsheets.clear()
    second_result = service.sync_once()

    assert first_result.failure_count == 1
    assert second_result.failure_count == 0
    assert second_result.synced_transaction_count == 1
    assert export_repository.synced == [
        ("user-1", "event-1", datetime(2026, 5, 20, 12, 30, tzinfo=timezone.utc))
    ]


def test_sync_marks_export_successful_without_rows_to_clear_previous_error():
    export_repository = FakeSheetExportRepository(
        configs=[
            make_config(
                user_id="user-1",
                spreadsheet_id="sheet-1",
                last_synced_event_id="event-previous",
                last_error="previous failure",
            )
        ],
        pending_events={"user-1": []},
    )
    sheets = FakeSheetRepositoryFactory()
    service = DatabaseToGoogleSheetsSyncService(
        export_repository=export_repository,
        sheet_repository_factory=sheets,
        clock=lambda: datetime(2026, 5, 20, 12, 30, tzinfo=timezone.utc),
    )

    result = service.sync_once()

    assert result.synced_transaction_count == 0
    assert result.failure_count == 0
    assert export_repository.synced == [
        (
            "user-1",
            "event-previous",
            datetime(2026, 5, 20, 12, 30, tzinfo=timezone.utc),
        )
    ]
    assert sheets.repositories["sheet-1"].upserts == []


def make_config(
    *,
    user_id: str,
    spreadsheet_id: str,
    last_synced_event_id: str | None = None,
    last_error: str | None = None,
) -> SheetExportConfig:
    return SheetExportConfig(
        user_id=user_id,
        spreadsheet_id=spreadsheet_id,
        enabled=True,
        last_synced_event_id=last_synced_event_id,
        last_synced_at=None,
        last_error=last_error,
    )


def make_event(
    *,
    event_id: str,
    user_id: str,
    transaction: LedgerTransaction,
) -> SheetExportEvent:
    return SheetExportEvent(
        event_id=event_id,
        user_id=user_id,
        transaction=transaction,
        event_created_at="2026-05-19T10:00:00+00:00",
    )


def make_transaction(transaction_id: str = "txn-1") -> LedgerTransaction:
    return LedgerTransaction(
        id=transaction_id,
        date="2026-05-19",
        amount=Decimal("12.30"),
        currency="SGD",
        type="expense",
        category="Dining",
        merchant="coffee shop",
        payment_method="card",
        note="lunch",
        created_at="2026-05-19T10:00:00+00:00",
        updated_at="2026-05-19T10:00:00+00:00",
    )


class FakeSheetExportRepository:
    def __init__(
        self,
        *,
        configs: list[SheetExportConfig],
        pending_events: dict[str, list[SheetExportEvent]],
    ) -> None:
        self.configs = configs
        self.pending_events = pending_events
        self.synced: list[tuple[str, str | None, datetime]] = []
        self.failures: list[tuple[str, str, datetime]] = []

    def list_enabled_exports(self) -> list[SheetExportConfig]:
        return self.configs

    def list_pending_events(
        self,
        config: SheetExportConfig,
        *,
        limit: int,
    ) -> list[SheetExportEvent]:
        return self.pending_events[config.user_id][:limit]

    def mark_synced(
        self,
        *,
        user_id: str,
        last_event_id: str | None,
        synced_at: datetime,
    ) -> None:
        self.synced.append((user_id, last_event_id, synced_at))

    def mark_failed(
        self,
        *,
        user_id: str,
        error: str,
        failed_at: datetime,
    ) -> None:
        self.failures.append((user_id, error, failed_at))


class FakeSheetRepositoryFactory:
    def __init__(self, fail_spreadsheets: set[str] | None = None) -> None:
        self.fail_spreadsheets = fail_spreadsheets or set()
        self.repositories: dict[str, FakeLedgerSheetRepository] = {}

    def __call__(self, spreadsheet_id: str) -> "FakeLedgerSheetRepository":
        repository = self.repositories.setdefault(
            spreadsheet_id,
            FakeLedgerSheetRepository(spreadsheet_id),
        )
        repository.should_fail = spreadsheet_id in self.fail_spreadsheets
        return repository


class FakeLedgerSheetRepository:
    def __init__(self, spreadsheet_id: str) -> None:
        self.spreadsheet_id = spreadsheet_id
        self.should_fail = False
        self.upserts: list[LedgerTransaction] = []

    def upsert_transaction(self, transaction: LedgerTransaction) -> None:
        if self.should_fail:
            raise RuntimeError(
                f"Google Sheets write failed for {self.spreadsheet_id}"
            )
        self.upserts.append(transaction)
