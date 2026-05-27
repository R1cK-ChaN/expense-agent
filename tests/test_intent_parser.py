from datetime import date
from decimal import Decimal

import pytest

from core.intent_parser import (
    IntentParser,
    ParserContext,
    ParserIntent,
    SUPPORTED_CATEGORIES,
)


TODAY = date(2026, 5, 20)


def test_parser_extracts_create_expense_with_defaults_and_no_missing_fields():
    llm_client = FakeLLMClient(
        {
            "intent": "create_expense",
            "confidence": 0.93,
            "expense": {
                "date": "2026-05-20",
                "amount": "12.5",
                "currency": "SGD",
                "category": "餐饮",
                "merchant": "麦当劳",
                "payment_method": None,
                "note": "午饭",
            },
            "update_fields": {},
            "query": None,
            "missing_fields": [],
        }
    )
    parser = IntentParser(llm_client=llm_client)

    result = parser.parse(
        "午饭 12.5 麦当劳",
        context=make_context(),
    )

    assert result.is_success is True
    assert result.intent is ParserIntent.CREATE_EXPENSE
    assert result.expense is not None
    assert result.expense.amount == Decimal("12.5")
    assert result.expense.currency == "SGD"
    assert result.expense.category == "餐饮"
    assert result.expense.merchant == "麦当劳"
    assert result.missing_fields == ()
    assert "DEFAULT_CURRENCY: SGD" in llm_client.calls[0].user_prompt
    assert "TODAY: 2026-05-20" in llm_client.calls[0].user_prompt
    assert "午饭 12.5 麦当劳" in llm_client.calls[0].user_prompt


def test_parser_prompt_guides_haircut_to_personal_care_category():
    llm_client = FakeLLMClient(
        {
            "intent": "create_expense",
            "confidence": 0.9,
            "expense": {
                "date": "2026-05-20",
                "amount": "19",
                "currency": "SGD",
                "category": "个人护理",
                "merchant": None,
                "payment_method": None,
                "note": "剪头发",
            },
            "update_fields": {},
            "query": None,
            "missing_fields": [],
        }
    )
    parser = IntentParser(llm_client=llm_client)

    result = parser.parse("剪头发 19", context=make_context())

    assert "个人护理" in SUPPORTED_CATEGORIES
    assert "剪头发" in llm_client.calls[0].system_prompt
    assert "白鸡饭" in llm_client.calls[0].system_prompt
    assert result.is_success is True
    assert result.expense is not None
    assert result.expense.category == "个人护理"
    assert result.expense.note == "剪头发"


def test_parser_preserves_llm_relative_date_resolution():
    llm_client = FakeLLMClient(
        {
            "intent": "create_expense",
            "confidence": 0.91,
            "expense": {
                "date": "2026-05-19",
                "amount": "8.9",
                "currency": "SGD",
                "category": "餐饮",
                "merchant": "星巴克",
                "payment_method": None,
                "note": None,
            },
            "update_fields": {},
            "query": None,
            "missing_fields": [],
        }
    )
    parser = IntentParser(llm_client=llm_client)

    result = parser.parse("昨天星巴克 8.9", context=make_context())

    assert result.is_success is True
    assert result.expense is not None
    assert result.expense.date == "2026-05-19"
    assert result.expense.category == "餐饮"


def test_parser_reports_missing_amount_without_failing_create_intent():
    llm_client = FakeLLMClient(
        {
            "intent": "create_expense",
            "confidence": 0.82,
            "expense": {
                "date": "2026-05-20",
                "amount": None,
                "currency": "SGD",
                "category": "餐饮",
                "merchant": None,
                "payment_method": None,
                "note": "今天喝咖啡",
            },
            "update_fields": {},
            "query": None,
            "missing_fields": ["amount"],
        }
    )
    parser = IntentParser(llm_client=llm_client)

    result = parser.parse("今天喝咖啡", context=make_context())

    assert result.is_success is True
    assert result.intent is ParserIntent.CREATE_EXPENSE
    assert result.expense is not None
    assert result.expense.amount is None
    assert result.missing_fields == ("amount",)


