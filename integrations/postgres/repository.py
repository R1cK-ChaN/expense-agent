import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

from integrations.google_sheets.repository import (
    ALLOWED_UPDATE_FIELDS,
    InvalidTransactionUpdateError,
    TransactionNotFoundError,
    TransactionRecord,
    TransactionRepositoryError,
)


UPDATE_FIELD_COLUMNS = {
    "date": "transaction_date",
    "amount": "amount",
    "currency": "currency",
    "type": "transaction_type",
    "category": "category",
    "merchant": "merchant",
    "payment_method": "payment_method",
    "note": "note",
}


class PostgresCursor(Protocol):
    def fetchone(self) -> Mapping[str, Any] | None:
        raise NotImplementedError

    def fetchall(self) -> list[Mapping[str, Any]]:
        raise NotImplementedError


class PostgresConnection(Protocol):
    def __enter__(self) -> "PostgresConnection":
        raise NotImplementedError

    def __exit__(
        self,
        exc_type: object,
        exc: object,
        traceback: object,
    ) -> None:
        raise NotImplementedError

    def execute(
        self,
        query: str,
        params: Mapping[str, Any] | None = None,
    ) -> PostgresCursor:
        raise NotImplementedError


@dataclass(frozen=True)
class _Identity:
    id: str
    user_id: str


