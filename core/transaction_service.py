import logging
from calendar import monthrange
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

from core.currencies import normalize_currency_code
from core.exchange_rates import (
    ExchangeRateProvider,
    ExchangeRateProviderError,
)
from core.intent_parser import IntentParserResult, ParserContext, ParserIntent
from core.messages import InboundMessage
from core.validator import (
    ValidationContext,
    ValidationResult,
    ValidatedExpense,
    UpdateValidationResult,
    validate_create_expense,
    validate_update_recent_expense,
)
from integrations.google_sheets.repository import (
    TransactionRecord,
    TransactionRepositoryError,
)


UNKNOWN_INTENT_MESSAGE = (
    "我目前可以帮你记录支出，例如：午饭 12.5 麦当劳。"
)
LOW_CONFIDENCE_MESSAGE = "这条消息我不太确定，请换个说法或补充金额、商家。"
PROCESSING_FAILURE_MESSAGE = "抱歉，暂时没能记账，请稍后再试。"
EXCHANGE_RATE_FAILURE_MESSAGE = "抱歉，暂时没能取得汇率，请稍后再试。"
NO_RECENT_EXPENSE_MESSAGE = "我还没有找到你最近的支出记录。"
SIMILAR_RECENT_EXPENSE_MESSAGE = (
    "检测到你刚刚记录过类似支出，请确认是要修改上一笔，还是新增一笔"
)
MIN_CREATE_EXPENSE_CONFIDENCE = 0.7
MIN_UPDATE_EXPENSE_CONFIDENCE = 0.7
MIN_QUERY_MONTHLY_TOTAL_CONFIDENCE = 0.7
MONTHLY_TOTAL_AMOUNT_QUANTUM = Decimal("0.01")
RECENT_EXPENSE_RETRY_WINDOW_SECONDS = 10 * 60
_EXACT_RECENT_EXPENSE_RETRY = "exact"
_AMBIGUOUS_RECENT_EXPENSE_RETRY = "ambiguous"

logger = logging.getLogger(__name__)


class ExpenseIntentParser(Protocol):
    def parse(
        self,
        text: str,
        *,
        context: ParserContext,
    ) -> IntentParserResult:
        raise NotImplementedError


class TransactionRepository(Protocol):
    def find_by_source_message(
        self,
        *,
        source_platform: str,
        user_id: str,
        chat_id: str,
        message_id: str,
    ) -> TransactionRecord | None:
        raise NotImplementedError

    def append_transaction(self, record: TransactionRecord) -> TransactionRecord:
        raise NotImplementedError

    def get_latest_transaction(
        self,
        *,
        source_platform: str,
        user_id: str,
    ) -> TransactionRecord | None:
        raise NotImplementedError

    def update_transaction(
        self,
        transaction_id: str,
        fields: Mapping[str, object],
    ) -> TransactionRecord:
        raise NotImplementedError

    def list_monthly_expenses(
        self,
        *,
        source_platform: str,
        user_id: str,
        month: str,
    ) -> list[TransactionRecord]:
        raise NotImplementedError

    def list_expenses(
        self,
        *,
        source_platform: str,
        user_id: str,
        start_date: str,
        end_date: str,
    ) -> list[TransactionRecord]:
        raise NotImplementedError


@dataclass(frozen=True)
class _ExpenseTotalSummary:
    total: Decimal
    currency: str
    foreign_totals: tuple[tuple[str, Decimal], ...]
    exchange_rate_dates: tuple[tuple[str, str], ...]
    category_totals: tuple[tuple[str, Decimal], ...]


@dataclass(frozen=True)
class _ExpenseQueryBounds:
    start_date: str
    end_date: str


class CreateExpenseValidator(Protocol):
    def __call__(
        self,
        parser_result: IntentParserResult,
        *,
        context: ValidationContext,
        source_text: str | None = None,
    ) -> ValidationResult:
        raise NotImplementedError


class UpdateExpenseValidator(Protocol):
    def __call__(
        self,
        parser_result: IntentParserResult,
        *,
        context: ValidationContext,
    ) -> UpdateValidationResult:
        raise NotImplementedError