def test_parser_extracts_update_recent_expense_fields():
    llm_client = FakeLLMClient(
        {
            "intent": "update_recent_expense",
            "confidence": 0.9,
            "expense": None,
            "update_fields": {"category": "交通"},
            "query": None,
            "missing_fields": [],
        }
    )
    parser = IntentParser(llm_client=llm_client)

    result = parser.parse("刚才那笔改成交通", context=make_context())

    assert result.is_success is True
    assert result.intent is ParserIntent.UPDATE_RECENT_EXPENSE
    assert result.update_fields == {"category": "交通"}


def test_parser_backfills_unambiguous_update_amount_from_text_when_llm_omits_it():
    llm_client = FakeLLMClient(
        {
            "intent": "update_recent_expense",
            "confidence": 0.9,
            "expense": None,
            "update_fields": {"category": "餐饮", "note": "白鸡饭"},
            "query": None,
            "missing_fields": [],
        }
    )
    parser = IntentParser(llm_client=llm_client)

    result = parser.parse(
        "改一下，我没有剪头发，也没有去吃福建面，我吃了白鸡饭花了6.8",
        context=make_context(),
    )

    assert result.is_success is True
    assert result.intent is ParserIntent.UPDATE_RECENT_EXPENSE
    assert result.update_fields == {
        "amount": Decimal("6.8"),
        "category": "餐饮",
        "note": "白鸡饭",
    }


def test_parser_does_not_backfill_ambiguous_update_amounts_from_text():
    llm_client = FakeLLMClient(
        {
            "intent": "update_recent_expense",
            "confidence": 0.9,
            "expense": None,
            "update_fields": {"note": "白鸡饭"},
            "query": None,
            "missing_fields": [],
        }
    )
    parser = IntentParser(llm_client=llm_client)

    result = parser.parse("改成 6.8，不是 19", context=make_context())

    assert result.is_success is True
    assert result.update_fields == {"note": "白鸡饭"}


@pytest.mark.parametrize(
    ("text", "update_fields"),
    [
        ("备注改成2号桌", {"note": "2号桌"}),
        ("刚才那笔支付方式改成 Visa 1234", {"payment_method": "Visa 1234"}),
        ("刚才那笔支付方式改成花旗卡1234", {"payment_method": "花旗卡1234"}),
    ],
)
def test_parser_does_not_backfill_unrelated_single_numbers_from_text(
    text: str,
    update_fields: dict[str, object],
):
    llm_client = FakeLLMClient(
        {
            "intent": "update_recent_expense",
            "confidence": 0.9,
            "expense": None,
            "update_fields": update_fields,
            "query": None,
            "missing_fields": [],
        }
    )
    parser = IntentParser(llm_client=llm_client)

    result = parser.parse(text, context=make_context())

    assert result.is_success is True
    assert result.update_fields == update_fields


def test_parser_preserves_update_fields_for_domain_validation():
    llm_client = FakeLLMClient(
        {
            "intent": "update_recent_expense",
            "confidence": 0.9,
            "expense": None,
            "update_fields": {
                "category": "宠物",
                "currency": "USD",
                "type": "income",
                "telegram_user_id": "7",
            },
            "query": None,
            "missing_fields": [],
        }
    )
    parser = IntentParser(llm_client=llm_client)

    result = parser.parse("刚才那笔改一下", context=make_context())

    assert result.is_success is True
    assert result.intent is ParserIntent.UPDATE_RECENT_EXPENSE
    assert result.update_fields == {
        "category": "宠物",
        "currency": "USD",
        "type": "income",
        "telegram_user_id": "7",
    }


def test_parser_allows_update_recent_expense_with_empty_fields_for_validator():
    llm_client = FakeLLMClient(
        {
            "intent": "update_recent_expense",
            "confidence": 0.9,
            "expense": None,
            "update_fields": {},
            "query": None,
            "missing_fields": [],
        }
    )
    parser = IntentParser(llm_client=llm_client)

    result = parser.parse("刚才那笔改一下", context=make_context())

    assert result.is_success is True
    assert result.intent is ParserIntent.UPDATE_RECENT_EXPENSE
    assert result.update_fields == {}


