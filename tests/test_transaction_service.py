from datetime import datetime, timezone
from decimal import Decimal

from app.telegram_webhook import TelegramInboundMessage
from core.intent_parser import (
    IntentParserResult,
    ParsedExpense,
    ParserContext,
    ParserIntent,
)
from core.transaction_service import (
    LOW_CONFIDENCE_MESSAGE,
    PROCESSING_FAILURE_MESSAGE,
    UNKNOWN_INTENT_MESSAGE,
    TransactionService,
)
from core.validator import MISSING_AMOUNT_MESSAGE
from integrations.google_sheets.repository import (
    TransactionRecord,
    TransactionRepositoryError,
)


def test_create_expense_appends_transaction_and_confirms_saved_summary():
    parser = FakeParser(
        make_parser_result(
            amount=Decimal("12.5"),
            date="2026-05-20",
            currency="SGD",
            category="餐饮",
            merchant="麦当劳",
            note="午饭",
        )
    )
    repository = FakeTransactionRepository()
    service = make_service(parser=parser, repository=repository)

    reply = service.handle_telegram_message(
        make_message(text="午饭 12.5 麦当劳")
    )

    assert reply == "已记录：2026-05-20 餐饮 12.5 SGD 麦当劳"
    assert parser.calls == [
        (
            "午饭 12.5 麦当劳",
            ParserContext(
                today=datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc).date(),
                timezone="Asia/Singapore",
                default_currency="SGD",
            ),
        )
    ]
    assert repository.find_calls == [("42", "9001")]
    assert repository.appended_records == [
        make_record(
            transaction_id="txn-1",
            amount=Decimal("12.5"),
            merchant="麦当劳",
            note="午饭",
        )
    ]


def test_create_expense_defaults_missing_date_and_currency_before_append():
    parser = FakeParser(
        make_parser_result(
            amount=Decimal("18.6"),
            date=None,
            currency=None,
            category="交通",
            merchant="grab",
        )
    )
    repository = FakeTransactionRepository()
    service = make_service(parser=parser, repository=repository)

    reply = service.handle_telegram_message(
        make_message(
            text="grab 18.6",
            received_at=datetime(2026, 5, 19, 16, 30, tzinfo=timezone.utc),
        )
    )

    assert reply == "已记录：2026-05-20 交通 18.6 SGD grab"
    assert repository.appended_records == [
        make_record(
            transaction_id="txn-1",
            date="2026-05-20",
            amount=Decimal("18.6"),
            category="交通",
            merchant="grab",
        )
    ]


def test_create_expense_preserves_parser_resolved_relative_date():
    parser = FakeParser(
        make_parser_result(
            amount=Decimal("8.9"),
            date="2026-05-19",
            currency="SGD",
            category="餐饮",
            merchant="星巴克",
        )
    )
    repository = FakeTransactionRepository()
    service = make_service(parser=parser, repository=repository)

    reply = service.handle_telegram_message(
        make_message(text="昨天星巴克 8.9")
    )

    assert reply == "已记录：2026-05-19 餐饮 8.9 SGD 星巴克"
    assert repository.appended_records[0].date == "2026-05-19"


def test_create_expense_missing_amount_does_not_append_and_asks_for_amount():
    parser = FakeParser(
        make_parser_result(
            amount=None,
            date="2026-05-20",
            currency="SGD",
            category="餐饮",
            note="今天喝咖啡",
            missing_fields=("amount",),
        )
    )
    repository = FakeTransactionRepository()
    service = make_service(parser=parser, repository=repository)

    reply = service.handle_telegram_message(make_message(text="今天喝咖啡"))

    assert reply == MISSING_AMOUNT_MESSAGE
    assert repository.appended_records == []


def test_duplicate_telegram_message_returns_existing_confirmation_without_append():
    existing_record = make_record(
        transaction_id="txn-existing",
        amount=Decimal("12.5"),
        merchant="麦当劳",
    )
    parser = FakeParser(make_parser_result())
    repository = FakeTransactionRepository(existing_record=existing_record)
    service = make_service(parser=parser, repository=repository)

    reply = service.handle_telegram_message(
        make_message(text="午饭 12.5 麦当劳")
    )

    assert reply == "已记录：2026-05-20 餐饮 12.5 SGD 麦当劳"
    assert parser.calls == []
    assert repository.appended_records == []


def test_unknown_intent_returns_guidance_without_append():
    parser = FakeParser(
        IntentParserResult(
            is_success=True,
            intent=ParserIntent.UNKNOWN,
            confidence=0.35,
            expense=None,
            update_fields={},
            query=None,
            missing_fields=(),
        )
    )
    repository = FakeTransactionRepository()
    service = make_service(parser=parser, repository=repository)

    reply = service.handle_telegram_message(make_message(text="你好"))

    assert reply == UNKNOWN_INTENT_MESSAGE
    assert repository.appended_records == []