class TransactionService:
    def __init__(
        self,
        *,
        parser: ExpenseIntentParser,
        repository: TransactionRepository,
        exchange_rate_provider: ExchangeRateProvider | None = None,
        timezone: str,
        default_currency: str,
        validator: CreateExpenseValidator = validate_create_expense,
        update_validator: UpdateExpenseValidator = validate_update_recent_expense,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._parser = parser
        self._repository = repository
        self._exchange_rate_provider = exchange_rate_provider
        self._timezone = timezone
        self._default_currency = default_currency
        self._validator = validator
        self._update_validator = update_validator
        self._clock = clock or _utc_now
        self._id_factory = id_factory or _default_transaction_id
        self._update_targets_by_message: dict[tuple[str, str, str, str], str] = {}

    def __call__(self, message: InboundMessage) -> str:
        return self.handle_message(message)

    def handle_message(self, message: InboundMessage) -> str:
        try:
            existing_record = self._repository.find_by_source_message(
                source_platform=message.source_platform,
                user_id=message.source_user_id,
                chat_id=message.source_chat_id,
                message_id=message.source_message_id,
            )
        except TransactionRepositoryError:
            logger.exception("Failed to check transaction duplicate status.")
            return PROCESSING_FAILURE_MESSAGE

        if existing_record is not None:
            return self._format_record_reply("已记录", existing_record)

        try:
            parser_result = self._parser.parse(
                message.message_text,
                context=ParserContext(
                    today=_message_date(message.received_at, self._timezone),
                    timezone=self._timezone,
                    default_currency=self._default_currency,
                ),
            )
        except Exception:
            logger.exception("Failed to parse inbound message.")
            return PROCESSING_FAILURE_MESSAGE

        if not parser_result.is_success:
            return PROCESSING_FAILURE_MESSAGE

        if parser_result.intent is ParserIntent.UPDATE_RECENT_EXPENSE:
            return self._handle_update_recent_expense(
                parser_result,
                message=message,
            )

        if parser_result.intent is ParserIntent.QUERY_MONTHLY_TOTAL:
            return self._handle_query_monthly_total(
                parser_result,
                message=message,
            )

        if parser_result.intent is not ParserIntent.CREATE_EXPENSE:
            return UNKNOWN_INTENT_MESSAGE

        if parser_result.confidence < MIN_CREATE_EXPENSE_CONFIDENCE:
            return LOW_CONFIDENCE_MESSAGE

        validation = self._validator(
            parser_result,
            context=ValidationContext(
                timezone=self._timezone,
                default_currency=self._default_currency,
                now=message.received_at,
            ),
            source_text=message.message_text,
        )
        if not validation.is_valid or validation.expense is None:
            return validation.user_message or UNKNOWN_INTENT_MESSAGE

        retry_reply = self._handle_recent_create_retry(
            validation.expense,
            message=message,
        )
        if retry_reply is not None:
            return retry_reply

        record = self._new_transaction_record(
            validation.expense,
            message=message,
        )
        try:
            saved_record = self._repository.append_transaction(record)
        except TransactionRepositoryError:
            logger.exception("Failed to append transaction.")
            return PROCESSING_FAILURE_MESSAGE

        return self._format_record_reply("已记录", saved_record)

    def handle_telegram_message(self, message: InboundMessage) -> str:
        return self.handle_message(message)

    def _handle_update_recent_expense(
        self,
        parser_result: IntentParserResult,
        *,
        message: InboundMessage,
    ) -> str:
        if parser_result.confidence < MIN_UPDATE_EXPENSE_CONFIDENCE:
            return LOW_CONFIDENCE_MESSAGE

        validation = self._update_validator(
            parser_result,
            context=ValidationContext(
                timezone=self._timezone,
                default_currency=self._default_currency,
                now=message.received_at,
            ),
        )
        if not validation.is_valid:
            return validation.user_message or UNKNOWN_INTENT_MESSAGE

        update_message_key = (
            message.source_platform,
            message.source_user_id,
            message.source_chat_id,
            message.source_message_id,
        )
        transaction_id = self._update_targets_by_message.get(update_message_key)
        if transaction_id is None:
            try:
                latest_record = self._repository.get_latest_transaction(
                    source_platform=message.source_platform,
                    user_id=message.source_user_id,
                )
            except TransactionRepositoryError:
                logger.exception("Failed to load latest transaction.")
                return PROCESSING_FAILURE_MESSAGE

            if latest_record is None:
                return NO_RECENT_EXPENSE_MESSAGE
            transaction_id = latest_record.id

        try:
            updated_record = self._repository.update_transaction(
                transaction_id,
                validation.update_fields,
            )
        except TransactionRepositoryError:
            logger.exception("Failed to update transaction.")
            return PROCESSING_FAILURE_MESSAGE

        self._update_targets_by_message[update_message_key] = updated_record.id
        return self._format_record_reply("已更新", updated_record)

    def _handle_recent_create_retry(
        self,
        expense: ValidatedExpense,
        *,
        message: InboundMessage,
    ) -> str | None:
        retry_message_key = (
            message.source_platform,
            message.source_user_id,
            message.source_chat_id,
            message.source_message_id,
        )
        transaction_id = self._update_targets_by_message.get(retry_message_key)
        if transaction_id is not None:
            try:
                updated_record = self._repository.update_transaction(
                    transaction_id,
                    {"currency": expense.currency},
                )
            except TransactionRepositoryError:
                logger.exception("Failed to update duplicate transaction retry.")
                return PROCESSING_FAILURE_MESSAGE
            return self._format_record_reply("已更新", updated_record)

        try:
            latest_record = self._repository.get_latest_transaction(
                source_platform=message.source_platform,
                user_id=message.source_user_id,
            )
        except TransactionRepositoryError:
            logger.exception("Failed to load latest transaction for retry guard.")
            return PROCESSING_FAILURE_MESSAGE

        if latest_record is None:
            return None

        decision = _recent_expense_retry_decision(
            latest_record,
            expense,
            received_at=message.received_at,
            timezone_name=self._timezone,
        )
        if decision is None:
            return None

        if decision == _AMBIGUOUS_RECENT_EXPENSE_RETRY:
            return _format_similar_recent_expense_reply(latest_record)

        try:
            updated_record = self._repository.update_transaction(
                latest_record.id,
                {"currency": expense.currency},
            )
        except TransactionRepositoryError:
            logger.exception("Failed to update recent transaction retry.")
            return PROCESSING_FAILURE_MESSAGE

        self._update_targets_by_message[retry_message_key] = updated_record.id
        return self._format_record_reply("已更新", updated_record)

    def _handle_query_monthly_total(
        self,
        parser_result: IntentParserResult,
        *,
        message: InboundMessage,
    ) -> str:
        if parser_result.confidence < MIN_QUERY_MONTHLY_TOTAL_CONFIDENCE:
            return LOW_CONFIDENCE_MESSAGE

        query = parser_result.query
        if query is None:
            logger.error("Monthly total query intent missing query fields.")
            return PROCESSING_FAILURE_MESSAGE

        currency = normalize_currency_code(self._default_currency)
        query_currency = normalize_currency_code(
            query.currency,
            default_currency=self._default_currency,
        )
        if currency is None:
            return PROCESSING_FAILURE_MESSAGE
        if query_currency is None:
            return _format_unsupported_monthly_total_reply(currency)
        if query_currency != currency:
            return _format_unsupported_monthly_total_reply(currency)

        try:
            bounds = _query_bounds(
                query,
                current_date=_message_date(message.received_at, self._timezone),
            )
        except ValueError:
            logger.exception("Invalid expense query date range.")
            return PROCESSING_FAILURE_MESSAGE

        try:
            records = self._repository.list_expenses(
                source_platform=message.source_platform,
                user_id=message.source_user_id,
                start_date=bounds.start_date,
                end_date=bounds.end_date,
            )
        except TransactionRepositoryError:
            logger.exception("Failed to list expenses by date range.")
            return PROCESSING_FAILURE_MESSAGE

        try:
            summary = _summarize_expense_records(
                records,
                currency=currency,
                exchange_rate_provider=self._exchange_rate_provider,
            )
        except ExchangeRateProviderError:
            logger.exception("Failed to convert monthly expenses.")
            return EXCHANGE_RATE_FAILURE_MESSAGE

        return _format_expense_total_reply(summary, bounds=bounds)

    def _format_record_reply(
        self,
        prefix: str,
        record: TransactionRecord,
    ) -> str:
        summary = _format_record_summary(record)
        local_currency = normalize_currency_code(self._default_currency)
        record_currency = normalize_currency_code(record.currency)
        if (
            local_currency is None
            or record_currency is None
            or record_currency == local_currency
        ):
            return f"{prefix}：{summary}"

        if self._exchange_rate_provider is None:
            return f"{prefix}：{summary}"
        try:
            conversion = self._exchange_rate_provider.convert(
                record.amount,
                from_currency=record_currency,
                to_currency=local_currency,
                date=record.date,
            )
        except Exception:
            logger.exception("Failed to convert recorded foreign-currency expense.")
            return f"{prefix}：{summary}"

        return (
            f"{prefix}：{summary}（折合 {_format_money(conversion.converted_amount)} "
            f"{local_currency}，汇率日 {conversion.rate_date}）"
        )

    def _new_transaction_record(
        self,
        expense: ValidatedExpense,
        *,
        message: InboundMessage,
    ) -> TransactionRecord:
        timestamp = _format_timestamp(self._clock(), self._timezone)
        return TransactionRecord(
            id=self._id_factory(),
            date=expense.date,
            amount=expense.amount,
            currency=expense.currency,
            type=expense.type,
            category=expense.category,
            merchant=expense.merchant,
            payment_method=expense.payment_method,
            note=expense.note,
            source_platform=message.source_platform,
            source_user_id=message.source_user_id,
            source_username=message.source_username,
            source_user_display_name=message.source_user_display_name,
            source_chat_id=message.source_chat_id,
            source_message_id=message.source_message_id,
            created_at=timestamp,
            updated_at=timestamp,
        )


def _format_similar_recent_expense_reply(record: TransactionRecord) -> str:
    return SIMILAR_RECENT_EXPENSE_MESSAGE + "：" + _format_record_summary(record)


def _format_expense_total_reply(
    summary: _ExpenseTotalSummary,
    *,
    bounds: _ExpenseQueryBounds,
) -> str:
    amount = _format_money(summary.total)
    if bounds.start_date == bounds.end_date:
        period = bounds.start_date
    else:
        period = f"{bounds.start_date} 至 {bounds.end_date}"
    lines = [f"{period} 支出合计：{amount} {summary.currency}"]

    if summary.foreign_totals:
        foreign_parts = [
            f"{_format_original_money(value)} {code}"
            for code, value in summary.foreign_totals
        ]
        lines.append(
            f"外币支出（{len(summary.foreign_totals)} 种）：" + "；".join(foreign_parts)
        )
        rate_date_parts = [
            f"{code} {rate_date}"
            for code, rate_date in summary.exchange_rate_dates
        ]
        lines.append("汇率日：" + "；".join(rate_date_parts))

    if summary.category_totals:
        lines.append("分类占比：")
        for category, category_total in summary.category_totals:
            percentage = (
                Decimal("0")
                if summary.total == 0
                else category_total / summary.total * Decimal("100")
            )
            lines.append(
                f"- {category}：{_format_money(category_total)} {summary.currency}"
                f"（{percentage.quantize(Decimal('0.01'))}%）"
            )
    return "\n".join(lines)


def _format_unsupported_monthly_total_reply(currency: str) -> str:
    return f"我目前只支持以本币 {currency} 汇总支出。"


def _format_record_summary(record: TransactionRecord) -> str:
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


def _message_date(timestamp: datetime, timezone_name: str) -> date:
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=ZoneInfo(timezone_name))
    return timestamp.astimezone(ZoneInfo(timezone_name)).date()