def test_parser_extracts_monthly_total_query():
    llm_client = FakeLLMClient(
        {
            "intent": "query_monthly_total",
            "confidence": 0.86,
            "expense": None,
            "update_fields": {},
            "query": {"month": "2026-05", "currency": "SGD"},
            "missing_fields": [],
        }
    )
    parser = IntentParser(llm_client=llm_client)

    result = parser.parse("这个月花了多少？", context=make_context())

    assert result.is_success is True
    assert result.intent is ParserIntent.QUERY_MONTHLY_TOTAL
    assert result.query is not None
    assert result.query.month == "2026-05"
    assert result.query.currency == "SGD"


def test_parser_classifies_unknown_message():
    llm_client = FakeLLMClient(
        {
            "intent": "unknown",
            "confidence": 0.35,
            "expense": None,
            "update_fields": {},
            "query": None,
            "missing_fields": [],
        }
    )
    parser = IntentParser(llm_client=llm_client)

    result = parser.parse("你好", context=make_context())

    assert result.is_success is True
    assert result.intent is ParserIntent.UNKNOWN
    assert result.expense is None
    assert result.update_fields == {}
    assert result.query is None


@pytest.mark.parametrize(
    "response",
    [
        "not-json",
        '{"intent": "create_expense"}',
        '{"intent":"create_expense","confidence":NaN,"expense":{"date":"2026-05-20","amount":"12.5","currency":"SGD","category":"餐饮","merchant":null,"payment_method":null,"note":null},"update_fields":{},"query":null,"missing_fields":[]}',
        '{"intent":"create_expense","confidence":0.8,"expense":{"date":"2026-05-20","amount":"NaN","currency":"SGD","category":"餐饮","merchant":null,"payment_method":null,"note":null},"update_fields":{},"query":null,"missing_fields":[]}',
        '{"intent":"query_monthly_total","confidence":0.8,"expense":null,"update_fields":{},"query":{"month":"2-00005","currency":"SGD"},"missing_fields":[]}',
    ],
)
def test_parser_returns_controlled_failure_for_malformed_llm_output(response):
    parser = IntentParser(llm_client=FakeLLMClient(response))

    result = parser.parse("午饭 12.5 麦当劳", context=make_context())

    assert result.is_success is False
    assert result.intent is ParserIntent.UNKNOWN
    assert result.error == "malformed_llm_output"


def test_parser_preserves_unsupported_create_category_for_validator():
    parser = IntentParser(
        llm_client=FakeLLMClient(
            {
                "intent": "create_expense",
                "confidence": 0.8,
                "expense": {
                    "date": "2026-05-20",
                    "amount": "12.5",
                    "currency": "SGD",
                    "category": "餐厅",
                    "merchant": "麦当劳",
                    "payment_method": None,
                    "note": None,
                },
                "update_fields": {},
                "query": None,
                "missing_fields": [],
            }
        )
    )

    result = parser.parse("午饭 12.5 麦当劳", context=make_context())

    assert "餐饮" in SUPPORTED_CATEGORIES
    assert result.is_success is True
    assert result.expense is not None
    assert result.expense.category == "餐厅"


def make_context() -> ParserContext:
    return ParserContext(
        today=TODAY,
        timezone="Asia/Singapore",
        default_currency="SGD",
    )


class FakeLLMClient:
    def __init__(self, response: dict[str, object] | str) -> None:
        self._response = response
        self.calls: list[LLMCall] = []

    def complete_json(self, *, system_prompt: str, user_prompt: str) -> str:
        self.calls.append(
            LLMCall(system_prompt=system_prompt, user_prompt=user_prompt)
        )
        if isinstance(self._response, str):
            return self._response

        import json

        return json.dumps(self._response)


class LLMCall:
    def __init__(self, *, system_prompt: str, user_prompt: str) -> None:
        self.system_prompt = system_prompt
        self.user_prompt = user_prompt