class PostgresTransactionRepository:
    def __init__(
        self,
        *,
        database_url: str | None = None,
        connection_factory: Callable[[], PostgresConnection] | None = None,
        timezone: str = "Asia/Singapore",
        clock: Callable[[], datetime] | None = None,
        uuid_factory: Callable[[], str] | None = None,
    ) -> None:
        if connection_factory is None:
            connection_factory = _build_connection_factory(database_url)

        self._connection_factory = connection_factory
        self._timezone = timezone
        self._clock = clock or _utc_now
        self._uuid_factory = uuid_factory or _default_uuid

    def append_transaction(self, record: TransactionRecord) -> TransactionRecord:
        try:
            with self._connection_factory() as connection:
                identity = self._get_or_create_identity(connection, record)
                message_id = self._insert_inbound_message(
                    connection,
                    identity=identity,
                    record=record,
                )
                existing_record = self._select_transaction_by_message_id(
                    connection,
                    message_id,
                )
                if existing_record is not None:
                    return existing_record

                database_transaction_id = self._insert_transaction(
                    connection,
                    identity=identity,
                    message_id=message_id,
                    record=record,
                )
                if database_transaction_id is None:
                    existing_record = self._select_transaction_by_message_id(
                        connection,
                        message_id,
                    )
                    if existing_record is not None:
                        return existing_record
                    raise TransactionRepositoryError(
                        "PostgreSQL transaction insert did not return a row."
                    )

                self._insert_transaction_event(
                    connection,
                    transaction_id=database_transaction_id,
                    message_id=message_id,
                    event_type="created",
                    old_values=None,
                    new_values=_record_event_values(record),
                    created_at=record.created_at,
                )
                return record
        except TransactionRepositoryError:
            raise
        except Exception as error:
            raise TransactionRepositoryError(
                "Failed to append transaction to PostgreSQL."
            ) from error

    def find_by_source_message(
        self,
        *,
        source_platform: str,
        user_id: str,
        chat_id: str,
        message_id: str,
    ) -> TransactionRecord | None:
        try:
            with self._connection_factory() as connection:
                row = connection.execute(
                    SELECT_TRANSACTION_BY_SOURCE_MESSAGE_SQL,
                    {
                        "platform": str(source_platform),
                        "platform_user_id": str(user_id),
                        "platform_chat_id": str(chat_id),
                        "provider_dedupe_key": str(message_id),
                    },
                ).fetchone()
        except Exception as error:
            raise TransactionRepositoryError(
                "Failed to read transaction by source message from PostgreSQL."
            ) from error

        return None if row is None else _row_to_record(row, self._timezone)

    def find_by_telegram_message(
        self,
        *,
        user_id: str,
        chat_id: str,
        message_id: str,
    ) -> TransactionRecord | None:
        return self.find_by_source_message(
            source_platform="telegram",
            user_id=user_id,
            chat_id=chat_id,
            message_id=message_id,
        )

    def get_latest_transaction(
        self,
        *,
        source_platform: str,
        user_id: str,
    ) -> TransactionRecord | None:
        try:
            with self._connection_factory() as connection:
                row = connection.execute(
                    SELECT_LATEST_TRANSACTION_SQL,
                    {
                        "platform": str(source_platform),
                        "platform_user_id": str(user_id),
                    },
                ).fetchone()
        except Exception as error:
            raise TransactionRepositoryError(
                "Failed to read latest transaction from PostgreSQL."
            ) from error

        return None if row is None else _row_to_record(row, self._timezone)

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

        try:
            with self._connection_factory() as connection:
                old_row = connection.execute(
                    SELECT_TRANSACTION_FOR_UPDATE_SQL,
                    {"external_id": str(transaction_id)},
                ).fetchone()
                if old_row is None:
                    raise TransactionNotFoundError(
                        f"Transaction not found: {transaction_id}"
                    )

                old_record = _row_to_record(old_row, self._timezone)
                updated_at = _format_timestamp(self._clock(), self._timezone)
                update_fields = _database_update_fields(fields)
                update_params = {
                    "external_id": str(transaction_id),
                    "updated_at": updated_at,
                    "fields": update_fields,
                    **update_fields,
                }
                updated_row = connection.execute(
                    _update_transaction_sql(update_fields),
                    update_params,
                ).fetchone()
                if updated_row is None:
                    raise TransactionNotFoundError(
                        f"Transaction not found: {transaction_id}"
                    )

                updated_record = _row_to_record(updated_row, self._timezone)
                self._insert_transaction_event(
                    connection,
                    transaction_id=str(updated_row["database_id"]),
                    message_id=None,
                    event_type="updated",
                    old_values=_record_event_values(old_record),
                    new_values=_record_event_values(updated_record),
                    created_at=updated_at,
                )
        except (InvalidTransactionUpdateError, TransactionNotFoundError):
            raise
        except Exception as error:
            raise TransactionRepositoryError(
                "Failed to update transaction in PostgreSQL."
            ) from error

        return updated_record

    def list_monthly_expenses(
        self,
        *,
        source_platform: str,
        user_id: str,
        month: str,
    ) -> list[TransactionRecord]:
        _validate_month(month)
        month_start, month_end = _month_bounds(month)
        try:
            with self._connection_factory() as connection:
                rows = connection.execute(
                    SELECT_MONTHLY_TRANSACTIONS_SQL,
                    {
                        "platform": str(source_platform),
                        "platform_user_id": str(user_id),
                        "month_start": month_start,
                        "month_end": month_end,
                    },
                ).fetchall()
        except Exception as error:
            raise TransactionRepositoryError(
                "Failed to list monthly expenses from PostgreSQL."
            ) from error

        return [_row_to_record(row, self._timezone) for row in rows]

    def sum_monthly_expense(
        self,
        *,
        source_platform: str,
        user_id: str,
        month: str,
        currency: str,
    ) -> Decimal:
        total = Decimal("0")
        for record in self.list_monthly_expenses(
            source_platform=source_platform,
            user_id=user_id,
            month=month,
        ):
            if record.currency == currency:
                total += record.amount
        return total

    def list_transactions(self) -> list[TransactionRecord]:
        try:
            with self._connection_factory() as connection:
                rows = connection.execute(SELECT_ALL_TRANSACTIONS_SQL).fetchall()
        except Exception as error:
            raise TransactionRepositoryError(
                "Failed to list transactions from PostgreSQL."
            ) from error

        return [_row_to_record(row, self._timezone) for row in rows]

    def _get_or_create_identity(
        self,
        connection: PostgresConnection,
        record: TransactionRecord,
    ) -> _Identity:
        row = connection.execute(
            UPSERT_IDENTITY_SQL,
            {
                "user_id": self._uuid_factory(),
                "identity_id": self._uuid_factory(),
                "platform": record.source_platform,
                "platform_user_id": record.source_user_id,
                "username": record.source_username,
                "display_name": record.source_user_display_name,
                "created_at": record.created_at,
                "updated_at": record.created_at,
            },
        ).fetchone()
        if row is None:
            raise TransactionRepositoryError(
                "PostgreSQL identity upsert did not return a row."
            )
        return _Identity(id=str(row["id"]), user_id=str(row["user_id"]))

    def _insert_inbound_message(
        self,
        connection: PostgresConnection,
        *,
        identity: _Identity,
        record: TransactionRecord,
    ) -> str:
        row = connection.execute(
            INSERT_INBOUND_MESSAGE_SQL,
            {
                "id": self._uuid_factory(),
                "user_id": identity.user_id,
                "identity_id": identity.id,
                "platform": record.source_platform,
                "platform_chat_id": record.source_chat_id,
                "platform_message_id": record.source_message_id,
                "provider_dedupe_key": _provider_dedupe_key(record),
                "provider_message_type": "text",
                "provider_event_type": None,
                "normalized_text": None,
                "received_at": record.created_at,
                "created_at": record.created_at,
            },
        ).fetchone()
        if row is None:
            raise TransactionRepositoryError(
                "PostgreSQL inbound message insert did not return a row."
            )
        return str(row["id"])

    def _select_transaction_by_message_id(
        self,
        connection: PostgresConnection,
        message_id: str,
    ) -> TransactionRecord | None:
        row = connection.execute(
            SELECT_TRANSACTION_BY_MESSAGE_ID_SQL,
            {"message_id": message_id},
        ).fetchone()
        return None if row is None else _row_to_record(row, self._timezone)

    def _insert_transaction(
        self,
        connection: PostgresConnection,
        *,
        identity: _Identity,
        message_id: str,
        record: TransactionRecord,
    ) -> str | None:
        row = connection.execute(
            INSERT_TRANSACTION_SQL,
            {
                "id": self._uuid_factory(),
                "external_id": record.id,
                "user_id": identity.user_id,
                "created_from_message_id": message_id,
                "transaction_date": record.date,
                "amount": record.amount,
                "currency": record.currency,
                "transaction_type": record.type,
                "category": record.category,
                "merchant": record.merchant,
                "payment_method": record.payment_method,
                "note": record.note,
                "created_at": record.created_at,
                "updated_at": record.updated_at,
            },
        ).fetchone()
        return None if row is None else str(row["id"])

    def _insert_transaction_event(
        self,
        connection: PostgresConnection,
        *,
        transaction_id: str,
        message_id: str | None,
        event_type: str,
        old_values: Mapping[str, object] | None,
        new_values: Mapping[str, object] | None,
        created_at: str,
    ) -> None:
        connection.execute(
            INSERT_TRANSACTION_EVENT_SQL,
            {
                "id": self._uuid_factory(),
                "transaction_id": transaction_id,
                "message_id": message_id,
                "event_type": event_type,
                "old_values": _jsonb_param(old_values),
                "new_values": _jsonb_param(new_values),
                "created_at": created_at,
            },
        )


