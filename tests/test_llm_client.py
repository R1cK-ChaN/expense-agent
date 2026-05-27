import pytest

from integrations.llm_client import LLMClientError, OpenAICompatibleLLMClient


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
