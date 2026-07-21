import json
from collections.abc import Mapping, Sequence
from typing import Protocol
from urllib import error, request

from core.function_calls import (
    ApplicationFunction,
    FunctionCallBatch,
    FunctionCallProposal,
)


OPENAI_COMPATIBLE_API_BASE_URL = "https://api.openai.com/v1"
MINIMAL_REASONING_EFFORT = "minimal"
LOW_REASONING_EFFORT = "low"


class LLMClientError(Exception):
    """Raised when the LLM provider client cannot complete a request."""


class JsonTransport(Protocol):
    def post_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, object],
    ) -> Mapping[str, object]:
        raise NotImplementedError


class OpenAICompatibleLLMClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        transport: JsonTransport | None = None,
        api_base_url: str = OPENAI_COMPATIBLE_API_BASE_URL,
    ) -> None:
        if not api_key:
            raise ValueError("api_key must be configured")
        if not model:
            raise ValueError("model must be configured")

        self._api_key = api_key
        self._model = model
        self._transport = transport or UrllibJsonTransport()
        self._api_base_url = api_base_url.rstrip("/")

    def complete_json(self, *, system_prompt: str, user_prompt: str) -> str:
        payload: dict[str, object] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
        }
        if _uses_minimal_reasoning_effort(self._model):
            payload["reasoning_effort"] = MINIMAL_REASONING_EFFORT
        if not _requires_default_temperature(self._model):
            payload["temperature"] = 0

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        try:
            response = self._transport.post_json(
                f"{self._api_base_url}/chat/completions",
                headers=headers,
                payload=payload,
            )
        except Exception:
            raise LLMClientError("LLM provider request failed.") from None

        return _extract_message_content(response)


