"""Select one complete application function batch from an inbound message."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from core.function_calls import APPLICATION_FUNCTION_TOOLS, FunctionCallBatch
from core.messages import ConversationKind


SYSTEM_PROMPT = """
You select backend application functions for an expense tracking product.
Return one complete function-call batch for the current user message.
You may select multiple functions, including multiple record_expense calls.
Never produce final user-visible text. Never calculate financial totals.
Never access a ledger, invent missing amounts, or request destructive actions.
Use request_clarification or reject_unsupported_request when appropriate.
When bounded pending-request context is present, combine it only with the
current message to complete or replace that request; it is not chat history.
The backend validates every proposal and owns all execution and replies.
For statistics, propose personal scope only when the user explicitly asks for
their own spending. Otherwise leave scope null so the backend applies the
deterministic private-chat or group-chat default.
""".strip()


class FunctionSelectionClient(Protocol):
    def select_functions(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        tools: Sequence[Mapping[str, object]],
    ) -> FunctionCallBatch:
        raise NotImplementedError


@dataclass(frozen=True)
class FunctionSelectionContext:
    today: date
    timezone: str
    default_currency: str
    conversation_kind: ConversationKind = ConversationKind.PERSONAL
    pending_request: Mapping[str, object] | None = None


class FunctionSelector:
    def __init__(self, *, llm_client: FunctionSelectionClient) -> None:
        self._llm_client = llm_client

    def select(
        self,
        text: str,
        *,
        context: FunctionSelectionContext,
    ) -> FunctionCallBatch:
        return self._llm_client.select_functions(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=_build_user_prompt(text, context),
            tools=APPLICATION_FUNCTION_TOOLS,
        )


def _build_user_prompt(text: str, context: FunctionSelectionContext) -> str:
    return "\n".join(
        [
            f"TODAY: {context.today.isoformat()}",
            f"TIMEZONE: {context.timezone}",
            f"DEFAULT_CURRENCY: {context.default_currency}",
            f"CONVERSATION_KIND: {context.conversation_kind.value}",
            f"PENDING_REQUEST: {context.pending_request!r}",
            "USER_MESSAGE:",
            text,
        ]
    )
