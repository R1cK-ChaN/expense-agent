from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.telegram_webhook import TelegramInboundMessage
from core.intent_parser import (
    IntentParserResult,
    ParsedExpense,
    ParserContext,
    ParserIntent,
)
from core.transaction_service import (
    LOW_CONFIDENCE_MESSAGE,
    NO_RECENT_EXPENSE_MESSAGE,
    PROCESSING_FAILURE_MESSAGE,
    UNKNOWN_INTENT_MESSAGE,
    TransactionService,
)
from core.validator import MISSING_AMOUNT_MESSAGE, UNSUPPORTED_UPDATE_FIELD_MESSAGE
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


def test_update_recent_expense_updates_category_and_confirms_updated_summary():
    parser = FakeParser(make_update_parser_result(update_fields={"category": "办公"}))
    repository = FakeTransactionRepository(
        latest_records={
            "42": make_record(
                transaction_id="txn-latest",
                category="餐饮",
                merchant="麦当劳",
            )
        }
    )
    service = make_service(parser=parser, repository=repository)

    reply = service.handle_telegram_message(
        make_message(text="刚才那笔改成办公")
    )

    assert reply == "已更新：2026-05-20 办公 12.5 SGD 麦当劳"
    assert repository.latest_calls == ["42"]
    assert repository.update_calls == [("txn-latest", {"category": "办公"})]
    assert repository.updated_records == [
        make_record(
            transaction_id="txn-latest",
            category="办公",
            merchant="麦当劳",
        )
    ]
    assert repository.appended_records == []


@pytest.mark.parametrize(
    ("text", "update_fields", "expected_record_fields"),
    [
        (
            "刚才那笔金额是 18.6",
            {"amount": Decimal("18.6")},
            {"amount": Decimal("18.6")},
        ),
        (
            "刚才那笔是 Grab",
            {"merchant": "Grab"},
            {"merchant": "Grab"},
        ),
        (
            "刚才那笔支付方式是 Visa",
            {"payment_method": "Visa"},
            {"payment_method": "Visa"},
        ),
        (
            "刚才那笔是昨天的",
            {"date": "2026-05-19"},
            {"date": "2026-05-19"},
        ),
    ],
)
def test_update_recent_expense_updates_supported_fields(
    text: str,
    update_fields: dict[str, object],
    expected_record_fields: dict[str, object],
):
    parser = FakeParser(make_update_parser_result(update_fields=update_fields))
    repository = FakeTransactionRepository(
        latest_records={"42": make_record(transaction_id="txn-latest")}
    )
    service = make_service(parser=parser, repository=repository)

    reply = service.handle_telegram_message(make_message(text=text))

    expected_record = make_record(
        transaction_id="txn-latest",
        **expected_record_fields,
    )
    assert reply == format_expected_update_confirmation(expected_record)
    assert repository.update_calls == [("txn-latest", update_fields)]
    assert repository.updated_records == [expected_record]


def test_update_recent_expense_without_latest_record_returns_prd_reply():
    parser = FakeParser(
        make_update_parser_result(update_fields={"amount": Decimal("18.6")})
    )
    repository = FakeTransactionRepository()
    service = make_service(parser=parser, repository=repository)

    reply = service.handle_telegram_message(
        make_message(text="刚才那笔金额是 18.6")
    )

    assert reply == NO_RECENT_EXPENSE_MESSAGE
    assert repository.latest_calls == ["42"]
    assert repository.update_calls == []


def test_update_recent_expense_rejects_unsupported_fields_without_storage_lookup():
    parser = FakeParser(make_update_parser_result(update_fields={"currency": "USD"}))
    repository = FakeTransactionRepository(
        latest_records={"42": make_record(transaction_id="txn-latest")}
    )
    service = make_service(parser=parser, repository=repository)

    reply = service.handle_telegram_message(
        make_message(text="刚才那笔改成 USD")
    )

    assert reply == UNSUPPORTED_UPDATE_FIELD_MESSAGE
    assert repository.latest_calls == []
    assert repository.update_calls == []