RECORD_SELECT_COLUMNS = """
    t.id as database_id,
    t.external_id as id,
    t.transaction_date as date,
    t.amount as amount,
    t.currency as currency,
    t.transaction_type as type,
    t.category as category,
    t.merchant as merchant,
    t.payment_method as payment_method,
    t.note as note,
    m.platform as source_platform,
    source_ui.platform_user_id as source_user_id,
    source_ui.username as source_username,
    source_ui.display_name as source_user_display_name,
    m.platform_chat_id as source_chat_id,
    m.platform_message_id as source_message_id,
    t.created_at as created_at,
    t.updated_at as updated_at
"""


RECORD_SELECT_COLUMNS_WITH_IDENTITY_FALLBACK = """
    t.id as database_id,
    t.external_id as id,
    t.transaction_date as date,
    t.amount as amount,
    t.currency as currency,
    t.transaction_type as type,
    t.category as category,
    t.merchant as merchant,
    t.payment_method as payment_method,
    t.note as note,
    coalesce(m.platform, fallback_ui.platform, '') as source_platform,
    coalesce(
        source_ui.platform_user_id,
        fallback_ui.platform_user_id,
        ''
    ) as source_user_id,
    coalesce(source_ui.username, fallback_ui.username) as source_username,
    coalesce(
        source_ui.display_name,
        fallback_ui.display_name
    ) as source_user_display_name,
    coalesce(m.platform_chat_id, '') as source_chat_id,
    coalesce(m.platform_message_id, '') as source_message_id,
    t.created_at as created_at,
    t.updated_at as updated_at
"""


