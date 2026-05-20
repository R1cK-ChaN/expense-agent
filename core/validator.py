import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from zoneinfo import ZoneInfo

from core.categories import DEFAULT_EXPENSE_CATEGORY, SUPPORTED_CATEGORY_SET
from core.intent_parser import IntentParserResult, ParserIntent


DEFAULT_CURRENCY = "SGD"
EXPENSE_TYPE = "expense"

MISSING_AMOUNT_MESSAGE = "这笔支出还缺金额，请补充一下。"
INVALID_AMOUNT_MESSAGE = "金额需要大于 0，请重新发送。"
INVALID_DATE_MESSAGE = "日期格式不正确，请重新发送。"
INVALID_CURRENCY_MESSAGE = "货币代码需要是 3 位字母，请重新发送。"
UNSUPPORTED_TYPE_MESSAGE = "目前只支持记录支出。"
MULTIPLE_EXPENSES_MESSAGE = "目前一条消息只能记录一笔支出，请分开发送。"
PARSER_FAILURE_MESSAGE = "这条消息暂时无法识别，请重新发送。"

_CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")
_AMOUNT_PATTERN = re.compile(r"(?<![\d:/-])\d+(?:\.\d+)?(?![\d:/-])")
_DATE_OR_TIME_PATTERN = re.compile(
    r"(?<!\d)(?:"
    r"\d{4}[-/]\d{1,2}[-/]\d{1,2}"
    r"|\d{4}年\d{1,2}月\d{1,2}日?"
    r"|\d{1,2}月\d{1,2}日?"
    r"|\d{8}"
    r"|\d{1,2}[-/]\d{1,2}(?:[-/]\d{2,4})?"
    r"|\d{1,2}:\d{2}"
    r")(?!\d)"
)


class ValidationErrorCode(StrEnum):
    MISSING_AMOUNT = "missing_amount"
    INVALID_AMOUNT = "invalid_amount"
    INVALID_DATE = "invalid_date"
    INVALID_CURRENCY = "invalid_currency"
    UNSUPPORTED_TYPE = "unsupported_type"
    MULTIPLE_EXPENSES = "multiple_expenses"
    PARSER_FAILURE = "parser_failure"


@dataclass(frozen=True)
class ValidationContext:
    timezone: str
    default_currency: str = DEFAULT_CURRENCY
    now: datetime | None = None


@dataclass(frozen=True)
class ValidationError:
    code: ValidationErrorCode
    message: str


@dataclass(frozen=True)
class ValidatedExpense:
    date: str
    amount: Decimal
    currency: str
    type: str
    category: str
    merchant: str | None
    payment_method: str | None
    note: str | None


@dataclass(frozen=True)
class ValidationResult:
    is_valid: bool
    expense: ValidatedExpense | None
    errors: tuple[ValidationError, ...]

    @property
    def user_message(self) -> str | None:
        if not self.errors:
            return None
        return self.errors[0].message


def validate_create_expense(
    parser_result: IntentParserResult,
    *,
    context: ValidationContext,
    source_text: str | None = None,
) -> ValidationResult:
    if not parser_result.is_success:
        return _invalid(ValidationErrorCode.PARSER_FAILURE, PARSER_FAILURE_MESSAGE)

    if (
        parser_result.intent is not ParserIntent.CREATE_EXPENSE
        or parser_result.expense is None
    ):
        return _invalid(ValidationErrorCode.UNSUPPORTED_TYPE, UNSUPPORTED_TYPE_MESSAGE)

    if _appears_to_contain_multiple_expenses(source_text):
        return _invalid(
            ValidationErrorCode.MULTIPLE_EXPENSES,
            MULTIPLE_EXPENSES_MESSAGE,
        )

    parsed_expense = parser_result.expense
    transaction_type = _normalize_type(parsed_expense.type)
    if transaction_type != EXPENSE_TYPE:
        return _invalid(ValidationErrorCode.UNSUPPORTED_TYPE, UNSUPPORTED_TYPE_MESSAGE)

    if "amount" in parser_result.missing_fields or parsed_expense.amount is None:
        return _invalid(ValidationErrorCode.MISSING_AMOUNT, MISSING_AMOUNT_MESSAGE)

    amount = parsed_expense.amount
    if not amount.is_finite() or amount <= Decimal("0"):
        return _invalid(ValidationErrorCode.INVALID_AMOUNT, INVALID_AMOUNT_MESSAGE)

    date_value = _normalize_date(parsed_expense.date, context)
    if date_value is None:
        return _invalid(ValidationErrorCode.INVALID_DATE, INVALID_DATE_MESSAGE)

    currency_value = _normalize_currency(parsed_expense.currency, context)
    if currency_value is None:
        return _invalid(ValidationErrorCode.INVALID_CURRENCY, INVALID_CURRENCY_MESSAGE)

    return ValidationResult(
        is_valid=True,
        expense=ValidatedExpense(
            date=date_value,
            amount=amount,
            currency=currency_value,
            type=EXPENSE_TYPE,
            category=_normalize_category(parsed_expense.category),
            merchant=_normalize_optional_text(parsed_expense.merchant),
            payment_method=_normalize_optional_text(parsed_expense.payment_method),
            note=_normalize_optional_text(parsed_expense.note),
        ),
        errors=(),
    )


def _invalid(code: ValidationErrorCode, message: str) -> ValidationResult:
    return ValidationResult(
        is_valid=False,
        expense=None,
        errors=(ValidationError(code=code, message=message),),
    )


def _normalize_type(value: str | None) -> str:
    if value is None or value.strip() == "":
        return EXPENSE_TYPE
    return value.strip().lower()


def _normalize_date(value: str | None, context: ValidationContext) -> str | None:
    if value is None or value.strip() == "":
        return _today(context).isoformat()

    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError:
        return None


def _today(context: ValidationContext) -> date:
    timezone = ZoneInfo(context.timezone)
    now = context.now
    if now is None:
        return datetime.now(timezone).date()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone)
    return now.astimezone(timezone).date()


def _normalize_currency(
    value: str | None,
    context: ValidationContext,
) -> str | None:
    currency = value if value is not None and value.strip() else context.default_currency
    currency = currency.strip().upper()
    if not _CURRENCY_PATTERN.fullmatch(currency):
        return None
    return currency


def _normalize_category(value: str | None) -> str:
    category = _normalize_optional_text(value)
    if category is None or category not in SUPPORTED_CATEGORY_SET:
        return DEFAULT_EXPENSE_CATEGORY
    return category


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _appears_to_contain_multiple_expenses(source_text: str | None) -> bool:
    if source_text is None:
        return False

    candidate_text = _DATE_OR_TIME_PATTERN.sub(" ", source_text)
    return len(_AMOUNT_PATTERN.findall(candidate_text)) > 1
