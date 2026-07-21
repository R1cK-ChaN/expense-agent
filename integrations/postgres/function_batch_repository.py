"""PostgreSQL unit-of-work for delivery-idempotent function batches."""

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from decimal import Decimal
from typing import Any
from uuid import uuid4

from core.function_batch_executor import (
    BatchStart,
    CreateExpenseCommand,
    UpdateTargetNotFoundError,
    UpdateLatestExpenseCommand,
    WriteCommand,
)
from core.messages import InboundMessage
from core.pending_requests import PendingRequest
from integrations.google_sheets.repository import (
    TransactionRecord,
    TransactionRepositoryError,
)
from integrations.postgres.repository import (
    INSERT_INBOUND_MESSAGE_SQL,
    INSERT_TRANSACTION_EVENT_SQL,
    UPSERT_IDENTITY_SQL,
    PostgresConnection,
    _build_connection_factory,
    _jsonb_param,
    _record_event_values,
)


class PostgresFunctionBatchRepository:
    def __init__(
        self,
        *,
        database_url: str | None = None,
        connection_factory: Callable[[], PostgresConnection] | None = None,
        uuid_factory: Callable[[], str] | None = None,
    ) -> None:
        self._connection_factory = connection_factory or _build_connection_factory(
            database_url
        )
        self._uuid_factory = uuid_factory or (lambda: str(uuid4()))

    def begin_batch(
        self,
        request: InboundMessage,
        accepted_calls: Sequence[Mapping[str, object]],
    ) -> BatchStart:
        received_at = request.received_at.isoformat()
        try:
            with self._connection_factory() as connection:
                identity_row = connection.execute(
                    UPSERT_IDENTITY_SQL,
                    {
                        "user_id": self._uuid_factory(),
                        "identity_id": self._uuid_factory(),
                        "platform": request.source_platform,
                        "platform_user_id": request.source_user_id,
                        "username": request.source_username,
                        "display_name": request.source_user_display_name,
                        "created_at": received_at,
                        "updated_at": received_at,
                    },
                ).fetchone()
                if identity_row is None:
                    raise TransactionRepositoryError(
                        "PostgreSQL identity upsert did not return a row."
                    )
                message_row = connection.execute(
                    INSERT_INBOUND_MESSAGE_SQL,
                    {
                        "id": self._uuid_factory(),
                        "user_id": str(identity_row["user_id"]),
                        "identity_id": str(identity_row["id"]),
                        "platform": request.source_platform,
                        "platform_chat_id": request.source_chat_id,
                        "platform_message_id": request.source_message_id,
                        "provider_dedupe_key": request.source_message_id,
                        "provider_message_type": "text",
                        "provider_event_type": None,
                        "normalized_text": request.message_text,
                        "received_at": received_at,
                        "created_at": received_at,
                    },
                ).fetchone()
                if message_row is None:
                    raise TransactionRepositoryError(
                        "PostgreSQL inbound message insert did not return a row."
                    )
                legacy_row = connection.execute(
                    SELECT_LEGACY_TRANSACTION_SQL,
                    {"inbound_message_id": str(message_row["id"])},
                ).fetchone()
                if legacy_row is not None:
                    return BatchStart(
                        batch_id=str(message_row["id"]),
                        stored_reply=_legacy_transaction_reply(legacy_row),
                    )
                batch_row = connection.execute(
                    BEGIN_BATCH_SQL,
                    {
                        "id": self._uuid_factory(),
                        "inbound_message_id": str(message_row["id"]),
                        "accepted_calls": json.dumps(
                            list(accepted_calls),
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                        "created_at": received_at,
                    },
                ).fetchone()
                if batch_row is None:
                    raise TransactionRepositoryError(
                        "PostgreSQL function batch insert did not return a row."
                    )
        except (TransactionRepositoryError, UpdateTargetNotFoundError):
            raise
        except Exception as error:
            raise TransactionRepositoryError(
                "Failed to begin function batch in PostgreSQL."
            ) from error
        return BatchStart(
            batch_id=str(batch_row["id"]),
            stored_reply=(
                None
                if batch_row.get("reply_text") is None
                else str(batch_row["reply_text"])
            ),
            accepted_calls=_decoded_calls(
                batch_row.get("accepted_calls"),
                fallback=accepted_calls,
            ),
        )

    def execute_writes(
        self,
        batch_id: str,
        commands: tuple[WriteCommand, ...],
    ) -> Mapping[int, TransactionRecord]:
        saved: dict[int, TransactionRecord] = {}
        try:
            with self._connection_factory() as connection:
                for command in commands:
                    if isinstance(command, UpdateLatestExpenseCommand):
                        row = connection.execute(
                            _update_latest_transaction_sql(command.fields),
                            {
                                "function_batch_id": batch_id,
                                "function_call_index": command.call_index,
                                "function_name": "update_expense",
                                "fields": _database_update_fields(command.fields),
                                **_database_update_fields(command.fields),
                            },
                        ).fetchone()
                        if row is None:
                            raise UpdateTargetNotFoundError
                        saved_record = _full_record_from_row(row)
                        saved[command.call_index] = saved_record
                        if bool(row.get("inserted", True)):
                            connection.execute(
                                INSERT_TRANSACTION_EVENT_SQL,
                                {
                                    "id": self._uuid_factory(),
                                    "transaction_id": str(row["database_id"]),
                                    "message_id": None,
                                    "event_type": "updated",
                                    "old_values": _jsonb_param(
                                        _database_row_event_values(row["old_values"])
                                    ),
                                    "new_values": _jsonb_param(
                                        _record_event_values(saved_record)
                                    ),
                                    "created_at": saved_record.updated_at,
                                },
                            )
                        continue
                    record = command.record
                    row = connection.execute(
                        INSERT_BATCH_TRANSACTION_SQL,
                        {
                            "id": self._uuid_factory(),
                            "external_id": record.id,
                            "function_batch_id": batch_id,
                            "function_call_index": command.call_index,
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
                    if row is None:
                        raise TransactionRepositoryError(
                            "PostgreSQL batch transaction insert did not return a row."
                        )
                    saved_record = _record_from_row(record, row)
                    saved[command.call_index] = saved_record
                    if bool(row.get("inserted", True)):
                        connection.execute(
                            INSERT_TRANSACTION_EVENT_SQL,
                            {
                                "id": self._uuid_factory(),
                                "transaction_id": str(row["database_id"]),
                                "message_id": None,
                                "event_type": "created",
                                "old_values": None,
                                "new_values": _jsonb_param(
                                    _record_event_values(saved_record)
                                ),
                                "created_at": saved_record.created_at,
                            },
                        )
                connection.execute(
                    MARK_WRITES_COMMITTED_SQL,
                    {"batch_id": batch_id},
                )
        except (TransactionRepositoryError, UpdateTargetNotFoundError):
            raise
        except Exception as error:
            raise TransactionRepositoryError(
                "Failed to execute function batch writes in PostgreSQL."
            ) from error
        return saved

    def complete_batch(
        self,
        batch_id: str,
        operation_results: Sequence[Mapping[str, object]],
        reply_text: str,
    ) -> None:
        try:
            with self._connection_factory() as connection:
                connection.execute(
                    COMPLETE_BATCH_SQL,
                    {
                        "batch_id": batch_id,
                        "operation_results": json.dumps(
                            list(operation_results),
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                        "reply_text": reply_text,
                    },
                )
        except Exception as error:
            raise TransactionRepositoryError(
                "Failed to complete function batch in PostgreSQL."
            ) from error


class PostgresPendingRequestRepository:
    def __init__(
        self,
        *,
        database_url: str | None = None,
        connection_factory: Callable[[], PostgresConnection] | None = None,
        uuid_factory: Callable[[], str] | None = None,
    ) -> None:
        self._connection_factory = connection_factory or _build_connection_factory(
            database_url
        )
        self._uuid_factory = uuid_factory or (lambda: str(uuid4()))

    def get(
        self,
        *,
        platform: str,
        user_id: str,
        chat_id: str,
    ) -> PendingRequest | None:
        try:
            with self._connection_factory() as connection:
                row = connection.execute(
                    SELECT_PENDING_REQUEST_SQL,
                    {
                        "platform": platform,
                        "platform_user_id": user_id,
                        "platform_chat_id": chat_id,
                    },
                ).fetchone()
        except Exception as error:
            raise TransactionRepositoryError(
                "Failed to read pending request from PostgreSQL."
            ) from error
        if row is None:
            return None
        known_arguments = row["known_arguments"]
        if isinstance(known_arguments, str):
            known_arguments = json.loads(known_arguments)
        return PendingRequest(
            platform=platform,
            user_id=user_id,
            chat_id=chat_id,
            proposed_function=str(row["proposed_function"]),
            known_arguments=dict(known_arguments),
            missing_fields=tuple(str(value) for value in row["missing_fields"]),
            expires_at=row["expires_at"],
        )

    def upsert(self, request: PendingRequest) -> None:
        try:
            with self._connection_factory() as connection:
                connection.execute(
                    UPSERT_PENDING_REQUEST_SQL,
                    {
                        "id": self._uuid_factory(),
                        "platform": request.platform,
                        "platform_user_id": request.user_id,
                        "platform_chat_id": request.chat_id,
                        "proposed_function": request.proposed_function,
                        "known_arguments": json.dumps(
                            dict(request.known_arguments),
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                        "missing_fields": list(request.missing_fields),
                        "expires_at": request.expires_at,
                    },
                )
        except Exception as error:
            raise TransactionRepositoryError(
                "Failed to save pending request to PostgreSQL."
            ) from error

    def delete(self, *, platform: str, user_id: str, chat_id: str) -> None:
        try:
            with self._connection_factory() as connection:
                connection.execute(
                    DELETE_PENDING_REQUEST_SQL,
                    {
                        "platform": platform,
                        "platform_user_id": user_id,
                        "platform_chat_id": chat_id,
                    },
                )
        except Exception as error:
            raise TransactionRepositoryError(
                "Failed to delete pending request from PostgreSQL."
            ) from error
BEGIN_BATCH_SQL = """
-- function_batch_repository.begin_batch
insert into function_call_batches (
    id,
    inbound_message_id,
    accepted_calls,
    status,
    created_at
)
values (
    %(id)s,
    %(inbound_message_id)s,
    %(accepted_calls)s::jsonb,
    'accepted',
    %(created_at)s
)
on conflict (inbound_message_id)
do update set inbound_message_id = function_call_batches.inbound_message_id
returning id, status, reply_text, accepted_calls
"""


SELECT_LEGACY_TRANSACTION_SQL = """
-- function_batch_repository.select_legacy_transaction
select
    t.transaction_date as date,
    t.amount,
    t.currency,
    t.category,
    t.merchant,
    t.note
from transactions t
where t.created_from_message_id = %(inbound_message_id)s
order by t.id asc
limit 1
"""


INSERT_BATCH_TRANSACTION_SQL = """
-- function_batch_repository.insert_transaction
with inserted as (
    insert into transactions (
        id,
        external_id,
        user_id,
        created_from_message_id,
        function_batch_id,
        function_call_index,
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
    select
        %(id)s,
        %(external_id)s,
        m.user_id,
        null,
        b.id,
        %(function_call_index)s,
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
    from function_call_batches b
    join inbound_messages m on m.id = b.inbound_message_id
    where b.id = %(function_batch_id)s
    on conflict (function_batch_id, function_call_index) do nothing
    returning *, true as inserted
), selected as (
    select * from inserted
    union all
    select t.*, false as inserted
    from transactions t
    where t.function_batch_id = %(function_batch_id)s
      and t.function_call_index = %(function_call_index)s
      and not exists (select 1 from inserted)
)
select
    id as database_id,
    external_id as id,
    transaction_date as date,
    amount,
    currency,
    transaction_type as type,
    category,
    merchant,
    payment_method,
    note,
    created_at,
    updated_at,
    inserted
from selected
limit 1
"""


MARK_WRITES_COMMITTED_SQL = """
-- function_batch_repository.mark_writes_committed
update function_call_batches
set status = 'writes_committed'
where id = %(batch_id)s
  and status = 'accepted'
"""


COMPLETE_BATCH_SQL = """
-- function_batch_repository.complete_batch
update function_call_batches
set operation_results = %(operation_results)s::jsonb,
    reply_text = %(reply_text)s,
    status = 'completed',
    completed_at = now()
where id = %(batch_id)s
  and status in ('accepted', 'writes_committed')
"""


SELECT_PENDING_REQUEST_SQL = """
-- function_batch_repository.select_pending_request
select
    p.proposed_function,
    p.known_arguments,
    p.missing_fields,
    p.expires_at
from pending_requests p
join user_identities ui on ui.id = p.identity_id
where ui.platform = %(platform)s
  and ui.platform_user_id = %(platform_user_id)s
  and p.platform_chat_id = %(platform_chat_id)s
limit 1
"""


UPSERT_PENDING_REQUEST_SQL = """
-- function_batch_repository.upsert_pending_request
insert into pending_requests (
    id,
    identity_id,
    platform_chat_id,
    proposed_function,
    known_arguments,
    missing_fields,
    expires_at
)
select
    %(id)s,
    ui.id,
    %(platform_chat_id)s,
    %(proposed_function)s,
    %(known_arguments)s::jsonb,
    %(missing_fields)s,
    %(expires_at)s
from user_identities ui
where ui.platform = %(platform)s
  and ui.platform_user_id = %(platform_user_id)s
on conflict (identity_id, platform_chat_id)
do update set proposed_function = excluded.proposed_function,
    known_arguments = excluded.known_arguments,
    missing_fields = excluded.missing_fields,
    expires_at = excluded.expires_at,
    updated_at = now()
"""


DELETE_PENDING_REQUEST_SQL = """
-- function_batch_repository.delete_pending_request
delete from pending_requests p
using user_identities ui
where ui.id = p.identity_id
  and ui.platform = %(platform)s
  and ui.platform_user_id = %(platform_user_id)s
  and p.platform_chat_id = %(platform_chat_id)s
"""


def _record_from_row(
    proposed: TransactionRecord,
    row: Mapping[str, Any],
) -> TransactionRecord:
    return replace(
        proposed,
        id=str(row["id"]),
        date=str(row["date"]),
        amount=Decimal(str(row["amount"])),
        currency=str(row["currency"]),
        type=str(row["type"]),
        category=str(row["category"]),
        merchant=None if row.get("merchant") is None else str(row["merchant"]),
        payment_method=(
            None
            if row.get("payment_method") is None
            else str(row["payment_method"])
        ),
        note=None if row.get("note") is None else str(row["note"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _decoded_calls(
    value: object,
    *,
    fallback: Sequence[Mapping[str, object]],
) -> tuple[Mapping[str, object], ...]:
    if value is None:
        return tuple(fallback)
    decoded = json.loads(value) if isinstance(value, str) else value
    if not isinstance(decoded, list) or not all(
        isinstance(call, Mapping) for call in decoded
    ):
        raise TransactionRepositoryError("Stored function batch calls are invalid.")
    return tuple(dict(call) for call in decoded)


def _legacy_transaction_reply(row: Mapping[str, object]) -> str:
    parts = [
        str(row["date"]),
        str(row["category"]),
        format(Decimal(str(row["amount"])), "f"),
        str(row["currency"]),
    ]
    description = row.get("merchant") or row.get("note")
    if description:
        parts.append(str(description))
    return "已记录：" + " ".join(parts)


UPDATE_FIELD_COLUMNS = {
    "date": "transaction_date",
    "amount": "amount",
    "currency": "currency",
    "category": "category",
    "merchant": "merchant",
    "payment_method": "payment_method",
    "note": "note",
}


def _database_update_fields(fields: Mapping[str, object]) -> dict[str, object]:
    return {UPDATE_FIELD_COLUMNS[name]: value for name, value in fields.items()}


def _update_latest_transaction_sql(fields: Mapping[str, object]) -> str:
    database_fields = _database_update_fields(fields)
    assignments = [
        f"{column} = %({column})s" for column in database_fields
    ]
    assignments.append("updated_at = now()")
    set_clause = ",\n        ".join(assignments)
    return f"""
-- function_batch_repository.update_latest_transaction
with claimed as (
    insert into function_call_executions (
        function_batch_id,
        function_call_index,
        function_name,
        status
    )
    values (
        %(function_batch_id)s,
        %(function_call_index)s,
        %(function_name)s,
        'started'
    )
    on conflict (function_batch_id, function_call_index) do nothing
    returning function_batch_id
), target as (
    select t.*
    from function_call_batches b
    join inbound_messages m on m.id = b.inbound_message_id
    join transactions t on t.user_id = m.user_id
    where b.id = %(function_batch_id)s
      and exists (select 1 from claimed)
    order by t.created_at desc, t.id desc
    limit 1
    for update of t
), updated as (
    update transactions t
    set {set_clause}
    from target
    where t.id = target.id
    returning t.*, to_jsonb(target.*) as old_values
), completed as (
    update function_call_executions execution
    set status = 'completed',
        transaction_id = updated.id,
        completed_at = now()
    from updated
    where execution.function_batch_id = %(function_batch_id)s
      and execution.function_call_index = %(function_call_index)s
    returning execution.transaction_id
), selected as (
    select updated.*, true as inserted
    from updated
    union all
    select t.*, false as inserted, null::jsonb as old_values
    from function_call_executions execution
    join transactions t on t.id = execution.transaction_id
    where execution.function_batch_id = %(function_batch_id)s
      and execution.function_call_index = %(function_call_index)s
      and not exists (select 1 from claimed)
)
select
    selected.id as database_id,
    selected.external_id as id,
    selected.transaction_date as date,
    selected.amount,
    selected.currency,
    selected.transaction_type as type,
    selected.category,
    selected.merchant,
    selected.payment_method,
    selected.note,
    m.platform as source_platform,
    ui.platform_user_id as source_user_id,
    ui.username as source_username,
    ui.display_name as source_user_display_name,
    m.platform_chat_id as source_chat_id,
    m.platform_message_id as source_message_id,
    selected.created_at,
    selected.updated_at,
    selected.inserted,
    selected.old_values
from selected
join function_call_batches b on b.id = %(function_batch_id)s
join inbound_messages m on m.id = b.inbound_message_id
join user_identities ui on ui.id = m.identity_id
limit 1
"""


def _full_record_from_row(row: Mapping[str, Any]) -> TransactionRecord:
    return TransactionRecord(
        id=str(row["id"]),
        date=str(row["date"]),
        amount=Decimal(str(row["amount"])),
        currency=str(row["currency"]),
        type=str(row["type"]),
        category=str(row["category"]),
        merchant=None if row.get("merchant") is None else str(row["merchant"]),
        payment_method=(
            None
            if row.get("payment_method") is None
            else str(row["payment_method"])
        ),
        note=None if row.get("note") is None else str(row["note"]),
        source_platform=str(row["source_platform"]),
        source_user_id=str(row["source_user_id"]),
        source_username=(
            None
            if row.get("source_username") is None
            else str(row["source_username"])
        ),
        source_user_display_name=(
            None
            if row.get("source_user_display_name") is None
            else str(row["source_user_display_name"])
        ),
        source_chat_id=str(row["source_chat_id"]),
        source_message_id=str(row["source_message_id"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _database_row_event_values(values: Mapping[str, object]) -> Mapping[str, object]:
    return {
        "id": str(values["external_id"]),
        "date": str(values["transaction_date"]),
        "amount": str(values["amount"]),
        "currency": str(values["currency"]),
        "type": str(values["transaction_type"]),
        "category": str(values["category"]),
        "merchant": values.get("merchant"),
        "payment_method": values.get("payment_method"),
        "note": values.get("note"),
    }