UPSERT_IDENTITY_SQL = """
-- postgres_repository.upsert_identity
with existing_identity as (
    select id, user_id
    from user_identities
    where platform = %(platform)s
      and platform_user_id = %(platform_user_id)s
),
created_user as (
    insert into users (id, created_at, updated_at)
    select %(user_id)s, %(created_at)s, %(updated_at)s
    where not exists (select 1 from existing_identity)
    returning id
),
inserted_identity as (
    insert into user_identities (
        id,
        user_id,
        platform,
        platform_user_id,
        username,
        display_name,
        created_at,
        updated_at
    )
    select
        %(identity_id)s,
        created_user.id,
        %(platform)s,
        %(platform_user_id)s,
        %(username)s,
        %(display_name)s,
        %(created_at)s,
        %(updated_at)s
    from created_user
    on conflict (platform, platform_user_id)
    do update set username = excluded.username,
        display_name = excluded.display_name,
        updated_at = excluded.updated_at
    returning id, user_id
),
updated_existing_identity as (
    update user_identities
    set username = %(username)s,
        display_name = %(display_name)s,
        updated_at = %(updated_at)s
    where id in (select id from existing_identity)
    returning id, user_id
),
cleanup_created_user as (
    delete from users
    where id = %(user_id)s
      and exists (
          select 1
          from inserted_identity
          where user_id <> %(user_id)s
      )
)
select id, user_id
from inserted_identity
union all
select id, user_id
from updated_existing_identity
limit 1
"""


INSERT_INBOUND_MESSAGE_SQL = """
-- postgres_repository.insert_inbound_message
insert into inbound_messages (
    id,
    user_id,
    identity_id,
    platform,
    platform_chat_id,
    platform_message_id,
    provider_dedupe_key,
    provider_message_type,
    provider_event_type,
    normalized_text,
    received_at,
    created_at
)
values (
    %(id)s,
    %(user_id)s,
    %(identity_id)s,
    %(platform)s,
    %(platform_chat_id)s,
    %(platform_message_id)s,
    %(provider_dedupe_key)s,
    %(provider_message_type)s,
    %(provider_event_type)s,
    %(normalized_text)s,
    %(received_at)s,
    %(created_at)s
)
on conflict (platform, platform_chat_id, provider_dedupe_key)
do update set platform_message_id = inbound_messages.platform_message_id
returning id
"""


INSERT_TRANSACTION_SQL = """
-- postgres_repository.insert_transaction
insert into transactions (
    id,
    external_id,
    user_id,
    created_from_message_id,
    transaction_date,
    amount,
    currency,
    transaction_type,
    category,
    merchant,
    payment_method,
    note,
    created_at,
    updated_at
)
values (
    %(id)s,
    %(external_id)s,
    %(user_id)s,
    %(created_from_message_id)s,
    %(transaction_date)s,
    %(amount)s,
    %(currency)s,
    %(transaction_type)s,
    %(category)s,
    %(merchant)s,
    %(payment_method)s,
    %(note)s,
    %(created_at)s,
    %(updated_at)s
)
on conflict (created_from_message_id) do nothing
returning id
"""