def _query_bounds(query: object, *, current_date: date) -> _ExpenseQueryBounds:
    start_date = getattr(query, "start_date", None)
    end_date = getattr(query, "end_date", None)
    if start_date is not None and end_date is not None:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
        if start > end:
            raise ValueError("query start date is after end date")
        return _ExpenseQueryBounds(start.isoformat(), end.isoformat())

    month = getattr(query, "month", None)
    if not isinstance(month, str):
        raise ValueError("query has no date bounds")
    month_start = date.fromisoformat(f"{month}-01")
    month_end = month_start.replace(day=monthrange(month_start.year, month_start.month)[1])
    if month_start.strftime("%Y-%m") == current_date.strftime("%Y-%m"):
        month_end = current_date
    return _ExpenseQueryBounds(month_start.isoformat(), month_end.isoformat())


def _summarize_expense_records(
    records: list[TransactionRecord],
    *,
    currency: str,
    exchange_rate_provider: ExchangeRateProvider | None,
) -> _ExpenseTotalSummary:
    total = Decimal("0")
    foreign_totals: dict[str, Decimal] = {}
    exchange_rate_dates: set[tuple[str, str]] = set()
    category_totals: dict[str, Decimal] = {}
    for record in records:
        record_currency = normalize_currency_code(record.currency)
        if record_currency is None:
            raise ExchangeRateProviderError(
                f"Unsupported stored currency: {record.currency}"
            )
        if record_currency == currency:
            converted_amount = record.amount
            total += converted_amount
            category_totals[record.category] = (
                category_totals.get(record.category, Decimal("0")) + converted_amount
            )
            continue
        if exchange_rate_provider is None:
            raise ExchangeRateProviderError("Exchange-rate provider is not configured.")

        conversion = exchange_rate_provider.convert(
            record.amount,
            from_currency=record_currency,
            to_currency=currency,
            date=record.date,
        )
        total += conversion.converted_amount
        foreign_totals[record_currency] = (
            foreign_totals.get(record_currency, Decimal("0")) + record.amount
        )
        exchange_rate_dates.add((record_currency, conversion.rate_date))
        category_totals[record.category] = (
            category_totals.get(record.category, Decimal("0"))
            + conversion.converted_amount
        )

    return _ExpenseTotalSummary(
        total=total,
        currency=currency,
        foreign_totals=tuple(sorted(foreign_totals.items())),
        exchange_rate_dates=tuple(sorted(exchange_rate_dates)),
        category_totals=tuple(
            sorted(category_totals.items(), key=lambda item: (-item[1], item[0]))
        ),
    )


