import pytest

from core.function_calls import ApplicationFunction
from integrations.llm_client import (
    LLMClientError,
    OpenAICompatibleLLMClient,
    OpenAIResponsesFunctionClient,
)


STRICT_TEST_TOOL = {
    "type": "function",
    "name": "record_expense",
    "description": "Record one validated expense.",
    "strict": True,
    "parameters": {
        "type": "object",
        "properties": {
            "amount": {"type": "string"},
        },
        "required": ["amount"],
        "additionalProperties": False,
    },
}


def test_openai_compatible_client_requests_json_chat_completion():
    transport = FakeJsonTransport(
        {
            "choices": [
                {
                    "message": {
                        "content": '{"intent": "unknown"}',
                    }
                }
            ]
        }
    )
    client = OpenAICompatibleLLMClient(
        api_key="parser-secret",
        model="parser-model",
        transport=transport,
    )

    response = client.complete_json(
        system_prompt="system instructions",
        user_prompt="user message",
    )

    assert response == '{"intent": "unknown"}'
    assert transport.requests == [
        (
            "https://api.openai.com/v1/chat/completions",
            {
                "Authorization": "Bearer parser-secret",
                "Content-Type": "application/json",
            },
            {
                "model": "parser-model",
                "messages": [
                    {"role": "system", "content": "system instructions"},
                    {"role": "user", "content": "user message"},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0,
            },
        )
    ]


def test_openai_compatible_client_uses_minimal_reasoning_for_gpt_5_nano():
    transport = FakeJsonTransport(
        {
            "choices": [
                {
                    "message": {
                        "content": '{"intent": "unknown"}',
                    }
                }
            ]
        }
    )
    client = OpenAICompatibleLLMClient(
        api_key="parser-secret",
        model="gpt-5-nano",
        transport=transport,
    )

    response = client.complete_json(
        system_prompt="system instructions",
        user_prompt="user message",
    )

    assert response == '{"intent": "unknown"}'
    payload = transport.requests[0][2]
    assert "temperature" not in payload
    assert payload["reasoning_effort"] == "minimal"


@pytest.mark.parametrize(
    "model",
    [
        "gpt-5.5-2026-04-23",
        "o4-mini",
        "chat-latest",
    ],
)
def test_openai_compatible_client_omits_temperature_for_default_temperature_models(
    model: str,
):
    transport = FakeJsonTransport(
        {
            "choices": [
                {
                    "message": {
                        "content": '{"intent": "unknown"}',
                    }
                }
            ]
        }
    )
    client = OpenAICompatibleLLMClient(
        api_key="parser-secret",
        model=model,
        transport=transport,
    )

    response = client.complete_json(
        system_prompt="system instructions",
        user_prompt="user message",
    )

    assert response == '{"intent": "unknown"}'
    payload = transport.requests[0][2]
    assert "temperature" not in payload
    assert "reasoning_effort" not in payload


def test_openai_compatible_client_maps_bad_provider_response_to_error():
    client = OpenAICompatibleLLMClient(
        api_key="parser-secret",
        model="parser-model",
        transport=FakeJsonTransport({"choices": []}),
    )

    with pytest.raises(LLMClientError, match="LLM provider returned invalid JSON"):
        client.complete_json(
            system_prompt="system instructions",
            user_prompt="user message",
        )


def test_openai_compatible_client_requires_api_key_and_model():
    with pytest.raises(ValueError, match="api_key"):
        OpenAICompatibleLLMClient(api_key="", model="parser-model")

    with pytest.raises(ValueError, match="model"):
        OpenAICompatibleLLMClient(api_key="parser-secret", model="")


def test_responses_client_requests_one_strict_function_batch_with_low_reasoning():
    transport = FakeJsonTransport(
        {
            "status": "completed",
            "output": [
                {
                    "type": "reasoning",
                    "id": "reasoning-1",
                },
                {
                    "type": "function_call",
                    "call_id": "provider-call-1",
                    "name": "record_expense",
                    "arguments": '{"amount":"20"}',
                    "status": "completed",
                },
                {
                    "type": "function_call",
                    "call_id": "provider-call-2",
                    "name": "get_spending_summary",
                    "arguments": '{"period":"today"}',
                    "status": "completed",
                },
            ]
        }
    )
    client = OpenAIResponsesFunctionClient(
        api_key="parser-secret",
        model="gpt-5.5-2026-04-23",
        transport=transport,
    )

    batch = client.select_functions(
        system_prompt="Select backend functions only.",
        user_prompt="午饭 20，然后告诉我今天花了多少",
        tools=[
            STRICT_TEST_TOOL,
            {
                **STRICT_TEST_TOOL,
                "name": "get_spending_summary",
            },
        ],
    )

    assert [call.function for call in batch.calls] == [
        ApplicationFunction.RECORD_EXPENSE,
        ApplicationFunction.GET_SPENDING_SUMMARY,
    ]
    assert [call.arguments for call in batch.calls] == [
        {"amount": "20"},
        {"period": "today"},
    ]
    assert transport.requests == [
        (
            "https://api.openai.com/v1/responses",
            {
                "Authorization": "Bearer parser-secret",
                "Content-Type": "application/json",
            },
            {
                "model": "gpt-5.5-2026-04-23",
                "instructions": "Select backend functions only.",
                "input": "午饭 20，然后告诉我今天花了多少",
                "tools": [
                    STRICT_TEST_TOOL,
                    {
                        **STRICT_TEST_TOOL,
                        "name": "get_spending_summary",
                    },
                ],
                "tool_choice": "required",
                "parallel_tool_calls": True,
                "reasoning": {"effort": "low"},
            },
        )
    ]


@pytest.mark.parametrize(
    ("response", "error_message"),
    [
        ({"status": "completed", "output": []}, "non-empty function batch"),
        (
            {
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "done"}],
                    },
                    {
                        "type": "function_call",
                        "name": "record_expense",
                        "arguments": '{"amount":"20"}',
                        "status": "completed",
                    },
                ]
            },
            "must not return assistant text",
        ),
        (
            {
                "status": "completed",
                "output": [
                    {
                        "type": "function_call",
                        "name": "delete_all_expenses",
                        "arguments": "{}",
                        "status": "completed",
                    }
                ]
            },
            "unknown function",
        ),
        (
            {
                "status": "completed",
                "output": [
                    {
                        "type": "function_call",
                        "name": "record_expense",
                        "arguments": "not-json",
                        "status": "completed",
                    }
                ]
            },
            "invalid function arguments",
        ),
        (
            {
                "status": "incomplete",
                "output": [
                    {
                        "type": "function_call",
                        "name": "record_expense",
                        "arguments": '{"amount":"20"}',
                        "status": "completed",
                    }
                ],
            },
            "response is not completed",
        ),
        (
            {
                "status": "completed",
                "output": [
                    {
                        "type": "function_call",
                        "name": "record_expense",
                        "arguments": '{"amount":"20"}',
                        "status": "in_progress",
                    }
                ],
            },
            "function call is not completed",
        ),
    ],
)
def test_responses_client_rejects_unsafe_provider_output(
    response: dict[str, object],
    error_message: str,
):
    client = OpenAIResponsesFunctionClient(
        api_key="parser-secret",
        model="gpt-5.5-2026-04-23",
        transport=FakeJsonTransport(response),
    )

    with pytest.raises(LLMClientError, match=error_message):
        client.select_functions(
            system_prompt="Select backend functions only.",
            user_prompt="message",
            tools=[STRICT_TEST_TOOL],
        )


def test_responses_client_requires_strict_function_schemas():
    client = OpenAIResponsesFunctionClient(
        api_key="parser-secret",
        model="gpt-5.5-2026-04-23",
        transport=FakeJsonTransport({"status": "completed", "output": []}),
    )

    with pytest.raises(ValueError, match="strict function schema"):
        client.select_functions(
            system_prompt="Select backend functions only.",
            user_prompt="message",
            tools=[{**STRICT_TEST_TOOL, "strict": False}],
        )


class FakeJsonTransport:
    def __init__(self, response: dict[str, object]) -> None:
        self._response = response
        self.requests: list[
            tuple[str, dict[str, str], dict[str, object]]
        ] = []

    def post_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, object],
    ) -> dict[str, object]:
        self.requests.append((url, dict(headers), dict(payload)))
        return self._response