INSERT_TRANSACTION_EVENT_SQL = """
-- postgres_repository.insert_transaction_event
insert into transaction_events (
    id,
    transaction_id,
    message_id,
    event_type,
    old_values,
    new_values,
    created_at
)
values (
    %(id)s,
    %(transaction_id)s,
    %(message_id)s,
    %(event_type)s,
    %(old_values)s::jsonb,
    %(new_values)s::jsonb,
    %(created_at)s
)
"""


SELECT_TRANSACTION_BY_MESSAGE_ID_SQL = f"""
-- postgres_repository.select_transaction_by_message_id
select
{RECORD_SELECT_COLUMNS}
from transactions t
join inbound_messages m on m.id = t.created_from_message_id
join user_identities source_ui on source_ui.id = m.identity_id
where m.id = %(message_id)s
limit 1
"""


SELECT_TRANSACTION_BY_SOURCE_MESSAGE_SQL = f"""
-- postgres_repository.select_transaction_by_source_message
select
{RECORD_SELECT_COLUMNS}
from inbound_messages m
join user_identities source_ui on source_ui.id = m.identity_id
join transactions t on t.created_from_message_id = m.id
where m.platform = %(platform)s
  and source_ui.platform_user_id = %(platform_user_id)s
  and m.platform_chat_id = %(platform_chat_id)s
  and m.provider_dedupe_key = %(provider_dedupe_key)s
limit 1
"""


SELECT_LATEST_TRANSACTION_SQL = f"""
-- postgres_repository.select_latest_transaction
select
{RECORD_SELECT_COLUMNS_WITH_IDENTITY_FALLBACK}
from user_identities request_ui
join transactions t on t.user_id = request_ui.user_id
left join inbound_messages m on m.id = t.created_from_message_id
left join user_identities source_ui on source_ui.id = m.identity_id
join user_identities fallback_ui on fallback_ui.id = request_ui.id
where request_ui.platform = %(platform)s
  and request_ui.platform_user_id = %(platform_user_id)s
  and t.transaction_type = 'expense'
order by t.created_at desc, t.id desc
limit 1
"""


SELECT_TRANSACTION_FOR_UPDATE_SQL = f"""
-- postgres_repository.select_transaction_for_update
select
{RECORD_SELECT_COLUMNS_WITH_IDENTITY_FALLBACK}
from transactions t
left join inbound_messages m on m.id = t.created_from_message_id
left join user_identities source_ui on source_ui.id = m.identity_id
left join lateral (
    select *
    from user_identities
    where user_id = t.user_id
    order by created_at asc, id asc
    limit 1
) fallback_ui on true
where t.external_id = %(external_id)s
for update of t
"""


SELECT_MONTHLY_TRANSACTIONS_SQL = f"""
-- postgres_repository.select_monthly_transactions
select
{RECORD_SELECT_COLUMNS_WITH_IDENTITY_FALLBACK}
from user_identities request_ui
join transactions t on t.user_id = request_ui.user_id
left join inbound_messages m on m.id = t.created_from_message_id
left join user_identities source_ui on source_ui.id = m.identity_id
join user_identities fallback_ui on fallback_ui.id = request_ui.id
where request_ui.platform = %(platform)s
  and request_ui.platform_user_id = %(platform_user_id)s
  and t.transaction_type = 'expense'
  and t.transaction_date >= %(month_start)s
  and t.transaction_date < %(month_end)s
order by t.transaction_date asc, t.created_at asc, t.id asc
"""


SELECT_ALL_TRANSACTIONS_SQL = f"""
-- postgres_repository.select_all_transactions
select
{RECORD_SELECT_COLUMNS_WITH_IDENTITY_FALLBACK}
from transactions t
left join inbound_messages m on m.id = t.created_from_message_id
left join user_identities source_ui on source_ui.id = m.identity_id
left join lateral (
    select *
    from user_identities
    where user_id = t.user_id
    order by created_at asc, id asc
    limit 1
) fallback_ui on true
order by t.transaction_date asc, t.created_at asc, t.id asc
"""


