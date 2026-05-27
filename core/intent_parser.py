import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Protocol

from core.categories import CATEGORY_GUIDANCE, SUPPORTED_CATEGORIES
from core.currencies import CURRENCY_PROMPT_GUIDANCE, SUPPORTED_CURRENCIES

REQUIRED_TOP_LEVEL_KEYS = frozenset(
    {
        "intent",
        "confidence",
        "expense",
        "update_fields",
        "query",
        "missing_fields",
    }
)

REQUIRED_EXPENSE_KEYS = frozenset(
    {
        "date",
        "amount",
        "currency",
        "category",
        "merchant",
        "payment_method",
        "note",
    }
)

KNOWN_UPDATE_FIELDS = REQUIRED_EXPENSE_KEYS | {"type"}
_AMOUNT_PATTERN = re.compile(r"(?<![\d:/年月日-])\d+(?:\.\d+)?(?![\d:/年月日-])")
_AMOUNT_CUES = (
    "amount",
    "cost",
    "paid",
    "spent",
    "total",
    "价钱",
    "价格",
    "付款",
    "付了",
    "花费",
    "花了",
    "金额",
)


class ParserIntent(StrEnum):
    CREATE_EXPENSE = "create_expense"
    UPDATE_RECENT_EXPENSE = "update_recent_expense"
    QUERY_MONTHLY_TOTAL = "query_monthly_total"
    UNKNOWN = "unknown"


class IntentParserLLMClient(Protocol):
    def complete_json(self, *, system_prompt: str, user_prompt: str) -> str:
        raise NotImplementedError


@dataclass(frozen=True)
class ParserContext:
    today: date
    timezone: str
    default_currency: str


@dataclass(frozen=True)
class ParsedExpense:
    date: str | None
    amount: Decimal | None
    currency: str | None
    category: str | None
    merchant: str | None
    payment_method: str | None
    note: str | None
    type: str | None = None


@dataclass(frozen=True)
class MonthlyTotalQuery:
    month: str
    currency: str | None


@dataclass(frozen=True)
class IntentParserResult:
    is_success: bool
    intent: ParserIntent
    confidence: float
    expense: ParsedExpense | None
    update_fields: dict[str, object]
    query: MonthlyTotalQuery | None
    missing_fields: tuple[str, ...]
    error: str | None = None

    @classmethod
    def failure(cls, error: str) -> "IntentParserResult":
        return cls(
            is_success=False,
            intent=ParserIntent.UNKNOWN,
            confidence=0.0,
            expense=None,
            update_fields={},
            query=None,
            missing_fields=(),
            error=error,
        )


CATEGORY_GUIDANCE_TEXT = "\n".join(
    f"- {category}: {description}" for category, description in CATEGORY_GUIDANCE
)

