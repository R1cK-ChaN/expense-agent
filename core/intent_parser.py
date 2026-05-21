import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Protocol

from core.categories import SUPPORTED_CATEGORIES

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

Return these top-level keys exactly. Use null for non-applicable expense/query
blocks:
{{
  "intent": "create_expense | update_recent_expense | query_monthly_total | unknown",
  "confidence": 0.0,
  "expense": null,
  "update_fields": {{}},
  "query": null,
  "missing_fields": []
}}

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
For query_monthly_total, query must be:
{{"month": "YYYY-MM", "currency": "currency code or null"}}

Use the provided TODAY, TIMEZONE, and DEFAULT_CURRENCY context to resolve
relative dates and omitted currencies. If a create_expense amount is missing,
leave expense.amount null and include "amount" in missing_fields.
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
            return _parse_payload(payload)
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
