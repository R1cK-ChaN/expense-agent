import logging
from collections.abc import Callable
from datetime import date, datetime, timezone
from typing import Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

from core.intent_parser import IntentParserResult, ParserContext, ParserIntent
from core.validator import (
    ValidationContext,
    ValidationResult,
    ValidatedExpense,
    validate_create_expense,
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
MIN_CREATE_EXPENSE_CONFIDENCE = 0.7

logger = logging.getLogger(__name__)


class TelegramMessage(Protocol):
    telegram_user_id: str
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
        message_id: str,
    ) -> TransactionRecord | None:
        raise NotImplementedError

    def append_transaction(self, record: TransactionRecord) -> TransactionRecord:
        raise NotImplementedError


class CreateExpenseValidator(Protocol):
    def __call__(
        self,
        parser_result: IntentParserResult,
        *,
        context: ValidationContext,
        source_text: str | None = None,
    ) -> ValidationResult:
        raise NotImplementedError


class TransactionService:
    def __init__(
        self,
        *,
        parser: ExpenseIntentParser,
        repository: TransactionRepository,
        timezone: str,
        default_currency: str,
        validator: CreateExpenseValidator = validate_create_expense,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._parser = parser
        self._repository = repository
        self._timezone = timezone
        self._default_currency = default_currency
        self._validator = validator
        self._clock = clock or _utc_now
        self._id_factory = id_factory or _default_transaction_id

    def __call__(self, message: TelegramMessage) -> str:
        return self.handle_telegram_message(message)

    def handle_telegram_message(self, message: TelegramMessage) -> str:
        try:
            existing_record = self._repository.find_by_telegram_message(
                user_id=message.telegram_user_id,
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

    def _new_transaction_record(
        self,
        expense: ValidatedExpense,
        *,
        message: TelegramMessage,
    ) -> TransactionRecord:
        timestamp = _format_timestamp(self._clock())
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
            telegram_message_id=message.message_id,
            created_at=timestamp,
            updated_at=timestamp,
        )


def _format_confirmation(record: TransactionRecord) -> str:
    parts = [
        record.date,
        record.category,
        format(record.amount, "f"),
        record.currency,
    ]
    description = record.merchant or record.note
    if description:
        parts.append(description)

    return "已记录：" + " ".join(parts)


def _message_date(timestamp: datetime, timezone_name: str) -> date:
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=ZoneInfo(timezone_name))
    return timestamp.astimezone(ZoneInfo(timezone_name)).date()


def _format_timestamp(timestamp: datetime) -> str:
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc).isoformat()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _default_transaction_id() -> str:
    return f"txn-{uuid4().hex}"
