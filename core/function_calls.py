"""Provider-neutral contract for one-shot application function batches.

The LLM may propose only allowlisted application functions. Proposals remain
untrusted until a backend executor validates their arguments and authorization.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from core.categories import SUPPORTED_CATEGORIES
from core.currencies import SUPPORTED_CURRENCIES


class ApplicationFunction(StrEnum):
    RECORD_EXPENSE = "record_expense"
    UPDATE_EXPENSE = "update_expense"
    GET_SPENDING_SUMMARY = "get_spending_summary"
    COMPARE_SPENDING_PERIODS = "compare_spending_periods"
    GET_TOP_EXPENSES = "get_top_expenses"
    LIST_RECENT_EXPENSES = "list_recent_expenses"
    REQUEST_CLARIFICATION = "request_clarification"
    REJECT_UNSUPPORTED_REQUEST = "reject_unsupported_request"


@dataclass(frozen=True)
class FunctionCallProposal:
    function: ApplicationFunction
    arguments: Mapping[str, object]


@dataclass(frozen=True)
class FunctionCallBatch:
    calls: tuple[FunctionCallProposal, ...]

    def __post_init__(self) -> None:
        if not self.calls:
            raise ValueError("function call batch requires at least one call")


def _nullable_string(**extra: object) -> dict[str, object]:
    return {"type": ["string", "null"], **extra}


def _closed_object(
    properties: Mapping[str, object],
) -> dict[str, object]:
    return {
        "type": "object",
        "properties": dict(properties),
        "required": list(properties),
        "additionalProperties": False,
    }


def _period_schema() -> dict[str, object]:
    return _closed_object(
        {
            "kind": {
                "type": "string",
                "enum": [
                    "today",
                    "yesterday",
                    "this_week",
                    "last_week",
                    "this_month",
                    "last_month",
                    "custom",
                ],
            },
            "start_date": _nullable_string(
                description="Required only for a custom inclusive date range."
            ),
            "end_date": _nullable_string(
                description="Required only for a custom inclusive date range."
            ),
        }
    )


def _filters() -> dict[str, object]:
    return {
        "category": _nullable_string(enum=[*SUPPORTED_CATEGORIES, None]),
        "merchant": _nullable_string(),
    }


def _tool(
    function: ApplicationFunction,
    description: str,
    properties: Mapping[str, object],
) -> dict[str, object]:
    return {
        "type": "function",
        "name": function.value,
        "description": description,
        "strict": True,
        "parameters": _closed_object(properties),
    }


_EXPENSE_FIELDS: dict[str, object] = {
    "date": _nullable_string(
        description="Explicit YYYY-MM-DD date, or null for the backend default."
    ),
    "amount": _nullable_string(
        description="Positive decimal string explicitly stated by the user."
    ),
    "currency": _nullable_string(enum=[*SUPPORTED_CURRENCIES, None]),
    "category": _nullable_string(enum=[*SUPPORTED_CATEGORIES, None]),
    "merchant": _nullable_string(),
    "payment_method": _nullable_string(),
    "note": _nullable_string(),
}


APPLICATION_FUNCTION_TOOLS: tuple[dict[str, object], ...] = (
    _tool(
        ApplicationFunction.RECORD_EXPENSE,
        "Propose one expense. Call once per expense in a multi-expense message.",
        _EXPENSE_FIELDS,
    ),
    _tool(
        ApplicationFunction.UPDATE_EXPENSE,
        "Propose changes to the user's latest expense; never delete an expense.",
        {
            "target": {"type": "string", "enum": ["latest"]},
            "changes": _closed_object(_EXPENSE_FIELDS),
        },
    ),
    _tool(
        ApplicationFunction.GET_SPENDING_SUMMARY,
        "Summarize deterministic spending totals and category breakdowns.",
        {"period": _period_schema(), **_filters()},
    ),
    _tool(
        ApplicationFunction.COMPARE_SPENDING_PERIODS,
        "Compare deterministic spending totals across two explicit periods.",
        {
            "current_period": _period_schema(),
            "comparison_period": _period_schema(),
            **_filters(),
        },
    ),
    _tool(
        ApplicationFunction.GET_TOP_EXPENSES,
        "List the largest expenses in a bounded period.",
        {
            "period": _period_schema(),
            **_filters(),
            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
        },
    ),
    _tool(
        ApplicationFunction.LIST_RECENT_EXPENSES,
        "List the user's most recent matching expenses.",
        {
            **_filters(),
            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
        },
    ),
    _tool(
        ApplicationFunction.REQUEST_CLARIFICATION,
        "Request specific missing or ambiguous information without ledger access.",
        {
            "reason_code": {
                "type": "string",
                "enum": ["missing_fields", "ambiguous_target", "ambiguous_request"],
            },
            "missing_fields": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": list(_EXPENSE_FIELDS),
                },
            },
        },
    ),
    _tool(
        ApplicationFunction.REJECT_UNSUPPORTED_REQUEST,
        "Reject a capability outside the product boundary without ledger access.",
        {
            "capability_code": {
                "type": "string",
                "enum": [
                    "delete",
                    "bulk_destructive_mutation",
                    "financial_advice",
                    "unsupported",
                ],
            }
        },
    ),
)
