from datetime import datetime, timezone
from decimal import Decimal

import pytest

from core.intent_parser import (
    IntentParser,
    IntentParserResult,
    MonthlyTotalQuery,
    ParsedExpense,
    ParserContext,
    ParserIntent,
)
from core.messages import InboundMessage
from core.exchange_rates import ExchangeRateConversion, ExchangeRateProviderError
from core.transaction_service import (
    EXCHANGE_RATE_FAILURE_MESSAGE,
    LOW_CONFIDENCE_MESSAGE,
    NO_RECENT_EXPENSE_MESSAGE,
    PROCESSING_FAILURE_MESSAGE,
    SIMILAR_RECENT_EXPENSE_MESSAGE,
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
    assert repository.find_calls == [("telegram", "42", "12345", "9001")]
    assert repository.appended_records == [
        make_record(
            transaction_id="txn-1",
            amount=Decimal("12.5"),
            merchant="麦当劳",
            note="午饭",
        )
    ]
    saved_record = repository.appended_records[0]
    assert saved_record.source_platform == "telegram"
    assert saved_record.source_username == "ada"
    assert saved_record.source_user_display_name == "Ada Lovelace"
    assert saved_record.source_chat_id == "12345"
    assert saved_record.created_at == "2026-05-20T13:00:00+08:00"
    assert saved_record.updated_at == "2026-05-20T13:00:00+08:00"


def test_create_expense_uses_generic_source_metadata_for_wechat_message():
    parser = FakeParser(
        make_parser_result(
            amount=Decimal("12.5"),
            date="2026-05-20",
            currency="SGD",
            category="餐饮",
            merchant="麦当劳",
        )
    )
    repository = FakeTransactionRepository()
    service = make_service(parser=parser, repository=repository)

    reply = service.handle_message(
        make_message(
            text="午饭 12.5 麦当劳",
            source_platform="wechat",
            source_user_id="wechat-user",
            source_chat_id="official-account",
            source_message_id="msg-9001",
            source_username=None,
            source_user_display_name=None,
        )
    )

    assert reply == "已记录：2026-05-20 餐饮 12.5 SGD 麦当劳"
    assert repository.find_calls == [
        ("wechat", "wechat-user", "official-account", "msg-9001")
    ]
    assert repository.appended_records == [
        make_record(
            transaction_id="txn-1",
            amount=Decimal("12.5"),
            merchant="麦当劳",
            source_platform="wechat",
            source_user_id="wechat-user",
            source_username=None,
            source_user_display_name=None,
            source_chat_id="official-account",
            source_message_id="msg-9001",
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


def test_create_expense_records_haircut_as_personal_care():
    parser = FakeParser(
        make_parser_result(
            amount=Decimal("19"),
            category="个人护理",
            merchant=None,
            note="剪头发",
        )
    )
    repository = FakeTransactionRepository()
    service = make_service(parser=parser, repository=repository)

    reply = service.handle_telegram_message(make_message(text="剪头发 19"))

    assert reply == "已记录：2026-05-20 个人护理 19 SGD 剪头发"
    assert repository.appended_records == [
        make_record(
            transaction_id="txn-1",
            amount=Decimal("19"),
            category="个人护理",
            merchant=None,
            note="剪头发",
        )
    ]


def test_create_expense_records_product_with_spec_numbers():
    parser = FakeParser(
        make_parser_result(
            amount=Decimal("6599"),
            currency="CNY",
            category="数码",
            merchant="ipad pro 13inch",
            note="ipad pro 13inch",
        )
    )
    repository = FakeTransactionRepository()
    service = make_service(parser=parser, repository=repository)

    reply = service.handle_telegram_message(
        make_message(text="@expenseBillingBot ipad pro 13inch 6599 cny")
    )

    assert reply == "已记录：2026-05-20 数码 6599 CNY ipad pro 13inch"
    assert repository.appended_records == [
        make_record(
            transaction_id="txn-1",
            amount=Decimal("6599"),
            currency="CNY",
            category="数码",
            merchant="ipad pro 13inch",
            note="ipad pro 13inch",
        )
    ]


def test_create_expense_records_attached_rmb_amount_as_cny():
    parser = IntentParser(
        llm_client=FakeLLMClient(
            {
                "intent": "create_expense",
                "confidence": 0.86,
                "expense": {
                    "date": "2026-05-20",
                    "amount": None,
                    "currency": "SGD",
                    "category": "餐饮",
                    "merchant": None,
                    "payment_method": None,
                    "note": "咖啡粉",
                },
                "update_fields": {},
                "query": None,
                "missing_fields": ["amount"],
            }
        )
    )
    repository = FakeTransactionRepository()
    service = make_service(parser=parser, repository=repository)

    reply = service.handle_telegram_message(
        make_message(text="@expenseBillingBot 咖啡粉 123rmb")
    )

    assert reply == "已记录：2026-05-20 餐饮 123 CNY 咖啡粉"
    assert repository.appended_records == [
        make_record(
            transaction_id="txn-1",
            amount=Decimal("123"),
            currency="CNY",
            merchant=None,
            note="咖啡粉",
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


def test_create_expense_retry_updates_recent_matching_expense_currency():
    parser = FakeParser(
        make_parser_result(
            amount=Decimal("13"),
            currency="CNY",
            category="餐饮",
            merchant="正新鸡排",
        )
    )
    repository = FakeTransactionRepository(
        latest_records={
            "42": make_record(
                transaction_id="txn-latest",
                amount=Decimal("13"),
                currency="SGD",
                category="餐饮",
                merchant="正新鸡排",
                created_at="2026-05-20T13:00:00+08:00",
            )
        }
    )
    service = make_service(parser=parser, repository=repository)

    reply = service.handle_telegram_message(
        make_message(
            text="正新鸡排 13cny",
            source_message_id="9002",
            received_at=datetime(2026, 5, 20, 5, 2, tzinfo=timezone.utc),
        )
    )

    assert reply == "已更新：2026-05-20 餐饮 13 CNY 正新鸡排"
    assert repository.latest_calls == [("telegram", "42")]
    assert repository.update_calls == [("txn-latest", {"currency": "CNY"})]
    assert repository.updated_records == [
        make_record(
            transaction_id="txn-latest",
            amount=Decimal("13"),
            currency="CNY",
            category="餐饮",
            merchant="正新鸡排",
        )
    ]
    assert repository.appended_records == []


def test_duplicate_create_retry_message_reuses_original_retry_target():
    parser = FakeParser(
        make_parser_result(
            amount=Decimal("13"),
            currency="CNY",
            category="餐饮",
            merchant="正新鸡排",
        )
    )
    repository = FakeTransactionRepository(
        latest_records={
            "42": make_record(
                transaction_id="txn-latest",
                amount=Decimal("13"),
                currency="SGD",
                category="餐饮",
                merchant="正新鸡排",
                created_at="2026-05-20T13:00:00+08:00",
            )
        }
    )
    service = make_service(parser=parser, repository=repository)
    message = make_message(
        text="正新鸡排 13cny",
        source_message_id="9002",
        received_at=datetime(2026, 5, 20, 5, 2, tzinfo=timezone.utc),
    )

    service.handle_telegram_message(message)
    reply = service.handle_telegram_message(message)

    assert reply == "已更新：2026-05-20 餐饮 13 CNY 正新鸡排"
    assert repository.latest_calls == [("telegram", "42")]
    assert repository.update_calls == [
        ("txn-latest", {"currency": "CNY"}),
        ("txn-latest", {"currency": "CNY"}),
    ]
    assert repository.appended_records == []


def test_create_expense_retry_matches_note_when_latest_record_has_merchant():
    parser = FakeParser(
        make_parser_result(
            amount=Decimal("5"),
            currency="CNY",
            category="餐饮",
            merchant=None,
            note="latte",
        )
    )
    repository = FakeTransactionRepository(
        latest_records={
            "42": make_record(
                transaction_id="txn-latest",
                amount=Decimal("5"),
                currency="SGD",
                category="餐饮",
                merchant="Starbucks",
                note="latte",
                created_at="2026-05-20T13:00:00+08:00",
            )
        }
    )
    service = make_service(parser=parser, repository=repository)

    reply = service.handle_telegram_message(
        make_message(
            text="latte 5cny",
            source_message_id="9002",
            received_at=datetime(2026, 5, 20, 5, 2, tzinfo=timezone.utc),
        )
    )

    assert reply == "已更新：2026-05-20 餐饮 5 CNY Starbucks"
    assert repository.update_calls == [("txn-latest", {"currency": "CNY"})]
    assert repository.appended_records == []


def test_create_expense_retry_guard_clarifies_ambiguous_similar_currency_retry():
    parser = FakeParser(
        make_parser_result(
            amount=Decimal("13"),
            currency="CNY",
            category="餐饮",
            merchant="正新鸡排新加坡",
        )
    )
    repository = FakeTransactionRepository(
        latest_records={
            "42": make_record(
                transaction_id="txn-latest",
                amount=Decimal("13"),
                currency="SGD",
                category="餐饮",
                merchant="正新鸡排",
                created_at="2026-05-20T13:00:00+08:00",
            )
        }
    )
    service = make_service(parser=parser, repository=repository)

    reply = service.handle_telegram_message(
        make_message(
            text="正新鸡排新加坡 13cny",
            source_message_id="9002",
            received_at=datetime(2026, 5, 20, 5, 2, tzinfo=timezone.utc),
        )
    )

    assert reply == (
        SIMILAR_RECENT_EXPENSE_MESSAGE
        + "：2026-05-20 餐饮 13 SGD 正新鸡排"
    )
    assert repository.latest_calls == [("telegram", "42")]
    assert repository.update_calls == []
    assert repository.appended_records == []


def test_create_expense_retry_guard_ignores_stale_recent_match():
    parser = FakeParser(
        make_parser_result(
            amount=Decimal("13"),
            currency="CNY",
            category="餐饮",
            merchant="正新鸡排",
        )
    )
    repository = FakeTransactionRepository(
        latest_records={
            "42": make_record(
                transaction_id="txn-latest",
                amount=Decimal("13"),
                currency="SGD",
                category="餐饮",
                merchant="正新鸡排",
                created_at="2026-05-20T12:00:00+08:00",
            )
        }
    )
    service = make_service(parser=parser, repository=repository)

    reply = service.handle_telegram_message(
        make_message(
            text="正新鸡排 13cny",
            source_message_id="9002",
            received_at=datetime(2026, 5, 20, 5, 2, tzinfo=timezone.utc),
        )
    )

    assert reply == "已记录：2026-05-20 餐饮 13 CNY 正新鸡排"
    assert repository.latest_calls == [("telegram", "42")]
    assert repository.update_calls == []
    assert repository.appended_records == [
        make_record(
            transaction_id="txn-1",
            amount=Decimal("13"),
            currency="CNY",
            category="餐饮",
            merchant="正新鸡排",
            source_message_id="9002",
        )
    ]


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
    assert repository.latest_calls == [("telegram", "42")]
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
            "刚才那笔备注是白鸡饭",
            {"note": "白鸡饭"},
            {"note": "白鸡饭"},
        ),
        (
            "刚才那笔是昨天的",
            {"date": "2026-05-19"},
            {"date": "2026-05-19"},
        ),
        (
            "刚才那笔改成 CNY",
            {"currency": "CNY"},
            {"currency": "CNY"},
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


def test_update_recent_expense_applies_semantic_food_correction_with_note():
    parser = FakeParser(
        make_update_parser_result(
            update_fields={
                "amount": Decimal("6.8"),
                "category": "餐饮",
                "note": "白鸡饭",
            }
        )
    )
    repository = FakeTransactionRepository(
        latest_records={
            "42": make_record(
                transaction_id="txn-latest",
                amount=Decimal("19"),
                category="个人护理",
                note="剪头发",
            )
        }
    )
    service = make_service(parser=parser, repository=repository)

    reply = service.handle_telegram_message(
        make_message(text="改一下，我没有剪头发，也没有去吃福建面，我吃了白鸡饭花了6.8")
    )

    assert reply == "已更新：2026-05-20 餐饮 6.8 SGD 白鸡饭"
    assert repository.update_calls == [
        (
            "txn-latest",
            {
                "amount": Decimal("6.8"),
                "category": "餐饮",
                "note": "白鸡饭",
            },
        )
    ]
    assert repository.updated_records == [
        make_record(
            transaction_id="txn-latest",
            amount=Decimal("6.8"),
            category="餐饮",
            note="白鸡饭",
        )
    ]


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
    assert repository.latest_calls == [("telegram", "42")]
    assert repository.update_calls == []


def test_update_recent_expense_updates_currency_and_confirms_updated_summary():
    parser = FakeParser(make_update_parser_result(update_fields={"currency": "CNY"}))
    repository = FakeTransactionRepository(
        latest_records={
            "42": make_record(
                transaction_id="txn-latest",
                currency="SGD",
                merchant="正新鸡排",
            )
        }
    )
    service = make_service(parser=parser, repository=repository)

    reply = service.handle_telegram_message(
        make_message(text="改成 cny")
    )

    assert reply == "已更新：2026-05-20 餐饮 12.5 CNY 正新鸡排"
    assert repository.latest_calls == [("telegram", "42")]
    assert repository.update_calls == [("txn-latest", {"currency": "CNY"})]
    assert repository.updated_records == [
        make_record(
            transaction_id="txn-latest",
            currency="CNY",
            merchant="正新鸡排",
        )
    ]
    assert repository.appended_records == []


def test_update_recent_expense_is_scoped_to_current_source_user():
    parser = FakeParser(make_update_parser_result(update_fields={"category": "办公"}))
    repository = FakeTransactionRepository(
        latest_records={
            "7": make_record(
                transaction_id="other-user-latest",
                source_user_id="7",
            )
        }
    )
    service = make_service(parser=parser, repository=repository)

    reply = service.handle_telegram_message(
        make_message(text="刚才那笔改成办公", source_user_id="42")
    )

    assert reply == NO_RECENT_EXPENSE_MESSAGE
    assert repository.latest_calls == [("telegram", "42")]
    assert repository.update_calls == []


def test_duplicate_update_message_reuses_original_target_transaction():
    parser = FakeParser(make_update_parser_result(update_fields={"category": "办公"}))
    repository = FakeTransactionRepository(
        latest_records={"42": make_record(transaction_id="original-latest")}
    )
    service = make_service(parser=parser, repository=repository)

    service.handle_telegram_message(
        make_message(text="刚才那笔改成办公", source_message_id="9002")
    )
    repository.set_latest_record(
        "42",
        make_record(transaction_id="newer-expense"),
    )
    service.handle_telegram_message(
        make_message(text="刚才那笔改成办公", source_message_id="9002")
    )

    assert repository.latest_calls == [("telegram", "42")]
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


@pytest.mark.parametrize(
    "text",
    [
        "这个月花了多少？",
        "本月支出多少？",
        "这月总共花了多少钱？",
    ],
)
def test_query_monthly_total_returns_current_user_sgd_total(text: str):
    parser = FakeParser(make_query_parser_result())
    repository = FakeTransactionRepository(
        monthly_records=[
            make_record(transaction_id="txn-a", amount=Decimal("120")),
            make_record(transaction_id="txn-b", amount=Decimal("3.4")),
        ]
    )
    service = make_service(parser=parser, repository=repository)

    reply = service.handle_telegram_message(make_message(text=text))

    assert reply == "本月支出合计：123.40 SGD"
    assert repository.list_monthly_calls == [("telegram", "42", "2026-05")]
    assert repository.appended_records == []
    assert repository.update_calls == []


def test_query_monthly_total_uses_message_timezone_for_current_month():
    parser = FakeParser(make_query_parser_result(month="2026-05", currency="SGD"))
    repository = FakeTransactionRepository(
        monthly_records=[make_record(amount=Decimal("8.8"))]
    )
    service = make_service(parser=parser, repository=repository)

    reply = service.handle_telegram_message(
        make_message(
            text="这个月花了多少？",
            received_at=datetime(2026, 4, 30, 16, 30, tzinfo=timezone.utc),
        )
    )

    assert reply == "本月支出合计：8.80 SGD"
    assert repository.list_monthly_calls == [("telegram", "42", "2026-05")]


def test_query_monthly_total_defaults_omitted_query_currency_to_sgd():
    parser = FakeParser(make_query_parser_result(currency=None))
    repository = FakeTransactionRepository(
        monthly_records=[make_record(amount=Decimal("11"))]
    )
    service = make_service(parser=parser, repository=repository)

    reply = service.handle_telegram_message(make_message(text="这个月花了多少？"))

    assert reply == "本月支出合计：11.00 SGD"
    assert repository.list_monthly_calls == [("telegram", "42", "2026-05")]


def test_query_monthly_total_converts_mixed_currencies_to_sgd():
    parser = FakeParser(make_query_parser_result())
    repository = FakeTransactionRepository(
        monthly_records=[
            make_record(
                transaction_id="sgd",
                date="2026-05-01",
                amount=Decimal("10"),
                currency="SGD",
            ),
            make_record(
                transaction_id="cny",
                date="2026-05-02",
                amount=Decimal("30"),
                currency="CNY",
            ),
            make_record(
                transaction_id="usd",
                date="2026-05-03",
                amount=Decimal("5"),
                currency="USD",
            ),
        ]
    )
    exchange_rate_provider = FakeExchangeRateProvider(
        rates={
            ("CNY", "SGD", "2026-05-02"): (Decimal("0.18"), "2026-05-02"),
            ("USD", "SGD", "2026-05-03"): (Decimal("1.35"), "2026-05-02"),
        }
    )
    service = make_service(
        parser=parser,
        repository=repository,
        exchange_rate_provider=exchange_rate_provider,
    )

    reply = service.handle_telegram_message(make_message(text="这个月花了多少？"))

    assert reply == (
        "本月支出合计：22.15 SGD\n"
        "其中换算：30 CNY -> 5.40 SGD (汇率日 2026-05-02); "
        "5 USD -> 6.75 SGD (汇率日 2026-05-02)"
    )
    assert repository.list_monthly_calls == [("telegram", "42", "2026-05")]
    assert exchange_rate_provider.calls == [
        (Decimal("30"), "CNY", "SGD", "2026-05-02"),
        (Decimal("5"), "USD", "SGD", "2026-05-03"),
    ]


def test_query_monthly_total_rejects_non_current_month_without_storage_lookup():
    parser = FakeParser(make_query_parser_result(month="2026-04", currency="SGD"))
    repository = FakeTransactionRepository(
        monthly_records=[make_record(amount=Decimal("123.4"))]
    )
    service = make_service(parser=parser, repository=repository)

    reply = service.handle_telegram_message(make_message(text="上个月花了多少？"))

    assert reply == "我目前只支持查询本月 SGD 支出总额。"
    assert repository.list_monthly_calls == []


def test_query_monthly_total_rejects_non_sgd_currency_without_storage_lookup():
    parser = FakeParser(make_query_parser_result(month="2026-05", currency="USD"))
    repository = FakeTransactionRepository(
        monthly_records=[make_record(amount=Decimal("123.4"))]
    )
    service = make_service(parser=parser, repository=repository)

    reply = service.handle_telegram_message(make_message(text="这个月 USD 花了多少？"))

    assert reply == "我目前只支持查询本月 SGD 支出总额。"
    assert repository.list_monthly_calls == []


def test_query_monthly_total_rejects_unsupported_currency_without_storage_lookup():
    parser = FakeParser(make_query_parser_result(month="2026-05", currency="ABC"))
    repository = FakeTransactionRepository(
        monthly_records=[make_record(amount=Decimal("123.4"))]
    )
    service = make_service(parser=parser, repository=repository)

    reply = service.handle_telegram_message(make_message(text="这个月 ABC 花了多少？"))

    assert reply == "我目前只支持查询本月 SGD 支出总额。"
    assert repository.list_monthly_calls == []


def test_query_monthly_total_formats_zero_sgd_total():
    parser = FakeParser(make_query_parser_result())
    repository = FakeTransactionRepository()
    service = make_service(parser=parser, repository=repository)

    reply = service.handle_telegram_message(make_message(text="本月支出多少？"))

    assert reply == "本月支出合计：0.00 SGD"
    assert repository.list_monthly_calls == [("telegram", "42", "2026-05")]


def test_query_monthly_total_repository_failure_returns_fallback():
    parser = FakeParser(make_query_parser_result())
    repository = FakeTransactionRepository(fail_list_monthly=True)
    service = make_service(parser=parser, repository=repository)

    reply = service.handle_telegram_message(make_message(text="这个月花了多少？"))

    assert reply == PROCESSING_FAILURE_MESSAGE
    assert repository.list_monthly_calls == [("telegram", "42", "2026-05")]


def test_query_monthly_total_exchange_rate_failure_returns_fallback():
    parser = FakeParser(make_query_parser_result())
    repository = FakeTransactionRepository(
        monthly_records=[
            make_record(
                transaction_id="cny",
                date="2026-05-02",
                amount=Decimal("30"),
                currency="CNY",
            )
        ]
    )
    exchange_rate_provider = FakeExchangeRateProvider(fail=True)
    service = make_service(
        parser=parser,
        repository=repository,
        exchange_rate_provider=exchange_rate_provider,
    )

    reply = service.handle_telegram_message(make_message(text="本月支出多少？"))

    assert reply == EXCHANGE_RATE_FAILURE_MESSAGE
    assert repository.list_monthly_calls == [("telegram", "42", "2026-05")]


def make_service(
    *,
    parser: "FakeParser",
    repository: "FakeTransactionRepository",
    exchange_rate_provider: "FakeExchangeRateProvider | None" = None,
) -> TransactionService:
    return TransactionService(
        parser=parser,
        repository=repository,
        exchange_rate_provider=exchange_rate_provider or FakeExchangeRateProvider(),
        timezone="Asia/Singapore",
        default_currency="SGD",
        clock=lambda: datetime(2026, 5, 20, 5, 0, tzinfo=timezone.utc),
        id_factory=lambda: "txn-1",
    )


def make_message(
    *,
    text: str,
    source_platform: str = "telegram",
    source_user_id: str = "42",
    source_username: str | None = "ada",
    source_user_display_name: str | None = "Ada Lovelace",
    source_chat_id: str = "12345",
    source_message_id: str = "9001",
    received_at: datetime = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
) -> InboundMessage:
    return InboundMessage(
        source_platform=source_platform,
        source_user_id=source_user_id,
        source_chat_id=source_chat_id,
        source_message_id=source_message_id,
        message_text=text,
        received_at=received_at,
        source_username=source_username,
        source_user_display_name=source_user_display_name,
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


def make_query_parser_result(
    *,
    confidence: float = 0.9,
    month: str = "2026-05",
    currency: str | None = "SGD",
) -> IntentParserResult:
    return IntentParserResult(
        is_success=True,
        intent=ParserIntent.QUERY_MONTHLY_TOTAL,
        confidence=confidence,
        expense=None,
        update_fields={},
        query=MonthlyTotalQuery(month=month, currency=currency),
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
    source_platform: str = "telegram",
    source_user_id: str = "42",
    source_username: str | None = "ada",
    source_user_display_name: str | None = "Ada Lovelace",
    source_chat_id: str = "12345",
    source_message_id: str = "9001",
    created_at: str = "2026-05-20T13:00:00+08:00",
    updated_at: str = "2026-05-20T13:00:00+08:00",
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
        source_platform=source_platform,
        source_user_id=source_user_id,
        source_username=source_username,
        source_user_display_name=source_user_display_name,
        source_chat_id=source_chat_id,
        source_message_id=source_message_id,
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


class FakeLLMClient:
    def __init__(self, response: dict[str, object]) -> None:
        self._response = response

    def complete_json(self, *, system_prompt: str, user_prompt: str) -> str:
        import json

        return json.dumps(self._response)


class FakeTransactionRepository:
    def __init__(
        self,
        *,
        existing_record: TransactionRecord | None = None,
        latest_records: dict[str | tuple[str, str], TransactionRecord] | None = None,
        monthly_records: list[TransactionRecord] | None = None,
        fail_append: bool = False,
        fail_update: bool = False,
        fail_list_monthly: bool = False,
    ) -> None:
        self._existing_record = existing_record
        self._latest_records = {
            _source_key(key): record
            for key, record in (latest_records or {}).items()
        }
        self._records_by_id = {
            record.id: record for record in self._latest_records.values()
        }
        self._monthly_records = monthly_records or []
        self._fail_append = fail_append
        self._fail_update = fail_update
        self._fail_list_monthly = fail_list_monthly
        self.find_calls: list[tuple[str, str, str, str]] = []
        self.latest_calls: list[tuple[str, str]] = []
        self.appended_records: list[TransactionRecord] = []
        self.update_calls: list[tuple[str, dict[str, object]]] = []
        self.updated_records: list[TransactionRecord] = []
        self.list_monthly_calls: list[tuple[str, str, str]] = []

    def set_latest_record(self, user_id: str, record: TransactionRecord) -> None:
        self._latest_records[("telegram", user_id)] = record
        self._records_by_id[record.id] = record

    def find_by_source_message(
        self,
        *,
        source_platform: str,
        user_id: str,
        chat_id: str,
        message_id: str,
    ) -> TransactionRecord | None:
        self.find_calls.append((source_platform, user_id, chat_id, message_id))
        return self._existing_record

    def append_transaction(self, record: TransactionRecord) -> TransactionRecord:
        if self._fail_append:
            raise TransactionRepositoryError("append failed")
        self.appended_records.append(record)
        return record

    def get_latest_transaction(
        self,
        *,
        source_platform: str,
        user_id: str,
    ) -> TransactionRecord | None:
        self.latest_calls.append((source_platform, user_id))
        return self._latest_records.get((source_platform, user_id))

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
            "updated_at": "2026-05-20T13:00:00+08:00",
        }
        updated_record = TransactionRecord(**updated_values)
        self._records_by_id[transaction_id] = updated_record
        self.updated_records.append(updated_record)
        return updated_record

    def list_monthly_expenses(
        self,
        *,
        source_platform: str,
        user_id: str,
        month: str,
    ) -> list[TransactionRecord]:
        self.list_monthly_calls.append((source_platform, user_id, month))
        if self._fail_list_monthly:
            raise TransactionRepositoryError("list failed")

        return list(self._monthly_records)


def _source_key(key: object) -> tuple[str, str]:
    if isinstance(key, tuple):
        return key
    return ("telegram", str(key))


class FakeExchangeRateProvider:
    def __init__(
        self,
        *,
        rates: dict[tuple[str, str, str], tuple[Decimal, str]] | None = None,
        fail: bool = False,
    ) -> None:
        self._rates = rates or {}
        self._fail = fail
        self.calls: list[tuple[Decimal, str, str, str]] = []

    def convert(
        self,
        amount: Decimal,
        *,
        from_currency: str,
        to_currency: str,
        date: str,
    ) -> ExchangeRateConversion:
        self.calls.append((amount, from_currency, to_currency, date))
        if self._fail:
            raise ExchangeRateProviderError("rate unavailable")
        rate, rate_date = self._rates[(from_currency, to_currency, date)]
        return ExchangeRateConversion(
            original_amount=amount,
            original_currency=from_currency,
            converted_amount=amount * rate,
            converted_currency=to_currency,
            rate=rate,
            rate_date=rate_date,
        )