def test_low_confidence_create_expense_does_not_append_and_asks_to_rephrase():
    parser = FakeParser(make_parser_result(confidence=0.49))
    repository = FakeTransactionRepository()
    service = make_service(parser=parser, repository=repository)

    reply = service.handle_telegram_message(make_message(text="可能午饭吧"))

    assert reply == LOW_CONFIDENCE_MESSAGE
    assert repository.appended_records == []


def test_parser_failure_returns_fallback_without_append():
    parser = FakeParser(IntentParserResult.failure("llm_provider_error"))
    repository = FakeTransactionRepository()
    service = make_service(parser=parser, repository=repository)

    reply = service.handle_telegram_message(make_message(text="午饭 12.5"))

    assert reply == PROCESSING_FAILURE_MESSAGE
    assert repository.appended_records == []


def test_google_sheets_append_failure_returns_fallback():
    parser = FakeParser(make_parser_result())
    repository = FakeTransactionRepository(fail_append=True)
    service = make_service(parser=parser, repository=repository)

    reply = service.handle_telegram_message(make_message(text="午饭 12.5"))

    assert reply == PROCESSING_FAILURE_MESSAGE
    assert repository.appended_records == []


def make_service(
    *,
    parser: "FakeParser",
    repository: "FakeTransactionRepository",
) -> TransactionService:
    return TransactionService(
        parser=parser,
        repository=repository,
        timezone="Asia/Singapore",
        default_currency="SGD",
        clock=lambda: datetime(2026, 5, 20, 5, 0, tzinfo=timezone.utc),
        id_factory=lambda: "txn-1",
    )


def make_message(
    *,
    text: str,
    received_at: datetime = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
) -> TelegramInboundMessage:
    return TelegramInboundMessage(
        telegram_user_id="42",
        chat_id="12345",
        message_id="9001",
        message_text=text,
        received_at=received_at,
    )


def make_parser_result(
    *,
    confidence: float = 0.9,
    amount: Decimal | None = Decimal("12.5"),
    date: str | None = "2026-05-20",
    currency: str | None = "SGD",
    category: str | None = "餐饮",
    merchant: str | None = "麦当劳",
    payment_method: str | None = None,
    note: str | None = None,
    missing_fields: tuple[str, ...] = (),
) -> IntentParserResult:
    return IntentParserResult(
        is_success=True,
        intent=ParserIntent.CREATE_EXPENSE,
        confidence=confidence,
        expense=ParsedExpense(
            date=date,
            amount=amount,
            currency=currency,
            category=category,
            merchant=merchant,
            payment_method=payment_method,
            note=note,
            type="expense",
        ),
        update_fields={},
        query=None,
        missing_fields=missing_fields,
    )


def make_record(
    *,
    transaction_id: str = "txn-1",
    date: str = "2026-05-20",
    amount: Decimal = Decimal("12.5"),
    currency: str = "SGD",
    transaction_type: str = "expense",
    category: str = "餐饮",
    merchant: str | None = None,
    payment_method: str | None = None,
    note: str | None = None,
    telegram_user_id: str = "42",
    telegram_message_id: str = "9001",
    created_at: str = "2026-05-20T05:00:00+00:00",
    updated_at: str = "2026-05-20T05:00:00+00:00",
) -> TransactionRecord:
    return TransactionRecord(
        id=transaction_id,
        date=date,
        amount=amount,
        currency=currency,
        type=transaction_type,
        category=category,
        merchant=merchant,
        payment_method=payment_method,
        note=note,
        telegram_user_id=telegram_user_id,
        telegram_message_id=telegram_message_id,
        created_at=created_at,
        updated_at=updated_at,
    )


class FakeParser:
    def __init__(self, result: IntentParserResult) -> None:
        self._result = result
        self.calls: list[tuple[str, ParserContext]] = []

    def parse(self, text: str, *, context: ParserContext) -> IntentParserResult:
        self.calls.append((text, context))
        return self._result


class FakeTransactionRepository:
    def __init__(
        self,
        *,
        existing_record: TransactionRecord | None = None,
        fail_append: bool = False,
    ) -> None:
        self._existing_record = existing_record
        self._fail_append = fail_append
        self.find_calls: list[tuple[str, str]] = []
        self.appended_records: list[TransactionRecord] = []

    def find_by_telegram_message(
        self,
        *,
        user_id: str,
        message_id: str,
    ) -> TransactionRecord | None:
        self.find_calls.append((user_id, message_id))
        return self._existing_record

    def append_transaction(self, record: TransactionRecord) -> TransactionRecord:
        if self._fail_append:
            raise TransactionRepositoryError("append failed")
        self.appended_records.append(record)
        return record
