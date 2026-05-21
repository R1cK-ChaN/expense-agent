from datetime import datetime, timezone
from decimal import Decimal

import pytest

from core.intent_parser import IntentParserResult, ParsedExpense, ParserIntent
from core.validator import (
    DEFAULT_EXPENSE_CATEGORY,
    MISSING_AMOUNT_MESSAGE,
    MULTIPLE_EXPENSES_MESSAGE,
    UNSUPPORTED_TYPE_MESSAGE,
    ValidationContext,
    ValidationErrorCode,
    validate_create_expense,
)


def test_validation_rejects_missing_amount_without_normalized_expense():
    result = validate_create_expense(
        make_parser_result(amount=None),
        context=make_context(),
    )

    assert result.is_valid is False
    assert result.expense is None
    assert result.user_message == MISSING_AMOUNT_MESSAGE
    assert result.errors[0].code is ValidationErrorCode.MISSING_AMOUNT


@pytest.mark.parametrize("amount", [Decimal("0"), Decimal("-0.01")])
def test_validation_rejects_non_positive_amounts(amount):
    result = validate_create_expense(
        make_parser_result(amount=amount),
        context=make_context(),
    )

    assert result.is_valid is False
    assert result.expense is None
    assert result.errors[0].code is ValidationErrorCode.INVALID_AMOUNT


def test_validation_defaults_missing_date_using_configured_timezone_today():
    result = validate_create_expense(
        make_parser_result(date=None),
        context=make_context(
            now=datetime(2026, 5, 19, 16, 30, tzinfo=timezone.utc),
            timezone_name="Asia/Singapore",
        ),
    )

    assert result.is_valid is True
    assert result.expense is not None
    assert result.expense.date == "2026-05-20"


def test_validation_defaults_missing_currency_to_sgd():
    result = validate_create_expense(
        make_parser_result(currency=None),
        context=make_context(default_currency="SGD"),
    )

    assert result.is_valid is True
    assert result.expense is not None
    assert result.expense.currency == "SGD"


@pytest.mark.parametrize("category", [None, "餐厅"])
def test_validation_defaults_missing_or_unsupported_category(category):
    result = validate_create_expense(
        make_parser_result(category=category),
        context=make_context(),
    )

    assert result.is_valid is True
    assert result.expense is not None
    assert result.expense.category == DEFAULT_EXPENSE_CATEGORY


def test_validation_rejects_non_expense_transaction_type_for_mvp():
    result = validate_create_expense(
        make_parser_result(transaction_type="income"),
        context=make_context(),
    )

    assert result.is_valid is False
    assert result.expense is None
    assert result.user_message == UNSUPPORTED_TYPE_MESSAGE
    assert result.errors[0].code is ValidationErrorCode.UNSUPPORTED_TYPE


def test_validation_rejects_messages_that_appear_to_contain_multiple_expenses():
    result = validate_create_expense(
        make_parser_result(),
        context=make_context(),
        source_text="午饭 12\n咖啡 5",
    )

    assert result.is_valid is False
    assert result.expense is None
    assert result.user_message == MULTIPLE_EXPENSES_MESSAGE
    assert result.errors[0].code is ValidationErrorCode.MULTIPLE_EXPENSES


@pytest.mark.parametrize(
    "source_text",
    [
        "午饭 12，咖啡 5",
        "lunch 12 coffee 5",
    ],
)
def test_validation_rejects_inline_multiple_expense_amounts(source_text):
    result = validate_create_expense(
        make_parser_result(),
        context=make_context(),
        source_text=source_text,
    )

    assert result.is_valid is False
    assert result.expense is None
    assert result.errors[0].code is ValidationErrorCode.MULTIPLE_EXPENSES


@pytest.mark.parametrize(
    "source_text",
    [
        "2026-05-20\n午饭 12",
        "5月20日 午饭 12",
        "2026年5月20日 午饭 12",
    ],
)
def test_validation_does_not_treat_date_and_single_amount_as_multiple_expenses(
    source_text,
):
    result = validate_create_expense(
        make_parser_result(),
        context=make_context(),
        source_text=source_text,
    )

    assert result.is_valid is True
    assert result.expense is not None


def test_validation_normalizes_safe_create_expense_fields():
    result = validate_create_expense(
        make_parser_result(
            currency="sgd",
            category="餐饮",
            merchant="麦当劳",
            payment_method="card",
            note="午饭",
        ),
        context=make_context(),
    )

    assert result.is_valid is True
    assert result.errors == ()
    assert result.expense is not None
    assert result.expense.amount == Decimal("12.30")
    assert result.expense.currency == "SGD"
    assert result.expense.type == "expense"
    assert result.expense.category == "餐饮"
    assert result.expense.merchant == "麦当劳"
    assert result.expense.payment_method == "card"
    assert result.expense.note == "午饭"


def make_context(
    *,
    now: datetime = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
    timezone_name: str = "Asia/Singapore",
    default_currency: str = "SGD",
) -> ValidationContext:
    return ValidationContext(
        timezone=timezone_name,
        default_currency=default_currency,
        now=now,
    )


def make_parser_result(
    *,
    amount: Decimal | None = Decimal("12.30"),
    date: str | None = "2026-05-20",
    currency: str | None = "SGD",
    category: str | None = "餐饮",
    merchant: str | None = None,
    payment_method: str | None = None,
    note: str | None = None,
    transaction_type: str | None = "expense",
) -> IntentParserResult:
    return IntentParserResult(
        is_success=True,
        intent=ParserIntent.CREATE_EXPENSE,
        confidence=0.9,
        expense=ParsedExpense(
            date=date,
            amount=amount,
            currency=currency,
            category=category,
            merchant=merchant,
            payment_method=payment_method,
            note=note,
            type=transaction_type,
        ),
        update_fields={},
        query=None,
        missing_fields=(),
    )