def _update_transaction_sql(update_fields: Mapping[str, object]) -> str:
    assignments = [
        f"{column_name} = %({column_name})s" for column_name in update_fields
    ]
    assignments.append("updated_at = %(updated_at)s")
    set_clause = ",\n    ".join(assignments)
    return f"""
-- postgres_repository.update_transaction
with updated as (
    update transactions
    set {set_clause}
    where external_id = %(external_id)s
    returning *
)
select
{RECORD_SELECT_COLUMNS_WITH_IDENTITY_FALLBACK}
from updated t
left join inbound_messages m on m.id = t.created_from_message_id
left join user_identities source_ui on source_ui.id = m.identity_id
left join lateral (
    select *
    from user_identities
    where user_id = t.user_id
    order by created_at asc, id asc
    limit 1
) fallback_ui on true
"""


def _database_update_fields(fields: Mapping[str, object]) -> dict[str, object]:
    return {
        UPDATE_FIELD_COLUMNS[field_name]: _field_value_to_database_value(
            field_name,
            value,
        )
        for field_name, value in fields.items()
    }


def _field_value_to_database_value(field_name: str, value: object) -> object:
    if field_name == "amount":
        return _to_decimal(value)
    if field_name in {"merchant", "payment_method", "note"}:
        return _optional_string(value)
    return str(value)


def _row_to_record(row: Mapping[str, Any], timezone_name: str) -> TransactionRecord:
    return TransactionRecord(
        id=str(row["id"]),
        date=_date_to_string(row["date"]),
        amount=_to_decimal(row["amount"]),
        currency=str(row["currency"]),
        type=str(row["type"]),
        category=str(row["category"]),
        merchant=_optional_string(row["merchant"]),
        payment_method=_optional_string(row["payment_method"]),
        note=_optional_string(row["note"]),
        source_platform=str(row["source_platform"]),
        source_user_id=str(row["source_user_id"]),
        source_username=_optional_string(row["source_username"]),
        source_user_display_name=_optional_string(
            row["source_user_display_name"]
        ),
        source_chat_id=str(row["source_chat_id"]),
        source_message_id=str(row["source_message_id"] or ""),
        created_at=_timestamp_to_string(row["created_at"], timezone_name),
        updated_at=_timestamp_to_string(row["updated_at"], timezone_name),
    )


def _record_event_values(record: TransactionRecord) -> dict[str, object]:
    return {
        "id": record.id,
        "date": record.date,
        "amount": format(record.amount, "f"),
        "currency": record.currency,
        "type": record.type,
        "category": record.category,
        "merchant": record.merchant,
        "payment_method": record.payment_method,
        "note": record.note,
        "source_platform": record.source_platform,
        "source_user_id": record.source_user_id,
        "source_username": record.source_username,
        "source_user_display_name": record.source_user_display_name,
        "source_chat_id": record.source_chat_id,
        "source_message_id": record.source_message_id,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _jsonb_param(value: Mapping[str, object] | None) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _provider_dedupe_key(record: TransactionRecord) -> str:
    return str(record.source_message_id)


def _date_to_string(value: object) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


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


def _month_bounds(month: str) -> tuple[str, str]:
    year = int(month[:4])
    month_number = int(month[5:])
    if month_number == 12:
        next_year = year + 1
        next_month = 1
    else:
        next_year = year
        next_month = month_number + 1
    return f"{year:04d}-{month_number:02d}-01", f"{next_year:04d}-{next_month:02d}-01"


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
                "psycopg is required to use PostgresTransactionRepository."
            ) from error

        return psycopg.connect(database_url, row_factory=dict_row)

    return connect


def _default_uuid() -> str:
    return str(uuid4())


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