def test_update_recent_expense_is_scoped_to_current_telegram_user():
    parser = FakeParser(make_update_parser_result(update_fields={"category": "办公"}))
    repository = FakeTransactionRepository(
        latest_records={
            "7": make_record(
                transaction_id="other-user-latest",
                telegram_user_id="7",
            )
        }
    )
    service = make_service(parser=parser, repository=repository)

    reply = service.handle_telegram_message(
        make_message(text="刚才那笔改成办公", telegram_user_id="42")
    )

    assert reply == NO_RECENT_EXPENSE_MESSAGE
    assert repository.latest_calls == ["42"]
    assert repository.update_calls == []


def test_duplicate_update_message_reuses_original_target_transaction():
    parser = FakeParser(make_update_parser_result(update_fields={"category": "办公"}))
    repository = FakeTransactionRepository(
        latest_records={"42": make_record(transaction_id="original-latest")}
    )
    service = make_service(parser=parser, repository=repository)

    service.handle_telegram_message(
        make_message(text="刚才那笔改成办公", message_id="9002")
    )
    repository.set_latest_record(
        "42",
        make_record(transaction_id="newer-expense"),
    )
    service.handle_telegram_message(
        make_message(text="刚才那笔改成办公", message_id="9002")
    )

    assert repository.latest_calls == ["42"]
    assert repository.update_calls == [
        ("original-latest", {"category": "办公"}),
        ("original-latest", {"category": "办公"}),
    ]


def test_google_sheets_update_failure_returns_fallback():
    parser = FakeParser(make_update_parser_result(update_fields={"category": "办公"}))
    repository = FakeTransactionRepository(
        latest_records={"42": make_record(transaction_id="txn-latest")},
        fail_update=True,
    )
    service = make_service(parser=parser, repository=repository)

    reply = service.handle_telegram_message(
        make_message(text="刚才那笔改成办公")
    )

    assert reply == PROCESSING_FAILURE_MESSAGE
    assert repository.update_calls == [("txn-latest", {"category": "办公"})]


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
    telegram_user_id: str = "42",
    message_id: str = "9001",
    received_at: datetime = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
) -> TelegramInboundMessage:
    return TelegramInboundMessage(
        telegram_user_id=telegram_user_id,
        chat_id="12345",
        message_id=message_id,
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


def make_update_parser_result(
    *,
    confidence: float = 0.9,
    update_fields: dict[str, object],
) -> IntentParserResult:
    return IntentParserResult(
        is_success=True,
        intent=ParserIntent.UPDATE_RECENT_EXPENSE,
        confidence=confidence,
        expense=None,
        update_fields=update_fields,
        query=None,
        missing_fields=(),
    )


def format_expected_update_confirmation(record: TransactionRecord) -> str:
    parts = [
        record.date,
        record.category,
        format(record.amount, "f"),
        record.currency,
    ]
    description = record.merchant or record.note
    if description:
        parts.append(description)

    return "已更新：" + " ".join(parts)


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
        latest_records: dict[str, TransactionRecord] | None = None,
        fail_append: bool = False,
        fail_update: bool = False,
    ) -> None:
        self._existing_record = existing_record
        self._latest_records = latest_records or {}
        self._records_by_id = {
            record.id: record for record in self._latest_records.values()
        }
        self._fail_append = fail_append
        self._fail_update = fail_update
        self.find_calls: list[tuple[str, str]] = []
        self.latest_calls: list[str] = []
        self.appended_records: list[TransactionRecord] = []
        self.update_calls: list[tuple[str, dict[str, object]]] = []
        self.updated_records: list[TransactionRecord] = []

    def set_latest_record(self, user_id: str, record: TransactionRecord) -> None:
        self._latest_records[user_id] = record
        self._records_by_id[record.id] = record

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

    def get_latest_transaction(
        self,
        *,
        user_id: str,
    ) -> TransactionRecord | None:
        self.latest_calls.append(user_id)
        return self._latest_records.get(user_id)

    def update_transaction(
        self,
        transaction_id: str,
        fields: dict[str, object],
    ) -> TransactionRecord:
        self.update_calls.append((transaction_id, fields))
        if self._fail_update:
            raise TransactionRepositoryError("update failed")

        latest_record = self._records_by_id[transaction_id]
        updated_values = {
            **latest_record.__dict__,
            **fields,
            "updated_at": "2026-05-20T05:00:00+00:00",
        }
        updated_record = TransactionRecord(**updated_values)
        self._records_by_id[transaction_id] = updated_record
        self.updated_records.append(updated_record)
        return updated_record
