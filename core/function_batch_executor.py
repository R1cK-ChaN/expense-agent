"""Validate and execute one model-proposed function batch deterministically."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Protocol
from zoneinfo import ZoneInfo

from core.categories import DEFAULT_EXPENSE_CATEGORY, SUPPORTED_CATEGORY_SET
from core.currencies import normalize_currency_code
from core.function_calls import (
    ApplicationFunction,
    FunctionCallBatch,
    FunctionCallProposal,
)
from core.messages import ConversationKind, InboundMessage
from core.pending_requests import PendingRequestService
from core.statistics import (
    StatisticsFilters,
    StatisticsQueryScope,
    StatisticsScopeMode,
    render_recent_expenses,
    render_spending_comparison,
    render_spending_summary,
    render_top_expenses,
    resolve_period,
)
from integrations.google_sheets.repository import TransactionRecord


class FunctionBatchValidationError(ValueError):
    """Raised before persistence when a model proposal violates the contract."""


class UpdateTargetNotFoundError(Exception):
    """Raised when an accepted latest-expense update has no target."""


@dataclass(frozen=True)
class BatchStart:
    batch_id: str
    stored_reply: str | None
    accepted_calls: tuple[Mapping[str, object], ...] = ()
    is_new: bool = False


@dataclass(frozen=True)
class CreateExpenseCommand:
    call_index: int
    record: TransactionRecord


@dataclass(frozen=True)
class UpdateLatestExpenseCommand:
    call_index: int
    fields: Mapping[str, object]


WriteCommand = CreateExpenseCommand | UpdateLatestExpenseCommand


class FunctionBatchRepository(Protocol):
    def find_batch(self, request: InboundMessage) -> BatchStart | None:
        raise NotImplementedError

    def begin_batch(
        self,
        request: InboundMessage,
        accepted_calls: Sequence[Mapping[str, object]],
    ) -> BatchStart:
        raise NotImplementedError

    def accept_calls(
        self,
        batch_id: str,
        accepted_calls: Sequence[Mapping[str, object]],
    ) -> None:
        raise NotImplementedError

    def execute_writes(
        self,
        batch_id: str,
        commands: tuple[WriteCommand, ...],
    ) -> Mapping[int, TransactionRecord]:
        raise NotImplementedError

    def complete_batch(
        self,
        batch_id: str,
        operation_results: Sequence[Mapping[str, object]],
        reply_text: str,
    ) -> None:
        raise NotImplementedError


class FunctionBatchExecutor:
    def __init__(
        self,
        *,
        repository: FunctionBatchRepository,
        statistics: object | None,
        pending_requests: PendingRequestService,
        timezone: str,
        default_currency: str,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str],
    ) -> None:
        self._repository = repository
        self._statistics = statistics
        self._pending_requests = pending_requests
        self._timezone = timezone
        self._default_currency = default_currency
        self._clock = clock or _utc_now
        self._id_factory = id_factory

    def execute(self, message: InboundMessage, batch: FunctionCallBatch) -> str:
        self._validate_batch(message, batch)
        accepted_calls = tuple(_serialized_call(call) for call in batch.calls)
        start = self._repository.begin_batch(message, accepted_calls)
        if start.stored_reply is not None:
            return start.stored_reply
        persisted_batch = function_batch_from_serialized(
            start.accepted_calls or accepted_calls
        )
        validated = self._validate_batch(message, persisted_batch)

        if len(validated) == 1 and isinstance(validated[0], _ControlCall):
            reply = self._execute_control(message, validated[0])
            if (
                validated[0].function
                is ApplicationFunction.REJECT_UNSUPPORTED_REQUEST
            ):
                self._pending_requests.remove(
                    platform=message.source_platform,
                    user_id=message.source_user_id,
                    chat_id=message.source_chat_id,
                )
            self._repository.complete_batch(
                start.batch_id,
                ({"call_index": 0, "status": "completed"},),
                reply,
            )
            return reply

        write_commands = tuple(
            item
            for item in validated
            if isinstance(item, (CreateExpenseCommand, UpdateLatestExpenseCommand))
        )
        try:
            records = self._repository.execute_writes(start.batch_id, write_commands)
        except UpdateTargetNotFoundError:
            reply = "没有找到可修改的支出，请先记一笔或说明要修改哪一笔。"
            self._pending_requests.save(
                platform=message.source_platform,
                user_id=message.source_user_id,
                chat_id=message.source_chat_id,
                proposed_function=ApplicationFunction.UPDATE_EXPENSE.value,
                known_arguments={},
                missing_fields=("target",),
            )
            self._repository.complete_batch(
                start.batch_id,
                ({"call_index": 0, "status": "failed"},),
                reply,
            )
            return reply
        replies: list[str] = []
        results: list[Mapping[str, object]] = []
        for call_index, item in enumerate(validated):
            if isinstance(item, CreateExpenseCommand):
                record = records[item.call_index]
                replies.append("已记录：" + _format_record(record))
                results.append(
                    {
                        "call_index": call_index,
                        "status": "completed",
                        "transaction_id": record.id,
                    }
                )
                continue
            if isinstance(item, UpdateLatestExpenseCommand):
                record = records[item.call_index]
                replies.append("已更新：" + _format_record(record))
                results.append(
                    {
                        "call_index": call_index,
                        "status": "completed",
                        "transaction_id": record.id,
                    }
                )
                continue
            if isinstance(item, _ReadCall):
                try:
                    replies.append(self._execute_read(message, item))
                    results.append(
                        {"call_index": call_index, "status": "completed"}
                    )
                except Exception:
                    replies.append("统计暂时失败，请稍后再试。")
                    results.append({"call_index": call_index, "status": "failed"})
                continue
            raise AssertionError("validated function has no executor")

        reply = "\n".join(replies)
        self._pending_requests.remove(
            platform=message.source_platform,
            user_id=message.source_user_id,
            chat_id=message.source_chat_id,
        )
        self._repository.complete_batch(start.batch_id, tuple(results), reply)
        return reply

    def _validate_batch(
        self,
        message: InboundMessage,
        batch: FunctionCallBatch,
    ) -> tuple[object, ...]:
        control_calls = [
            call
            for call in batch.calls
            if call.function
            in {
                ApplicationFunction.REQUEST_CLARIFICATION,
                ApplicationFunction.REJECT_UNSUPPORTED_REQUEST,
            }
        ]
        if control_calls and len(batch.calls) != 1:
            raise FunctionBatchValidationError(
                "control functions must be the only call in a batch"
            )

        validated: list[object] = []
        for call_index, call in enumerate(batch.calls):
            if call.function is ApplicationFunction.RECORD_EXPENSE:
                validated.append(
                    self._validate_create(call_index, call.arguments, message)
                )
            elif call.function is ApplicationFunction.UPDATE_EXPENSE:
                validated.append(_validate_update(call_index, call.arguments))
            elif call.function in {
                ApplicationFunction.GET_SPENDING_SUMMARY,
                ApplicationFunction.COMPARE_SPENDING_PERIODS,
                ApplicationFunction.GET_TOP_EXPENSES,
                ApplicationFunction.LIST_RECENT_EXPENSES,
            }:
                validated.append(self._validate_read(call_index, call, message))
            elif call.function in {
                ApplicationFunction.REQUEST_CLARIFICATION,
                ApplicationFunction.REJECT_UNSUPPORTED_REQUEST,
            }:
                validated.append(_validate_control(call))
            else:
                raise FunctionBatchValidationError(
                    f"unsupported executor function: {call.function.value}"
                )
        return tuple(validated)

    def _validate_read(
        self,
        call_index: int,
        call: FunctionCallProposal,
        message: InboundMessage,
    ) -> "_ReadCall":
        arguments = call.arguments
        filters = _validated_filters(arguments)
        scope = _resolved_statistics_scope(message, arguments.get("scope"))
        today = message.received_at.astimezone(ZoneInfo(self._timezone)).date()
        if call.function is ApplicationFunction.LIST_RECENT_EXPENSES:
            limit = _validated_limit(arguments.get("limit"))
            return _ReadCall(
                call_index,
                call.function,
                {"scope": scope, "filters": filters, "limit": limit},
            )
        if call.function is ApplicationFunction.COMPARE_SPENDING_PERIODS:
            current = _resolved_period(arguments.get("current_period"), today=today)
            comparison = _resolved_period(
                arguments.get("comparison_period"), today=today
            )
            return _ReadCall(
                call_index,
                call.function,
                {
                    "current_range": current,
                    "comparison_range": comparison,
                    "scope": scope,
                    "filters": filters,
                },
            )
        date_range = _resolved_period(arguments.get("period"), today=today)
        read_arguments: dict[str, object] = {
            "date_range": date_range,
            "scope": scope,
            "filters": filters,
        }
        if call.function is ApplicationFunction.GET_TOP_EXPENSES:
            read_arguments["limit"] = _validated_limit(arguments.get("limit"))
        return _ReadCall(call_index, call.function, read_arguments)

    def _execute_read(self, message: InboundMessage, call: "_ReadCall") -> str:
        if self._statistics is None:
            raise RuntimeError("statistics service is unavailable")
        common = dict(call.arguments)
        if call.function is ApplicationFunction.GET_SPENDING_SUMMARY:
            value = self._statistics.get_spending_summary(**common)
            return value if isinstance(value, str) else render_spending_summary(value)
        if call.function is ApplicationFunction.COMPARE_SPENDING_PERIODS:
            value = self._statistics.compare_spending_periods(**common)
            return value if isinstance(value, str) else render_spending_comparison(value)
        if call.function is ApplicationFunction.GET_TOP_EXPENSES:
            value = self._statistics.get_top_expenses(**common)
            return value if isinstance(value, str) else render_top_expenses(value)
        value = self._statistics.list_recent_expenses(**common)
        return value if isinstance(value, str) else render_recent_expenses(value)

    def _validate_create(
        self,
        call_index: int,
        arguments: Mapping[str, object],
        message: InboundMessage,
    ) -> CreateExpenseCommand:
        try:
            amount = Decimal(str(arguments.get("amount")))
        except (InvalidOperation, ValueError):
            raise FunctionBatchValidationError("expense amount is invalid") from None
        if not amount.is_finite() or amount <= 0:
            raise FunctionBatchValidationError("expense amount must be positive")

        currency_value = arguments.get("currency")
        currency = normalize_currency_code(
            currency_value if isinstance(currency_value, str) else None,
            default_currency=self._default_currency,
        )
        if currency is None:
            raise FunctionBatchValidationError("expense currency is invalid")

        category_value = arguments.get("category")
        category = (
            category_value
            if isinstance(category_value, str) and category_value
            else DEFAULT_EXPENSE_CATEGORY
        )
        if category not in SUPPORTED_CATEGORY_SET:
            raise FunctionBatchValidationError("expense category is invalid")

        transaction_date = _validated_date(
            arguments.get("date"),
            default=message.received_at.astimezone(ZoneInfo(self._timezone)).date(),
        )
        timestamp = self._clock().astimezone(ZoneInfo(self._timezone)).isoformat()
        return CreateExpenseCommand(
            call_index=call_index,
            record=TransactionRecord(
                id=self._id_factory(),
                date=transaction_date.isoformat(),
                amount=amount,
                currency=currency,
                type="expense",
                category=category,
                merchant=_optional_string(arguments.get("merchant")),
                payment_method=_optional_string(arguments.get("payment_method")),
                note=_optional_string(arguments.get("note")),
                source_platform=message.source_platform,
                source_user_id=message.source_user_id,
                source_username=message.source_username,
                source_user_display_name=message.source_user_display_name,
                source_chat_id=message.source_chat_id,
                source_message_id=message.source_message_id,
                created_at=timestamp,
                updated_at=timestamp,
            ),
        )

    def _execute_control(self, message: InboundMessage, call: "_ControlCall") -> str:
        if call.function is ApplicationFunction.REJECT_UNSUPPORTED_REQUEST:
            return _unsupported_reply(call.code)
        self._pending_requests.save(
            platform=message.source_platform,
            user_id=message.source_user_id,
            chat_id=message.source_chat_id,
            proposed_function=call.proposed_function,
            known_arguments=call.known_arguments or {},
            missing_fields=call.missing_fields,
        )
        return _clarification_reply(call.missing_fields)


@dataclass(frozen=True)
class _ControlCall:
    function: ApplicationFunction
    code: str
    missing_fields: tuple[str, ...] = ()
    proposed_function: str = ApplicationFunction.RECORD_EXPENSE.value
    known_arguments: Mapping[str, object] | None = None


@dataclass(frozen=True)
class _ReadCall:
    call_index: int
    function: ApplicationFunction
    arguments: Mapping[str, object]


def _validate_control(call: FunctionCallProposal) -> _ControlCall:
    if call.function is ApplicationFunction.REJECT_UNSUPPORTED_REQUEST:
        code = call.arguments.get("capability_code")
        if not isinstance(code, str):
            raise FunctionBatchValidationError("unsupported capability code is invalid")
        return _ControlCall(function=call.function, code=code)
    reason = call.arguments.get("reason_code")
    missing = call.arguments.get("missing_fields")
    proposed = call.arguments.get("proposed_function", "record_expense")
    known = call.arguments.get("known_arguments", {})
    if (
        not isinstance(reason, str)
        or not isinstance(missing, list)
        or proposed not in {"record_expense", "update_expense"}
        or not isinstance(known, Mapping)
    ):
        raise FunctionBatchValidationError("clarification arguments are invalid")
    missing_fields = tuple(value for value in missing if isinstance(value, str))
    if reason == "missing_fields" and not missing_fields:
        raise FunctionBatchValidationError("clarification requires missing fields")
    return _ControlCall(
        function=call.function,
        code=reason,
        missing_fields=missing_fields,
        proposed_function=str(proposed),
        known_arguments=dict(known),
    )


def _validate_update(
    call_index: int,
    arguments: Mapping[str, object],
) -> UpdateLatestExpenseCommand:
    if arguments.get("target") != "latest":
        raise FunctionBatchValidationError("update target must be latest")
    changes = arguments.get("changes")
    if not isinstance(changes, Mapping):
        raise FunctionBatchValidationError("update changes are invalid")
    fields: dict[str, object] = {}
    for name, value in changes.items():
        if value is None:
            continue
        if name == "amount":
            try:
                amount = Decimal(str(value))
            except (InvalidOperation, ValueError):
                raise FunctionBatchValidationError("update amount is invalid") from None
            if not amount.is_finite() or amount <= 0:
                raise FunctionBatchValidationError("update amount must be positive")
            fields[name] = amount
        elif name == "date":
            fields[name] = _validated_date(value, default=date.today()).isoformat()
        elif name == "currency":
            currency = normalize_currency_code(value if isinstance(value, str) else None)
            if currency is None:
                raise FunctionBatchValidationError("update currency is invalid")
            fields[name] = currency
        elif name == "category":
            if not isinstance(value, str) or value not in SUPPORTED_CATEGORY_SET:
                raise FunctionBatchValidationError("update category is invalid")
            fields[name] = value
        elif name in {"merchant", "payment_method", "note"}:
            fields[name] = _optional_string(value)
        else:
            raise FunctionBatchValidationError(f"update field is invalid: {name}")
    if not fields:
        raise FunctionBatchValidationError("update requires at least one change")
    return UpdateLatestExpenseCommand(call_index=call_index, fields=fields)


def _validated_filters(arguments: Mapping[str, object]) -> StatisticsFilters:
    category = _optional_string(arguments.get("category"))
    if category is not None and category not in SUPPORTED_CATEGORY_SET:
        raise FunctionBatchValidationError("statistics category is invalid")
    return StatisticsFilters(
        category=category,
        merchant=_optional_string(arguments.get("merchant")),
    )


def _resolved_statistics_scope(
    message: InboundMessage,
    proposed_scope: object,
) -> StatisticsQueryScope:
    if proposed_scope not in {None, "personal"}:
        raise FunctionBatchValidationError("statistics scope is invalid")
    mode = (
        StatisticsScopeMode.PERSONAL
        if proposed_scope == "personal"
        or message.conversation_kind is ConversationKind.PERSONAL
        else StatisticsScopeMode.CONVERSATION
    )
    return StatisticsQueryScope(
        mode=mode,
        source_platform=message.source_platform,
        source_user_id=message.source_user_id,
        source_chat_id=message.source_chat_id,
    )


def _resolved_period(value: object, *, today: date):
    if not isinstance(value, Mapping):
        raise FunctionBatchValidationError("statistics period is invalid")
    kind = value.get("kind")
    if not isinstance(kind, str):
        raise FunctionBatchValidationError("statistics period is invalid")
    try:
        return resolve_period(
            kind,
            today=today,
            start_date=_optional_string(value.get("start_date")),
            end_date=_optional_string(value.get("end_date")),
        )
    except ValueError:
        raise FunctionBatchValidationError("statistics period is invalid") from None


def _validated_limit(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 20:
        raise FunctionBatchValidationError("statistics limit is invalid")
    return value


def _serialized_call(call: FunctionCallProposal) -> Mapping[str, object]:
    return {"function": call.function.value, "arguments": dict(call.arguments)}


def function_batch_from_serialized(
    calls: Sequence[Mapping[str, object]],
) -> FunctionCallBatch:
    proposals: list[FunctionCallProposal] = []
    for call in calls:
        function = call.get("function")
        arguments = call.get("arguments")
        if not isinstance(function, str) or not isinstance(arguments, Mapping):
            raise FunctionBatchValidationError("persisted function batch is invalid")
        try:
            application_function = ApplicationFunction(function)
        except ValueError:
            raise FunctionBatchValidationError(
                "persisted function batch contains an unknown function"
            ) from None
        proposals.append(
            FunctionCallProposal(
                function=application_function,
                arguments=dict(arguments),
            )
        )
    return FunctionCallBatch(calls=tuple(proposals))


def _validated_date(value: object, *, default: date) -> date:
    if value is None:
        return default
    if not isinstance(value, str):
        raise FunctionBatchValidationError("expense date is invalid")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise FunctionBatchValidationError("expense date is invalid") from None


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise FunctionBatchValidationError("optional expense field is invalid")
    stripped = value.strip()
    return stripped or None


def _format_record(record: TransactionRecord) -> str:
    parts = [
        record.date,
        record.category,
        format(record.amount, "f"),
        record.currency,
    ]
    description = record.merchant or record.note
    if description:
        parts.append(description)
    return " ".join(parts)


def _clarification_reply(missing_fields: tuple[str, ...]) -> str:
    labels = {
        "amount": "金额",
        "date": "日期",
        "currency": "币种",
        "category": "分类",
        "merchant": "商家",
        "payment_method": "支付方式",
        "note": "备注",
    }
    named = "、".join(labels.get(field, field) for field in missing_fields)
    return f"还缺{named}，请补充一下。" if named else "请再具体说明一下。"


def _unsupported_reply(code: str) -> str:
    if code in {"delete", "bulk_destructive_mutation"}:
        return "目前不支持删除或批量破坏性修改账目。"
    if code == "financial_advice":
        return "目前只支持记账和支出统计，不提供财务建议。"
    return "这个请求目前还不支持。"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
