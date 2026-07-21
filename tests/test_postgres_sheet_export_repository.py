from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from core.sheet_export import LedgerTransaction
from integrations.postgres.sheet_export_repository import (
    LIST_PENDING_EVENTS_SQL,
    PostgresSheetExportRepository,
)


def test_upsert_export_config_maps_internal_user_to_spreadsheet():
    database = InMemorySheetExportDatabase()
    repository = make_repository(database)

    config = repository.upsert_export_config(
        user_id="user-1",
        spreadsheet_id="sheet-1",
    )

    assert config.user_id == "user-1"
    assert config.spreadsheet_id == "sheet-1"
    assert config.enabled is True
    assert database.exports["user-1"]["spreadsheet_id"] == "sheet-1"
    assert database.exports["user-1"]["last_error"] is None
    assert database.commits == 1


def test_upsert_export_config_resets_cursor_when_spreadsheet_changes():
    database = InMemorySheetExportDatabase()
    database.seed_export(
        user_id="user-1",
        spreadsheet_id="old-sheet",
        last_synced_event_id="event-1",
    )
    database.exports["user-1"]["last_synced_at"] = "2026-05-20T12:00:00+00:00"
    repository = make_repository(database)

    config = repository.upsert_export_config(
        user_id="user-1",
        spreadsheet_id="new-sheet",
    )

    assert config.spreadsheet_id == "new-sheet"
    assert config.last_synced_event_id is None
    assert config.last_synced_at is None
    assert database.exports["user-1"]["last_synced_event_id"] is None
    assert database.exports["user-1"]["last_synced_at"] is None