SYSTEM_PROMPT = f"""
You are a parser-only component for a Telegram expense tracking backend.
Return JSON only. Do not call tools, write to storage, send messages, or decide
backend side effects.

Supported intents:
- create_expense
- update_recent_expense
- query_monthly_total
- unknown

Supported categories:
{", ".join(SUPPORTED_CATEGORIES)}

Supported currencies:
{", ".join(SUPPORTED_CURRENCIES)}

Category guidance:
{CATEGORY_GUIDANCE_TEXT}

Currency guidance:
{CURRENCY_PROMPT_GUIDANCE}

Return these top-level keys exactly. Use null for non-applicable expense/query
blocks. Do not copy the schema example confidence value; choose confidence
from the rules below:
{{
  "intent": "create_expense | update_recent_expense | query_monthly_total | unknown",
  "confidence": 0.9,
  "expense": null,
  "update_fields": {{}},
  "query": null,
  "missing_fields": []
}}

Confidence rules:
- Use 0.85 to 1.0 when the intent and required fields are clear, including
  fields resolved from TODAY, TIMEZONE, or DEFAULT_CURRENCY.
- Use 0.7 to 0.84 when the intent is likely but one non-required detail is
  uncertain.
- Use below 0.7 only when the user text is ambiguous or unsupported.

For create_expense, expense must be:
{{
    "date": "YYYY-MM-DD or null",
    "amount": "decimal string or null",
    "currency": "currency code or null",
    "category": "supported category or null",
    "merchant": "merchant or null",
    "payment_method": "payment method or null",
    "note": "note or null"
}}

For update_recent_expense, update_fields must contain only fields being changed.
When a descriptive correction clearly changes the category, include both the
descriptive field and category. For example, changing an item to 白鸡饭 should
include category 餐饮 and note 白鸡饭.
For descriptive corrections such as changing the food/item, use note when no
specific merchant or place is named. For food/item corrections, note should be
only the new item name, never the full correction sentence. Keep note concise;
remove correction phrasing such as "改一下", "不是", "没有", or "我吃了" when
the actual item is clear. Only include date when the user explicitly changes
the date; do not copy TODAY into update_fields just because it is present in
context.
For query_monthly_total, query must be:
{{"month": "YYYY-MM", "currency": "currency code or null"}}

Use the provided TODAY, TIMEZONE, and DEFAULT_CURRENCY context to resolve
relative dates and omitted currencies. If a create_expense amount is missing,
leave expense.amount null and include "amount" in missing_fields.
Choose the closest supported category. Use 未分类 only when the category cannot
be inferred from the text.
""".strip()


