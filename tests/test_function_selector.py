from datetime import date

from core.function_calls import (
    APPLICATION_FUNCTION_TOOLS,
    ApplicationFunction,
    FunctionCallBatch,
    FunctionCallProposal,
)
from core.function_selector import FunctionSelectionContext, FunctionSelector


def test_function_catalog_exposes_only_approved_strict_functions():
    assert {tool["name"] for tool in APPLICATION_FUNCTION_TOOLS} == {
        function.value for function in ApplicationFunction
    }
    for tool in APPLICATION_FUNCTION_TOOLS:
        assert tool["type"] == "function"
        assert tool["strict"] is True
        _assert_closed_object_schemas(tool["parameters"])


def test_function_selector_supplies_runtime_context_and_complete_catalog():
    expected = FunctionCallBatch(
        calls=(
            FunctionCallProposal(
                function=ApplicationFunction.GET_SPENDING_SUMMARY,
                arguments={
                    "period": {
                        "kind": "this_month",
                        "start_date": None,
                        "end_date": None,
                    },
                    "category": None,
                    "merchant": None,
                },
            ),
        )
    )
    client = FakeFunctionClient(expected)
    selector = FunctionSelector(llm_client=client)

    result = selector.select(
        "这个月花了多少？",
        context=FunctionSelectionContext(
            today=date(2026, 7, 21),
            timezone="Asia/Singapore",
            default_currency="SGD",
        ),
    )

    assert result == expected
    call = client.calls[0]
    assert call["tools"] == APPLICATION_FUNCTION_TOOLS
    assert "Never produce final user-visible text" in call["system_prompt"]
    assert "one complete function-call batch" in call["system_prompt"]
    assert "TODAY: 2026-07-21" in call["user_prompt"]
    assert "TIMEZONE: Asia/Singapore" in call["user_prompt"]
    assert "DEFAULT_CURRENCY: SGD" in call["user_prompt"]
    assert "这个月花了多少？" in call["user_prompt"]


def _assert_closed_object_schemas(schema: object) -> None:
    if not isinstance(schema, dict):
        return
    if schema.get("type") == "object":
        assert schema.get("additionalProperties") is False
        properties = schema.get("properties", {})
        assert set(schema.get("required", ())) == set(properties)
    for value in schema.values():
        if isinstance(value, dict):
            _assert_closed_object_schemas(value)
        elif isinstance(value, list):
            for item in value:
                _assert_closed_object_schemas(item)


class FakeFunctionClient:
    def __init__(self, result: FunctionCallBatch) -> None:
        self._result = result
        self.calls: list[dict[str, object]] = []

    def select_functions(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        tools: object,
    ) -> FunctionCallBatch:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "tools": tools,
            }
        )
        return self._result