def test_list_pending_events_returns_current_transaction_state_for_one_user():
    database = InMemorySheetExportDatabase()
    database.seed_export(user_id="user-1", spreadsheet_id="sheet-1")
    database.seed_export(user_id="user-2", spreadsheet_id="sheet-2")
    database.seed_event(
        event_id="event-1",
        user_id="user-1",
        transaction_id="txn-1",
        amount=Decimal("12.30"),
    )
    database.seed_event(
        event_id="event-2",
        user_id="user-2",
        transaction_id="txn-2",
        amount=Decimal("99.00"),
    )
    repository = make_repository(database)
    config = repository.list_enabled_exports()[0]

    events = repository.list_pending_events(config, limit=100)

    assert [event.event_id for event in events] == ["event-1"]
    assert events[0].user_id == "user-1"
    assert events[0].transaction == LedgerTransaction(
        id="txn-1",
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


def test_pending_event_query_types_a_null_uuid_cursor_for_postgresql():
    assert "%(last_synced_event_id)s::uuid is null" in LIST_PENDING_EVENTS_SQL


def test_list_pending_events_starts_after_last_synced_event_cursor():
    database = InMemorySheetExportDatabase()
    database.seed_export(
        user_id="user-1",
        spreadsheet_id="sheet-1",
        last_synced_event_id="event-1",
    )
    database.seed_event(event_id="event-1", user_id="user-1", transaction_id="txn-1")
    database.seed_event(event_id="event-2", user_id="user-1", transaction_id="txn-2")
    repository = make_repository(database)
    config = repository.list_enabled_exports()[0]

    events = repository.list_pending_events(config, limit=100)

    assert [event.event_id for event in events] == ["event-2"]


def test_mark_synced_advances_cursor_and_clears_error():
    database = InMemorySheetExportDatabase()
    database.seed_export(
        user_id="user-1",
        spreadsheet_id="sheet-1",
        last_error="previous failure",
    )
    repository = make_repository(database)

    repository.mark_synced(
        user_id="user-1",
        last_event_id="event-1",
        synced_at=datetime(2026, 5, 20, 12, 30, tzinfo=timezone.utc),
    )

    export = database.exports["user-1"]
    assert export["last_synced_event_id"] == "event-1"
    assert export["last_synced_at"] == datetime(2026, 5, 20, 12, 30, tzinfo=timezone.utc)
    assert export["last_error"] is None


def test_mark_failed_records_retryable_error_without_removing_pending_event():
    database = InMemorySheetExportDatabase()
    database.seed_export(user_id="user-1", spreadsheet_id="sheet-1")
    database.seed_event(event_id="event-1", user_id="user-1", transaction_id="txn-1")
    repository = make_repository(database)

    repository.mark_failed(
        user_id="user-1",
        error="Google Sheets write failed",
        failed_at=datetime(2026, 5, 20, 12, 30, tzinfo=timezone.utc),
    )

    export = database.exports["user-1"]
    assert export["last_synced_event_id"] is None
    assert export["last_error"] == "Google Sheets write failed"
    assert database.events[0]["id"] == "event-1"


def make_repository(
    database: "InMemorySheetExportDatabase",
) -> PostgresSheetExportRepository:
    return PostgresSheetExportRepository(
        connection_factory=database.connect,
        clock=lambda: datetime(2026, 5, 20, 12, 30, tzinfo=timezone.utc),
    )


class InMemorySheetExportDatabase:
    def __init__(self) -> None:
        self.exports: dict[str, dict[str, Any]] = {}
        self.events: list[dict[str, Any]] = []
        self.commits = 0
        self.rollbacks = 0

    def connect(self) -> "InMemorySheetExportConnection":
        return InMemorySheetExportConnection(self)

    def snapshot(self) -> dict[str, Any]:
        return {
            "exports": deepcopy(self.exports),
            "events": deepcopy(self.events),
        }

    def restore(self, snapshot: dict[str, Any]) -> None:
        self.exports = snapshot["exports"]
        self.events = snapshot["events"]

    def seed_export(
        self,
        *,
        user_id: str,
        spreadsheet_id: str,
        enabled: bool = True,
        last_synced_event_id: str | None = None,
        last_error: str | None = None,
    ) -> None:
        self.exports[user_id] = {
            "user_id": user_id,
            "spreadsheet_id": spreadsheet_id,
            "enabled": enabled,
            "last_synced_event_id": last_synced_event_id,
            "last_synced_at": None,
            "last_error": last_error,
            "created_at": "2026-05-19T10:00:00+00:00",
            "updated_at": "2026-05-19T10:00:00+00:00",
        }

    def seed_event(
        self,
        *,
        event_id: str,
        user_id: str,
        transaction_id: str,
        amount: Decimal = Decimal("12.30"),
    ) -> None:
        self.events.append(
            {
                "event_id": event_id,
                "id": event_id,
                "event_created_at": f"2026-05-19T10:0{len(self.events)}:00+00:00",
                "user_id": user_id,
                "transaction_id": transaction_id,
                "transaction_date": "2026-05-19",
                "amount": amount,
                "currency": "SGD",
                "transaction_type": "expense",
                "category": "Dining",
                "merchant": "coffee shop",
                "payment_method": "card",
                "note": "lunch",
                "created_at": "2026-05-19T10:00:00+00:00",
                "updated_at": "2026-05-19T10:00:00+00:00",
            }
        )


class InMemorySheetExportConnection:
    def __init__(self, database: InMemorySheetExportDatabase) -> None:
        self.database = database
        self._snapshot: dict[str, Any] | None = None

    def __enter__(self) -> "InMemorySheetExportConnection":
        self._snapshot = self.database.snapshot()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if exc_type is None:
            self.database.commits += 1
            return
        assert self._snapshot is not None
        self.database.restore(self._snapshot)
        self.database.rollbacks += 1

    def execute(
        self,
        query: str,
        params: dict[str, Any] | None = None,
    ) -> "InMemoryCursor":
        params = params or {}
        operation = _operation_name(query)
        rows = getattr(self, _method_name(operation))(params)
        return InMemoryCursor(rows)

    def _sheet_export_repository_upsert_export_config(
        self,
        params: dict[str, Any],
    ) -> list[dict[str, Any]]:
        existing = self.database.exports.get(params["user_id"])
        if existing is None:
            self.database.exports[params["user_id"]] = {
                "user_id": params["user_id"],
                "spreadsheet_id": params["spreadsheet_id"],
                "enabled": params["enabled"],
                "last_synced_event_id": None,
                "last_synced_at": None,
                "last_error": None,
                "created_at": params["now"],
                "updated_at": params["now"],
            }
        else:
            spreadsheet_changed = existing["spreadsheet_id"] != params["spreadsheet_id"]
            existing["spreadsheet_id"] = params["spreadsheet_id"]
            existing["enabled"] = params["enabled"]
            if spreadsheet_changed:
                existing["last_synced_event_id"] = None
                existing["last_synced_at"] = None
            existing["last_error"] = None
            existing["updated_at"] = params["now"]
        return [self.database.exports[params["user_id"]]]

    def _sheet_export_repository_list_enabled_exports(
        self,
        params: dict[str, Any],
    ) -> list[dict[str, Any]]:
        return [
            self.database.exports[user_id]
            for user_id in sorted(self.database.exports)
            if self.database.exports[user_id]["enabled"]
        ]

    def _sheet_export_repository_list_pending_events(
        self,
        params: dict[str, Any],
    ) -> list[dict[str, Any]]:
        rows = [
            event
            for event in self.database.events
            if event["user_id"] == params["user_id"]
        ]
        last_synced_event_id = params["last_synced_event_id"]
        if last_synced_event_id is not None:
            cursor_index = next(
                index
                for index, event in enumerate(rows)
                if event["event_id"] == last_synced_event_id
            )
            rows = rows[cursor_index + 1 :]
        return rows[: params["limit"]]

    def _sheet_export_repository_mark_synced(
        self,
        params: dict[str, Any],
    ) -> list[dict[str, Any]]:
        export = self.database.exports[params["user_id"]]
        export["last_synced_event_id"] = (
            params["last_event_id"] or export["last_synced_event_id"]
        )
        export["last_synced_at"] = params["synced_at"]
        export["last_error"] = None
        export["updated_at"] = params["synced_at"]
        return []

    def _sheet_export_repository_mark_failed(
        self,
        params: dict[str, Any],
    ) -> list[dict[str, Any]]:
        export = self.database.exports[params["user_id"]]
        export["last_error"] = params["last_error"]
        export["updated_at"] = params["failed_at"]
        return []


class InMemoryCursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def fetchone(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self._rows)


def _operation_name(query: str) -> str:
    for line in query.splitlines():
        line = line.strip()
        if line:
            return line.removeprefix("-- ")
    raise AssertionError("query is missing operation comment")


def _method_name(operation: str) -> str:
    return "_" + operation.replace(".", "_")