def _format_money(amount: Decimal) -> str:
    return format(amount.quantize(MONTHLY_TOTAL_AMOUNT_QUANTUM), "f")


def _format_original_money(amount: Decimal) -> str:
    return format(amount.normalize(), "f")


def _format_timestamp(timestamp: datetime, timezone_name: str) -> str:
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=ZoneInfo(timezone_name))
    return timestamp.astimezone(ZoneInfo(timezone_name)).isoformat()


def _recent_expense_retry_decision(
    record: TransactionRecord,
    expense: ValidatedExpense,
    *,
    received_at: datetime,
    timezone_name: str,
) -> str | None:
    if record.type != "expense":
        return None
    if record.date != expense.date:
        return None
    if record.amount != expense.amount:
        return None
    if record.category != expense.category:
        return None

    record_currency = normalize_currency_code(record.currency)
    expense_currency = normalize_currency_code(expense.currency)
    if (
        record_currency is None
        or expense_currency is None
        or record_currency == expense_currency
    ):
        return None

    if not _was_created_recently(
        record.created_at,
        received_at=received_at,
        timezone_name=timezone_name,
    ):
        return None

    record_descriptions = _normalized_retry_descriptions(record.merchant, record.note)
    expense_descriptions = _normalized_retry_descriptions(
        expense.merchant,
        expense.note,
    )
    if _retry_descriptions_have_exact_match(
        record_descriptions,
        expense_descriptions,
    ):
        return _EXACT_RECENT_EXPENSE_RETRY
    if _retry_descriptions_are_similar(record_descriptions, expense_descriptions):
        return _AMBIGUOUS_RECENT_EXPENSE_RETRY

    return None


