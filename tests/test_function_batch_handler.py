from datetime import datetime, timezone

import pytest

from core.function_batch_executor import BatchStart
from core.function_batch_handler import FunctionBatchHandler
from core.function_calls import (
    ApplicationFunction,
    FunctionCallBatch,
    FunctionCallProposal,
)
from core.messages import ConversationKind, InboundMessage
from core.pending_requests import PendingRequest
from integrations.google_sheets.repository import TransactionRepositoryError


def test_completed_delivery_replays_before_selector_call():
    repository = FakeRepository(
        BatchStart(batch_id="batch-1", stored_reply="已记录：原结果")
    )
    selector = FakeSelector(batch())
    pending = FakePending()
    handler = make_handler(repository, selector, pending)

    reply = handler.handle_message(message())

    assert reply == "已记录：原结果"
    assert selector.calls == []
    assert pending.removed == []


def test_incomplete_delivery_resumes_persisted_batch_without_selector_call():
    repository = FakeRepository(
        BatchStart(
            batch_id="batch-1",
            stored_reply=None,
            accepted_calls=(
                {
                    "function": "reject_unsupported_request",
                    "arguments": {"capability_code": "delete"},
                },
            ),
        )
    )
    selector = FakeSelector(batch())
    executor = FakeExecutor()
    handler = make_handler(repository, selector, FakePending(), executor=executor)

    handler.handle_message(message())

    assert selector.calls == []
    assert executor.batches[0].calls[0].function is ApplicationFunction.REJECT_UNSUPPORTED_REQUEST


def test_new_delivery_passes_bounded_pending_context_to_selector():
    repository = FakeRepository(None)
    selector = FakeSelector(batch())
    pending = FakePending(
        value=PendingRequest(
            platform="telegram",
            user_id="42",
            chat_id="100",
            proposed_function="record_expense",
            known_arguments={"merchant": "Toast Box"},
            missing_fields=("amount",),
            expires_at=datetime(2026, 7, 21, 2, 10, tzinfo=timezone.utc),
        )
    )
    handler = make_handler(repository, selector, pending)

    handler.handle_message(message())

    context = selector.calls[0][1]
    assert context.today.isoformat() == "2026-07-21"
    assert context.conversation_kind is ConversationKind.GROUP
    assert context.pending_request["known_arguments"] == {"merchant": "Toast Box"}
    assert repository.accepted[0][0] == "batch-new"


def test_repository_failure_is_raised_so_provider_can_retry():
    handler = make_handler(
        FakeRepository(
            BatchStart(
                batch_id="batch-1",
                stored_reply=None,
                accepted_calls=(
                    {
                        "function": "reject_unsupported_request",
                        "arguments": {"capability_code": "unsupported"},
                    },
                ),
            )
        ),
        FakeSelector(batch()),
        FakePending(),
        executor=FailingExecutor(),
    )

    with pytest.raises(TransactionRepositoryError):
        handler.handle_message(message())


def make_handler(repository, selector, pending, executor=None):
    return FunctionBatchHandler(
        selector=selector,
        executor=executor or FakeExecutor(),
        repository=repository,
        pending_requests=pending,
        timezone="Asia/Singapore",
        default_currency="SGD",
    )


def batch():
    return FunctionCallBatch(
        calls=(
            FunctionCallProposal(
                function=ApplicationFunction.REJECT_UNSUPPORTED_REQUEST,
                arguments={"capability_code": "unsupported"},
            ),
        )
    )


def message():
    return InboundMessage(
        source_platform="telegram",
        source_user_id="42",
        source_chat_id="100",
        source_message_id="9001",
        message_text="test",
        received_at=datetime(2026, 7, 21, 2, 0, tzinfo=timezone.utc),
        conversation_kind=ConversationKind.GROUP,
    )


class FakeRepository:
    def __init__(self, result):
        self.result = result
        self.accepted = []

    def find_batch(self, request):
        return self.result

    def begin_batch(self, request, accepted_calls):
        return BatchStart(
            batch_id="batch-new",
            stored_reply=None,
            is_new=True,
        )

    def accept_calls(self, batch_id, accepted_calls):
        self.accepted.append((batch_id, accepted_calls))


class FakeSelector:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def select(self, text, *, context):
        self.calls.append((text, context))
        return self.result


class FakeExecutor:
    def __init__(self):
        self.batches = []

    def execute(self, message, batch):
        self.batches.append(batch)
        return "done"


class FailingExecutor:
    def execute(self, message, batch):
        raise TransactionRepositoryError("completion failed")


class FakePending:
    def __init__(self, value=None):
        self.value = value
        self.removed = []

    def load(self, **kwargs):
        return self.value

    def remove(self, **kwargs):
        self.removed.append(kwargs)
