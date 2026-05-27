import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

from core.currencies import normalize_currency_code
from core.exchange_rates import (
    ExchangeRateConversion,
    ExchangeRateProvider,
    ExchangeRateProviderError,
)
from core.intent_parser import IntentParserResult, ParserContext, ParserIntent
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
MIN_CREATE_EXPENSE_CONFIDENCE = 0.7
MIN_UPDATE_EXPENSE_CONFIDENCE = 0.7
MIN_QUERY_MONTHLY_TOTAL_CONFIDENCE = 0.7
MONTHLY_TOTAL_AMOUNT_QUANTUM = Decimal("0.01")

logger = logging.getLogger(__name__)


class TelegramMessage(Protocol):
    telegram_user_id: str
    telegram_username: str | None
    telegram_user_display_name: str | None
    chat_id: str
    message_id: str
    message_text: str
    received_at: datetime


class ExpenseIntentParser(Protocol):
    def parse(
        self,
        text: str,
        *,
        context: ParserContext,
    ) -> IntentParserResult:
        raise NotImplementedError


class TransactionRepository(Protocol):
    def find_by_telegram_message(
        self,
        *,
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
        user_id: str,
        month: str,
    ) -> list[TransactionRecord]:
        raise NotImplementedError


@dataclass(frozen=True)
class _MonthlyTotalSummary:
    total: Decimal
    currency: str
    conversions: tuple[ExchangeRateConversion, ...]


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
        self._update_targets_by_message: dict[tuple[str, str, str], str] = {}

    def __call__(self, message: TelegramMessage) -> str:
        return self.handle_telegram_message(message)

    def handle_telegram_message(self, message: TelegramMessage) -> str:
        try:
            existing_record = self._repository.find_by_telegram_message(
                user_id=message.telegram_user_id,
                chat_id=message.chat_id,
                message_id=message.message_id,
            )
        except TransactionRepositoryError:
            logger.exception("Failed to check transaction duplicate status.")
            return PROCESSING_FAILURE_MESSAGE

        if existing_record is not None:
            return _format_confirmation(existing_record)

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
            logger.exception("Failed to parse Telegram message.")
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

        record = self._new_transaction_record(
            validation.expense,
            message=message,
        )
        try:
            saved_record = self._repository.append_transaction(record)
        except TransactionRepositoryError:
            logger.exception("Failed to append transaction.")
            return PROCESSING_FAILURE_MESSAGE

        return _format_confirmation(saved_record)

    def _handle_update_recent_expense(
        self,
        parser_result: IntentParserResult,
        *,
        message: TelegramMessage,
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
            message.telegram_user_id,
            message.chat_id,
            message.message_id,
        )
        transaction_id = self._update_targets_by_message.get(update_message_key)
        if transaction_id is None:
            try:
                latest_record = self._repository.get_latest_transaction(
                    user_id=message.telegram_user_id,
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
        return _format_update_confirmation(updated_record)

    def _handle_query_monthly_total(
        self,
        parser_result: IntentParserResult,
        *,
        message: TelegramMessage,
    ) -> str:
        if parser_result.confidence < MIN_QUERY_MONTHLY_TOTAL_CONFIDENCE:
            return LOW_CONFIDENCE_MESSAGE

        query = parser_result.query
        if query is None:
            logger.error("Monthly total query intent missing query fields.")
            return PROCESSING_FAILURE_MESSAGE

        current_month = _message_month(message.received_at, self._timezone)
        currency = normalize_currency_code(self._default_currency)
        query_currency = normalize_currency_code(
            query.currency,
            default_currency=self._default_currency,
        )
        if currency is None:
            return PROCESSING_FAILURE_MESSAGE
        if query_currency is None:
            return _format_unsupported_monthly_total_reply(currency)
        if query.month != current_month or query_currency != currency:
            return _format_unsupported_monthly_total_reply(currency)

        try:
            records = self._repository.list_monthly_expenses(
                user_id=message.telegram_user_id,
                month=current_month,
            )
        except TransactionRepositoryError:
            logger.exception("Failed to list monthly expenses.")
            return PROCESSING_FAILURE_MESSAGE

        try:
            summary = _summarize_monthly_records(
                records,
                currency=currency,
                exchange_rate_provider=self._exchange_rate_provider,
            )
        except ExchangeRateProviderError:
            logger.exception("Failed to convert monthly expenses.")
            return EXCHANGE_RATE_FAILURE_MESSAGE

        return _format_monthly_total_reply(summary)

    def _new_transaction_record(
        self,
        expense: ValidatedExpense,
        *,
        message: TelegramMessage,
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
            telegram_user_id=message.telegram_user_id,
            telegram_username=message.telegram_username,
            telegram_user_display_name=message.telegram_user_display_name,
            telegram_chat_id=message.chat_id,
            telegram_message_id=message.message_id,
            created_at=timestamp,
            updated_at=timestamp,
        )


def _format_confirmation(record: TransactionRecord) -> str:
    return "已记录：" + _format_record_summary(record)


def _format_update_confirmation(record: TransactionRecord) -> str:
    return "已更新：" + _format_record_summary(record)


def _format_monthly_total_reply(summary: _MonthlyTotalSummary) -> str:
    amount = _format_money(summary.total)
    reply = f"本月支出合计：{amount} {summary.currency}"
    if not summary.conversions:
        return reply

    conversion_parts = [
        (
            f"{format(conversion.original_amount, 'f')} "
            f"{conversion.original_currency} -> "
            f"{_format_money(conversion.converted_amount)} "
            f"{conversion.converted_currency} "
            f"(汇率日 {conversion.rate_date})"
        )
        for conversion in summary.conversions
    ]
    return reply + "\n其中换算：" + "; ".join(conversion_parts)


def _format_unsupported_monthly_total_reply(currency: str) -> str:
    return f"我目前只支持查询本月 {currency} 支出总额。"


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


def _message_month(timestamp: datetime, timezone_name: str) -> str:
    return _message_date(timestamp, timezone_name).strftime("%Y-%m")


def _summarize_monthly_records(
    records: list[TransactionRecord],
    *,
    currency: str,
    exchange_rate_provider: ExchangeRateProvider | None,
) -> _MonthlyTotalSummary:
    total = Decimal("0")
    conversions: list[ExchangeRateConversion] = []
    for record in records:
        record_currency = normalize_currency_code(record.currency)
        if record_currency is None:
            raise ExchangeRateProviderError(
                f"Unsupported stored currency: {record.currency}"
            )
        if record_currency == currency:
            total += record.amount
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
        conversions.append(conversion)

    return _MonthlyTotalSummary(
        total=total,
        currency=currency,
        conversions=tuple(conversions),
    )


def _format_money(amount: Decimal) -> str:
    return format(amount.quantize(MONTHLY_TOTAL_AMOUNT_QUANTUM), "f")


def _format_timestamp(timestamp: datetime, timezone_name: str) -> str:
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=ZoneInfo(timezone_name))
    return timestamp.astimezone(ZoneInfo(timezone_name)).isoformat()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _default_transaction_id() -> str:
    return f"txn-{uuid4().hex}"
