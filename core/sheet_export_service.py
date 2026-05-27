from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from core.sheet_export import (
    LedgerTransaction,
    SheetExportConfig,
    SheetExportEvent,
)


DEFAULT_SYNC_BATCH_SIZE = 500


class SheetExportRepository(Protocol):
    def list_enabled_exports(self) -> list[SheetExportConfig]:
        raise NotImplementedError

    def list_pending_events(
        self,
        config: SheetExportConfig,
        *,
        limit: int,
    ) -> list[SheetExportEvent]:
        raise NotImplementedError

    def mark_synced(
        self,
        *,
        user_id: str,
        last_event_id: str | None,
        synced_at: datetime,
    ) -> None:
        raise NotImplementedError

    def mark_failed(
        self,
        *,
        user_id: str,
        error: str,
        failed_at: datetime,
    ) -> None:
        raise NotImplementedError


class LedgerSheetRepository(Protocol):
    def upsert_transaction(self, transaction: LedgerTransaction) -> None:
        raise NotImplementedError


@dataclass(frozen=True)
class SheetSyncResult:
    export_count: int
    synced_transaction_count: int
    failure_count: int


class DatabaseToGoogleSheetsSyncService:
    def __init__(
        self,
        *,
        export_repository: SheetExportRepository,
        sheet_repository_factory: Callable[[str], LedgerSheetRepository],
        batch_size: int = DEFAULT_SYNC_BATCH_SIZE,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._export_repository = export_repository
        self._sheet_repository_factory = sheet_repository_factory
        self._batch_size = batch_size
        self._clock = clock or _utc_now

    def sync_once(self) -> SheetSyncResult:
        export_count = 0
        synced_transaction_count = 0
        failure_count = 0

        for config in self._export_repository.list_enabled_exports():
            export_count += 1
            last_event_id = config.last_synced_event_id
            try:
                pending_events = self._export_repository.list_pending_events(
                    config,
                    limit=self._batch_size,
                )
                sheet_repository = self._sheet_repository_factory(
                    config.spreadsheet_id,
                )
                for event in pending_events:
                    sheet_repository.upsert_transaction(event.transaction)
                    last_event_id = event.event_id
                    synced_transaction_count += 1

                self._export_repository.mark_synced(
                    user_id=config.user_id,
                    last_event_id=last_event_id,
                    synced_at=self._clock(),
                )
            except Exception as error:
                failure_count += 1
                self._export_repository.mark_failed(
                    user_id=config.user_id,
                    error=str(error),
                    failed_at=self._clock(),
                )

        return SheetSyncResult(
            export_count=export_count,
            synced_transaction_count=synced_transaction_count,
            failure_count=failure_count,
        )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