class OpenAIResponsesFunctionClient:
    """Select one complete allowlisted function batch through Responses API."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        transport: JsonTransport | None = None,
        api_base_url: str = OPENAI_COMPATIBLE_API_BASE_URL,
    ) -> None:
        if not api_key:
            raise ValueError("api_key must be configured")
        if not model:
            raise ValueError("model must be configured")

        self._api_key = api_key
        self._model = model
        self._transport = transport or UrllibJsonTransport()
        self._api_base_url = api_base_url.rstrip("/")

    def select_functions(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        tools: Sequence[Mapping[str, object]],
    ) -> FunctionCallBatch:
        _validate_strict_function_schemas(tools)
        payload: dict[str, object] = {
            "model": self._model,
            "instructions": system_prompt,
            "input": user_prompt,
            "tools": list(tools),
            "tool_choice": "required",
            "parallel_tool_calls": True,
            "reasoning": {"effort": LOW_REASONING_EFFORT},
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        try:
            response = self._transport.post_json(
                f"{self._api_base_url}/responses",
                headers=headers,
                payload=payload,
            )
        except Exception:
            raise LLMClientError("LLM provider request failed.") from None

        return _extract_function_batch(response)


class UrllibJsonTransport:
    def __init__(self, *, timeout_seconds: float = 20.0) -> None:
        self._timeout_seconds = timeout_seconds

    def post_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, object],
    ) -> Mapping[str, object]:
        request_body = json.dumps(payload).encode("utf-8")
        provider_request = request.Request(
            url,
            data=request_body,
            headers=headers,
            method="POST",
        )

        try:
            with request.urlopen(
                provider_request,
                timeout=self._timeout_seconds,
            ) as response:
                response_body = response.read().decode("utf-8")
        except error.URLError:
            raise LLMClientError("LLM provider request failed.") from None

        try:
            decoded_response = json.loads(response_body)
        except json.JSONDecodeError:
            raise LLMClientError("LLM provider returned invalid JSON.") from None

        if not isinstance(decoded_response, Mapping):
            raise LLMClientError("LLM provider returned invalid JSON.")
        return decoded_response


def _uses_minimal_reasoning_effort(model: str) -> bool:
    return (
        model == "gpt-5"
        or model.startswith("gpt-5-2025-")
        or model == "gpt-5-mini"
        or model.startswith("gpt-5-mini-")
        or model == "gpt-5-nano"
        or model.startswith("gpt-5-nano-")
    )


def _requires_default_temperature(model: str) -> bool:
    return (
        _uses_minimal_reasoning_effort(model)
        or model == "chat-latest"
        or model.endswith("-chat-latest")
        or model == "gpt-5.5"
        or model.startswith("gpt-5.5-")
        or _is_o_series_model(model)
    )


def _is_o_series_model(model: str) -> bool:
    return (
        model == "o1"
        or model.startswith("o1-")
        or model == "o3"
        or model.startswith("o3-")
        or model == "o4-mini"
        or model.startswith("o4-mini-")
    )


def _extract_message_content(response: Mapping[str, object]) -> str:
    choices = response.get("choices")
    if (
        not isinstance(choices, Sequence)
        or isinstance(choices, str | bytes)
        or not choices
    ):
        raise LLMClientError("LLM provider returned invalid JSON.")

    first_choice = choices[0]
    if not isinstance(first_choice, Mapping):
        raise LLMClientError("LLM provider returned invalid JSON.")

    message = first_choice.get("message")
    if not isinstance(message, Mapping):
        raise LLMClientError("LLM provider returned invalid JSON.")

    content = message.get("content")
    if not isinstance(content, str):
        raise LLMClientError("LLM provider returned invalid JSON.")

    return content


def _validate_strict_function_schemas(
    tools: Sequence[Mapping[str, object]],
) -> None:
    if not tools:
        raise ValueError("at least one strict function schema is required")
    for tool in tools:
        parameters = tool.get("parameters")
        try:
            ApplicationFunction(str(tool.get("name")))
        except ValueError:
            raise ValueError("unknown application function schema") from None
        if (
            tool.get("type") != "function"
            or tool.get("strict") is not True
            or not isinstance(parameters, Mapping)
            or parameters.get("additionalProperties") is not False
        ):
            raise ValueError("tools must use a strict function schema")


def _extract_function_batch(response: Mapping[str, object]) -> FunctionCallBatch:
    if response.get("status") != "completed":
        raise LLMClientError("LLM provider response is not completed.")
    output = response.get("output")
    if isinstance(output, str | bytes) or not isinstance(output, Sequence):
        raise LLMClientError("LLM provider returned invalid function output.")

    proposals: list[FunctionCallProposal] = []
    for item in output:
        if not isinstance(item, Mapping):
            raise LLMClientError("LLM provider returned invalid function output.")
        item_type = item.get("type")
        if item_type == "reasoning":
            continue
        if item_type == "message":
            raise LLMClientError("LLM provider must not return assistant text.")
        if item_type != "function_call":
            raise LLMClientError("LLM provider returned invalid function output.")
        if item.get("status") != "completed":
            raise LLMClientError("LLM provider function call is not completed.")

        function_name = item.get("name")
        if not isinstance(function_name, str):
            raise LLMClientError("LLM provider returned unknown function.")
        try:
            function = ApplicationFunction(function_name)
        except ValueError:
            raise LLMClientError("LLM provider returned unknown function.") from None

        arguments_json = item.get("arguments")
        if not isinstance(arguments_json, str):
            raise LLMClientError("LLM provider returned invalid function arguments.")
        try:
            arguments = json.loads(arguments_json)
        except json.JSONDecodeError:
            raise LLMClientError(
                "LLM provider returned invalid function arguments."
            ) from None
        if not isinstance(arguments, Mapping):
            raise LLMClientError("LLM provider returned invalid function arguments.")
        proposals.append(
            FunctionCallProposal(
                function=function,
                arguments=dict(arguments),
            )
        )

    try:
        return FunctionCallBatch(calls=tuple(proposals))
    except ValueError:
        raise LLMClientError(
            "LLM provider must return a non-empty function batch."
        ) from None