class IntentParser:
    def __init__(self, *, llm_client: IntentParserLLMClient) -> None:
        self._llm_client = llm_client

    def parse(self, text: str, *, context: ParserContext) -> IntentParserResult:
        try:
            raw_response = self._llm_client.complete_json(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=_build_user_prompt(text, context),
            )
        except Exception:
            return IntentParserResult.failure("llm_provider_error")

        try:
            payload = json.loads(raw_response)
            return _backfill_unambiguous_update_amount(
                text,
                _parse_payload(payload),
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            return IntentParserResult.failure("malformed_llm_output")


def _build_user_prompt(text: str, context: ParserContext) -> str:
    return "\n".join(
        [
            f"TODAY: {context.today.isoformat()}",
            f"TIMEZONE: {context.timezone}",
            f"DEFAULT_CURRENCY: {context.default_currency}",
            "TEXT:",
            text,
        ]
    )


def _parse_payload(payload: object) -> IntentParserResult:
    if not isinstance(payload, Mapping):
        raise ValueError("Parser response must be a JSON object.")

    missing_top_level_keys = REQUIRED_TOP_LEVEL_KEYS - payload.keys()
    if missing_top_level_keys:
        raise ValueError("Parser response is missing required keys.")

    intent = _parse_intent(payload["intent"])
    confidence = _parse_confidence(payload["confidence"])
    missing_fields = _parse_missing_fields(payload["missing_fields"])

    return IntentParserResult(
        is_success=True,
        intent=intent,
        confidence=confidence,
        expense=_parse_expense(payload["expense"], intent),
        update_fields=_parse_update_fields(payload["update_fields"], intent),
        query=_parse_query(payload["query"], intent),
        missing_fields=missing_fields,
    )


def _backfill_unambiguous_update_amount(
    text: str,
    result: IntentParserResult,
) -> IntentParserResult:
    if (
        result.intent is not ParserIntent.UPDATE_RECENT_EXPENSE
        or "amount" in result.update_fields
    ):
        return result

    amount = _unambiguous_amount_from_text(text)
    if amount is None:
        return result

    return replace(
        result,
        update_fields={
            "amount": amount,
            **result.update_fields,
        },
    )


def _unambiguous_amount_from_text(text: str) -> Decimal | None:
    matches = list(_AMOUNT_PATTERN.finditer(text))
    if len(matches) != 1:
        return None

    match = matches[0]
    candidate = match.group()
    if not _has_amount_cue(text, match.start(), match.end()):
        return None

    if "." not in candidate and len(candidate) > 5:
        return None

    try:
        amount = Decimal(candidate)
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount <= 0:
        return None
    return amount


def _has_amount_cue(text: str, start: int, end: int) -> bool:
    window = text[max(0, start - 12) : min(len(text), end + 6)].lower()
    return any(cue in window for cue in _AMOUNT_CUES)


def _parse_intent(value: object) -> ParserIntent:
    try:
        return ParserIntent(value)
    except ValueError:
        raise ValueError("Unsupported parser intent.") from None


def _parse_confidence(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("confidence must be numeric.")

    confidence = float(value)
    if not math.isfinite(confidence) or confidence < 0 or confidence > 1:
        raise ValueError("confidence must be between 0 and 1.")
    return confidence


def _parse_missing_fields(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(field, str) for field in value
    ):
        raise ValueError("missing_fields must be a list of strings.")
    return tuple(value)


def _parse_expense(value: object, intent: ParserIntent) -> ParsedExpense | None:
    if value is None:
        if intent is ParserIntent.CREATE_EXPENSE:
            raise ValueError("create_expense requires expense fields.")
        return None

    if not isinstance(value, Mapping):
        raise ValueError("expense must be an object or null.")

    missing_expense_keys = REQUIRED_EXPENSE_KEYS - value.keys()
    if missing_expense_keys:
        raise ValueError("expense is missing required keys.")

    category = _parse_optional_string(value["category"], "expense.category")

    return ParsedExpense(
        date=_parse_optional_string(value["date"], "expense.date"),
        amount=_parse_optional_decimal(value["amount"], "expense.amount"),
        currency=_parse_optional_string(value["currency"], "expense.currency"),
        category=category,
        merchant=_parse_optional_string(value["merchant"], "expense.merchant"),
        payment_method=_parse_optional_string(
            value["payment_method"],
            "expense.payment_method",
        ),
        note=_parse_optional_string(value["note"], "expense.note"),
        type=_parse_optional_string(value.get("type"), "expense.type"),
    )


def _parse_update_fields(
    value: object,
    intent: ParserIntent,
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("update_fields must be an object.")

    parsed_fields: dict[str, object] = {}
    for field_name, field_value in value.items():
        if not isinstance(field_name, str):
            raise ValueError("update_fields keys must be strings.")

        if field_name == "amount":
            parsed_value = _parse_optional_decimal(field_value, "update_fields.amount")
        elif field_name in KNOWN_UPDATE_FIELDS:
            parsed_value = _parse_optional_string(
                field_value,
                f"update_fields.{field_name}",
            )
        else:
            parsed_fields[field_name] = field_value
            continue

        if parsed_value is not None:
            parsed_fields[field_name] = parsed_value

    return parsed_fields


def _parse_query(value: object, intent: ParserIntent) -> MonthlyTotalQuery | None:
    if value is None:
        if intent is ParserIntent.QUERY_MONTHLY_TOTAL:
            raise ValueError("query_monthly_total requires query fields.")
        return None

    if not isinstance(value, Mapping):
        raise ValueError("query must be an object or null.")

    month = value.get("month")
    if not isinstance(month, str) or len(month) != 7:
        raise ValueError("query.month must be YYYY-MM.")
    _validate_month(month)

    return MonthlyTotalQuery(
        month=month,
        currency=_parse_optional_string(value.get("currency"), "query.currency"),
    )


def _parse_optional_string(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string or null.")
    return value


def _parse_optional_decimal(value: object, field_name: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        raise ValueError(f"{field_name} must be a decimal string or null.")
    try:
        decimal_value = Decimal(str(value))
    except InvalidOperation:
        raise ValueError(f"{field_name} must be a decimal string or null.") from None
    if not decimal_value.is_finite():
        raise ValueError(f"{field_name} must be a finite decimal string or null.")
    return decimal_value


def _validate_month(value: str) -> None:
    if (
        len(value) != 7
        or value[4] != "-"
        or not value[:4].isdecimal()
        or not value[5:].isdecimal()
        or int(value[5:]) < 1
        or int(value[5:]) > 12
    ):
        raise ValueError("query.month must be YYYY-MM.")
