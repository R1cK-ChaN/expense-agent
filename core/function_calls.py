"""Provider-neutral contract for one-shot application function batches.

The LLM may propose only allowlisted application functions. Proposals remain
untrusted until a backend executor validates their arguments and authorization.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum


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
