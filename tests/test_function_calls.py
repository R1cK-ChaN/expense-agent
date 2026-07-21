import pytest

from core.function_calls import (
    ApplicationFunction,
    FunctionCallBatch,
    FunctionCallProposal,
)


def test_function_call_batch_preserves_ordered_allowlisted_proposals():
    batch = FunctionCallBatch(
        calls=(
            FunctionCallProposal(
                function=ApplicationFunction.RECORD_EXPENSE,
                arguments={"amount": "20"},
            ),
            FunctionCallProposal(
                function=ApplicationFunction.GET_SPENDING_SUMMARY,
                arguments={"period": "today"},
            ),
        )
    )

    assert tuple(call.function.value for call in batch.calls) == (
        "record_expense",
        "get_spending_summary",
    )


def test_function_call_batch_rejects_empty_batch():
    with pytest.raises(ValueError, match="at least one"):
        FunctionCallBatch(calls=())
