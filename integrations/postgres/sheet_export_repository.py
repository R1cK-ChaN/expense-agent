from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from core.sheet_export import (
    LedgerTransaction,
    SheetExportConfig,
    SheetExportEvent,
)
from integrations.postgres.repository import (
    PostgresConnection,
    TransactionRepositoryError,
)


class PostgresSheetExportRepository:
    def __init__(
        self,
        *,
        database_url: str | None = None,
        connection_factory: Callable[[], PostgresConnection] | None = None,
        timezone: str = "Asia/Singapore",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if connection_factory is None:
            connection_factory = _build_connection_factory(database_url)

        self._connection_factory = connection_factory
        self._timezone = timezone
        self._clock = clock or _utc_now

    def upsert_export_config(
        self,
        *,
        user_id: str,
        spreadsheet_id: str,
        enabled: bool = True,
    ) -> SheetExportConfig:
        try:
            with self._connection_factory() as connection:
                row = connection.execute(
                    UPSERT_EXPORT_CONFIG_SQL,
                    {
                        "user_id": str(user_id),
                        "spreadsheet_id": str(spreadsheet_id),
                        "enabled": enabled,
                        "now": self._clock(),
                    },
                ).fetchone()
        except Exception as error:
            raise TransactionRepositoryError(
                "Failed to upsert Google Sheets export config in PostgreSQL."
            ) from error

        if row is None:
            raise TransactionRepositoryError(
                "PostgreSQL export config upsert did not return a row."
            )
        return _row_to_config(row, self._timezone)

    def list_enabled_exports(self) -> list[SheetExportConfig]:
        try:
            with self._connection_factory() as connection:
                rows = connection.execute(LIST_ENABLED_EXPORTS_SQL).fetchall()
        except Exception as error:
            raise TransactionRepositoryError(
                "Failed to list Google Sheets export configs from PostgreSQL."
            ) from error

        return [_row_to_config(row, self._timezone) for row in rows]

    def list_pending_events(
        self,
        config: SheetExportConfig,
        *,
        limit: int,
    ) -> list[SheetExportEvent]:
        try:
            with self._connection_factory() as connection:
                rows = connection.execute(
                    LIST_PENDING_EVENTS_SQL,
                    {
                        "user_id": config.user_id,
                        "last_synced_event_id": config.last_synced_event_id,
                        "limit": limit,
                    },
                ).fetchall()
        except Exception as error:
            raise TransactionRepositoryError(
                "Failed to list pending Google Sheets export events from PostgreSQL."
            ) from error

        return [_row_to_event(row, self._timezone) for row in rows]

    def mark_synced(
        self,
        *,
        user_id: str,
        last_event_id: str | None,
        synced_at: datetime,
    ) -> None:
        try:
            with self._connection_factory() as connection:
                connection.execute(
                    MARK_SYNCED_SQL,
                    {
                        "user_id": str(user_id),
                        "last_event_id": last_event_id,
                        "synced_at": synced_at,
                    },
                )
        except Exception as error:
            raise TransactionRepositoryError(
                "Failed to mark Google Sheets export as synced in PostgreSQL."
            ) from error

    def mark_failed(
        self,
        *,
        user_id: str,
        error: str,
        failed_at: datetime,
    ) -> None:
        try:
            with self._connection_factory() as connection:
                connection.execute(
                    MARK_FAILED_SQL,
                    {
                        "user_id": str(user_id),
                        "last_error": error[:2000],
                        "failed_at": failed_at,
                    },
                )
        except Exception as failure:
            raise TransactionRepositoryError(
                "Failed to mark Google Sheets export as failed in PostgreSQL."
            ) from failure


UPSERT_EXPORT_CONFIG_SQL = """
-- sheet_export_repository.upsert_export_config
insert into google_sheet_exports (
    user_id,
    spreadsheet_id,
    enabled,
    created_at,
    updated_at
)
values (
    %(user_id)s,
    %(spreadsheet_id)s,
    %(enabled)s,
    %(now)s,
    %(now)s
)
on conflict (user_id)
do update set spreadsheet_id = excluded.spreadsheet_id,
    enabled = excluded.enabled,
    last_synced_event_id = case
        when google_sheet_exports.spreadsheet_id is distinct from excluded.spreadsheet_id
        then null
        else google_sheet_exports.last_synced_event_id
    end,
    last_synced_at = case
        when google_sheet_exports.spreadsheet_id is distinct from excluded.spreadsheet_id
        then null
        else google_sheet_exports.last_synced_at
    end,
    last_error = null,
    updated_at = excluded.updated_at
returning
    user_id,
    spreadsheet_id,
    enabled,
    last_synced_event_id,
    last_synced_at,
    last_error
"""


LIST_ENABLED_EXPORTS_SQL = """
-- sheet_export_repository.list_enabled_exports
select
    user_id,
    spreadsheet_id,
    enabled,
    last_synced_event_id,
    last_synced_at,
    last_error
from google_sheet_exports
where enabled = true
order by user_id asc
"""


LIST_PENDING_EVENTS_SQL = """
-- sheet_export_repository.list_pending_events
select
    e.id as event_id,
    e.created_at as event_created_at,
    t.user_id as user_id,
    t.external_id as transaction_id,
    t.transaction_date as transaction_date,
    t.amount as amount,
    t.currency as currency,
    t.transaction_type as transaction_type,
    t.category as category,
    t.merchant as merchant,
    t.payment_method as payment_method,
    t.note as note,
    t.created_at as created_at,
    t.updated_at as updated_at
from transaction_events e
join transactions t on t.id = e.transaction_id
where t.user_id = %(user_id)s
  and (
      %(last_synced_event_id)s is null
      or (
          e.created_at,
          e.id
      ) > (
          select cursor_event.created_at, cursor_event.id
          from transaction_events cursor_event
          where cursor_event.id = %(last_synced_event_id)s
      )
  )
order by e.created_at asc, e.id asc
limit %(limit)s
"""


MARK_SYNCED_SQL = """
-- sheet_export_repository.mark_synced
update google_sheet_exports
set last_synced_event_id = coalesce(
        %(last_event_id)s,
        last_synced_event_id
    ),
    last_synced_at = %(synced_at)s,
    last_error = null,
    updated_at = %(synced_at)s
where user_id = %(user_id)s
"""


MARK_FAILED_SQL = """
-- sheet_export_repository.mark_failed
update google_sheet_exports
set last_error = %(last_error)s,
    updated_at = %(failed_at)s
where user_id = %(user_id)s
"""


def _row_to_config(
    row: Mapping[str, Any],
    timezone_name: str,
) -> SheetExportConfig:
    return SheetExportConfig(
        user_id=str(row["user_id"]),
        spreadsheet_id=str(row["spreadsheet_id"]),
        enabled=bool(row["enabled"]),
        last_synced_event_id=_optional_string(row["last_synced_event_id"]),
        last_synced_at=_optional_timestamp_to_string(
            row["last_synced_at"],
            timezone_name,
        ),
        last_error=_optional_string(row["last_error"]),
    )


def _row_to_event(
    row: Mapping[str, Any],
    timezone_name: str,
) -> SheetExportEvent:
    return SheetExportEvent(
        event_id=str(row["event_id"]),
        user_id=str(row["user_id"]),
        transaction=LedgerTransaction(
            id=str(row["transaction_id"]),
            date=_date_to_string(row["transaction_date"]),
            amount=_to_decimal(row["amount"]),
            currency=str(row["currency"]),
            type=str(row["transaction_type"]),
            category=str(row["category"]),
            merchant=_optional_string(row["merchant"]),
            payment_method=_optional_string(row["payment_method"]),
            note=_optional_string(row["note"]),
            created_at=_timestamp_to_string(row["created_at"], timezone_name),
            updated_at=_timestamp_to_string(row["updated_at"], timezone_name),
        ),
        event_created_at=_timestamp_to_string(row["event_created_at"], timezone_name),
    )


def _date_to_string(value: object) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _optional_timestamp_to_string(value: object, timezone_name: str) -> str | None:
    if value is None:
        return None
    return _timestamp_to_string(value, timezone_name)


def _timestamp_to_string(value: object, timezone_name: str) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=ZoneInfo(timezone_name))
        return value.astimezone(ZoneInfo(timezone_name)).isoformat()
    return str(value)


def _to_decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception as error:
        raise TransactionRepositoryError(f"Invalid transaction amount: {value}") from error


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _build_connection_factory(
    database_url: str | None,
) -> Callable[[], PostgresConnection]:
    if not database_url:
        raise ValueError("database_url or connection_factory is required")

    def connect() -> PostgresConnection:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as error:
            raise TransactionRepositoryError(
                "psycopg is required to use PostgresSheetExportRepository."
            ) from error

        return psycopg.connect(database_url, row_factory=dict_row)

    return connect


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