def _normalized_retry_descriptions(
    merchant: str | None,
    note: str | None,
) -> tuple[str, ...]:
    descriptions: list[str] = []
    for text in (merchant, note):
        if text is None:
            continue
        normalized = "".join(text.lower().split())
        if normalized and normalized not in descriptions:
            descriptions.append(normalized)
    return tuple(descriptions)


def _retry_descriptions_have_exact_match(
    left: tuple[str, ...],
    right: tuple[str, ...],
) -> bool:
    return any(description in right for description in left)


def _retry_descriptions_are_similar(
    left: tuple[str, ...],
    right: tuple[str, ...],
) -> bool:
    return any(
        min(len(left_description), len(right_description)) >= 2
        and (
            left_description in right_description
            or right_description in left_description
        )
        for left_description in left
        for right_description in right
    )


def _was_created_recently(
    created_at: str,
    *,
    received_at: datetime,
    timezone_name: str,
) -> bool:
    created = _parse_timestamp(created_at, timezone_name)
    if created is None:
        return False

    received = received_at
    if received.tzinfo is None:
        received = received.replace(tzinfo=ZoneInfo(timezone_name))
    age_seconds = (received - created).total_seconds()
    return 0 <= age_seconds <= RECENT_EXPENSE_RETRY_WINDOW_SECONDS


def _parse_timestamp(value: str, timezone_name: str) -> datetime | None:
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=ZoneInfo(timezone_name))
    return timestamp


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _default_transaction_id() -> str:
    return f"txn-{uuid4().hex}"
