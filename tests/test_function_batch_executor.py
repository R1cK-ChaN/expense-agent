from datetime import datetime, timezone
from decimal import Decimal

import pytest

from core.function_batch_executor import (
    BatchStart,
    FunctionBatchExecutor,
    FunctionBatchValidationError,
    UpdateLatestExpenseCommand,
)
from core.function_calls import (
    ApplicationFunction,
    FunctionCallBatch,
    FunctionCallProposal,
)
from core.messages import InboundMessage
from integrations.google_sheets.repository import TransactionRecord


def test_validates_whole_batch_before_any_write():
    repository = FakeBatchRepository()
    executor = make_executor(repository)
    batch = FunctionCallBatch(
        calls=(
            record_expense(amount="12.50", merchant="Toast Box"),
            record_expense(amount="not-money", merchant="MRT"),
        )
    )

    with pytest.raises(FunctionBatchValidationError):
        executor.execute(message(), batch)

    assert repository.started == []
    assert repository.write_batches == []


def test_multiple_creates_are_sent_to_one_atomic_write_batch():
    repository = FakeBatchRepository()
    executor = make_executor(repository)
    batch = FunctionCallBatch(
        calls=(
            record_expense(amount="12.50", merchant="Toast Box"),
            record_expense(amount="2.20", merchant="MRT", category="交通"),
        )
    )

    reply = executor.execute(message(), batch)

    assert len(repository.write_batches) == 1
    commands = repository.write_batches[0]
    assert [(command.call_index, command.record.amount) for command in commands] == [
        (0, Decimal("12.50")),
        (1, Decimal("2.20")),
    ]
    assert reply == (
        "已记录：2026-07-21 餐饮 12.50 SGD Toast Box\n"
        "已记录：2026-07-21 交通 2.20 SGD MRT"
    )
    assert repository.completed_replies == [reply]


def test_delivery_retry_returns_stored_reply_without_writing_again():
    repository = FakeBatchRepository(replay_reply="已记录：原结果")
    pending = FakePendingService()
    executor = make_executor(repository, pending=pending)

    reply = executor.execute(
        message(),
        FunctionCallBatch(calls=(record_expense(amount="99.00"),)),
    )

    assert reply == "已记录：原结果"
    assert repository.write_batches == []
    assert pending.removed == []


def test_clarification_persists_structured_pending_request_without_ledger_write():
    repository = FakeBatchRepository()
    pending = FakePendingService()
    executor = make_executor(repository, pending=pending)
    batch = FunctionCallBatch(
        calls=(
            FunctionCallProposal(
                function=ApplicationFunction.REQUEST_CLARIFICATION,
                arguments={
                    "reason_code": "missing_fields",
                    "missing_fields": ["amount"],
                    "proposed_function": "record_expense",
                    "known_arguments": {
                        "date": None,
                        "amount": None,
                        "currency": None,
                        "category": "餐饮",
                        "merchant": "Toast Box",
                        "payment_method": None,
                        "note": None,
                    },
                },
            ),
        )
    )

    reply = executor.execute(message(), batch)

    assert reply == "还缺金额，请补充一下。"
    assert repository.write_batches == []
    assert pending.saved[0]["missing_fields"] == ("amount",)
    assert pending.saved[0]["known_arguments"]["merchant"] == "Toast Box"


def test_control_function_cannot_be_mixed_with_ledger_functions():
    executor = make_executor(FakeBatchRepository())
    batch = FunctionCallBatch(
        calls=(
            record_expense(amount="5"),
            FunctionCallProposal(
                function=ApplicationFunction.REJECT_UNSUPPORTED_REQUEST,
                arguments={"capability_code": "delete"},
            ),
        )
    )

    with pytest.raises(FunctionBatchValidationError):
        executor.execute(message(), batch)


def test_mixed_batch_commits_writes_before_running_statistics():
    events = []
    repository = FakeBatchRepository(events=events)
    statistics = FakeStatistics(events=events)
    executor = make_executor(repository, statistics=statistics)
    batch = FunctionCallBatch(
        calls=(
            record_expense(amount="12.50"),
            FunctionCallProposal(
                function=ApplicationFunction.GET_SPENDING_SUMMARY,
                arguments={
                    "period": {
                        "kind": "today",
                        "start_date": None,
                        "end_date": None,
                    },
                    "category": None,
                    "merchant": None,
                },
            ),
        )
    )

    reply = executor.execute(message(), batch)

    assert events == ["writes_committed", "summary_read"]
    assert reply.endswith("今日支出共 12.50 SGD。")


def test_statistics_failure_does_not_rollback_committed_writes():
    events = []
    repository = FakeBatchRepository(events=events)
    executor = make_executor(
        repository,
        statistics=FakeStatistics(events=events, fail=True),
    )
    batch = FunctionCallBatch(
        calls=(
            record_expense(amount="12.50"),
            FunctionCallProposal(
                function=ApplicationFunction.LIST_RECENT_EXPENSES,
                arguments={"category": None, "merchant": None, "limit": 5},
            ),
        )
    )

    reply = executor.execute(message(), batch)

    assert events[0] == "writes_committed"
    assert reply.endswith("统计暂时失败，请稍后再试。")
    assert len(repository.write_batches) == 1


def test_update_latest_is_a_typed_write_command():
    repository = FakeBatchRepository()
    executor = make_executor(repository)
    batch = FunctionCallBatch(
        calls=(
            FunctionCallProposal(
                function=ApplicationFunction.UPDATE_EXPENSE,
                arguments={
                    "target": "latest",
                    "changes": {
                        "date": None,
                        "amount": "18.00",
                        "currency": None,
                        "category": None,
                        "merchant": None,
                        "payment_method": None,
                        "note": "corrected",
                    },
                },
            ),
        )
    )

    executor.execute(message(), batch)

    command = repository.write_batches[0][0]
    assert isinstance(command, UpdateLatestExpenseCommand)
    assert command.fields == {"amount": Decimal("18.00"), "note": "corrected"}


def test_resume_uses_persisted_calls_instead_of_new_model_proposal():
    persisted = (
        {
            "function": "record_expense",
            "arguments": dict(record_expense(amount="8.00").arguments),
        },
    )
    repository = FakeBatchRepository(persisted_calls=persisted)
    executor = make_executor(repository)

    reply = executor.execute(
        message(),
        FunctionCallBatch(calls=(record_expense(amount="99.00"),)),
    )

    assert repository.write_batches[0][0].record.amount == Decimal("8.00")
    assert "8.00 SGD" in reply


def test_missing_date_uses_inbound_received_date_not_processing_date():
    repository = FakeBatchRepository()
    executor = FunctionBatchExecutor(
        repository=repository,
        statistics=None,
        pending_requests=FakePendingService(),
        timezone="Asia/Singapore",
        default_currency="SGD",
        clock=lambda: datetime(2026, 7, 22, 2, 0, tzinfo=timezone.utc),
        id_factory=SequentialIds(),
    )

    executor.execute(
        message(),
        FunctionCallBatch(calls=(record_expense(amount="8.00"),)),
    )

    assert repository.write_batches[0][0].record.date == "2026-07-21"


def test_relative_statistics_use_inbound_date_on_delayed_processing():
    repository = FakeBatchRepository()
    statistics = CapturingStatistics()
    executor = FunctionBatchExecutor(
        repository=repository,
        statistics=statistics,
        pending_requests=FakePendingService(),
        timezone="Asia/Singapore",
        default_currency="SGD",
        clock=lambda: datetime(2026, 7, 22, 2, 0, tzinfo=timezone.utc),
        id_factory=SequentialIds(),
    )
    proposal = FunctionCallProposal(
        function=ApplicationFunction.GET_SPENDING_SUMMARY,
        arguments={
            "period": {"kind": "today", "start_date": None, "end_date": None},
            "category": None,
            "merchant": None,
        },
    )

    executor.execute(message(), FunctionCallBatch(calls=(proposal,)))

    assert statistics.date_range.start_date == "2026-07-21"
    assert statistics.date_range.end_date == "2026-07-21"


def test_missing_update_target_returns_deterministic_clarification():
    repository = FakeBatchRepository(missing_update_target=True)
    pending = FakePendingService()
    executor = make_executor(repository, pending=pending)
    batch = FunctionCallBatch(
        calls=(
            FunctionCallProposal(
                function=ApplicationFunction.UPDATE_EXPENSE,
                arguments={
                    "target": "latest",
                    "changes": {
                        "date": None,
                        "amount": "18.00",
                        "currency": None,
                        "category": None,
                        "merchant": None,
                        "payment_method": None,
                        "note": None,
                    },
                },
            ),
        )
    )

    reply = executor.execute(message(), batch)

    assert reply == "没有找到可修改的支出，请先记一笔或说明要修改哪一笔。"
    assert pending.saved[0]["proposed_function"] == "update_expense"


def record_expense(
    *,
    amount: str,
    merchant: str | None = None,
    category: str = "餐饮",
) -> FunctionCallProposal:
    return FunctionCallProposal(
        function=ApplicationFunction.RECORD_EXPENSE,
        arguments={
            "date": None,
            "amount": amount,
            "currency": None,
            "category": category,
            "merchant": merchant,
            "payment_method": None,
            "note": None,
        },
    )


def message() -> InboundMessage:
    return InboundMessage(
        source_platform="telegram",
        source_user_id="42",
        source_chat_id="100",
        source_message_id="9001",
        message_text="午饭 12.5，地铁 2.2",
        received_at=datetime(2026, 7, 21, 2, 0, tzinfo=timezone.utc),
        source_username="ada",
        source_user_display_name="Ada",
    )


def record(transaction_id: str, amount: str) -> TransactionRecord:
    return TransactionRecord(
        id=transaction_id,
        date="2026-07-21",
        amount=Decimal(amount),
        currency="SGD",
        type="expense",
        category="餐饮",
        merchant=None,
        payment_method=None,
        note="corrected",
        source_platform="telegram",
        source_user_id="42",
        source_username="ada",
        source_user_display_name="Ada",
        source_chat_id="100",
        source_message_id="9001",
        created_at="2026-07-21T10:00:00+08:00",
        updated_at="2026-07-21T10:00:00+08:00",
    )


def make_executor(repository, *, pending=None, statistics=None):
    return FunctionBatchExecutor(
        repository=repository,
        statistics=statistics,
        pending_requests=pending or FakePendingService(),
        timezone="Asia/Singapore",
        default_currency="SGD",
        clock=lambda: datetime(2026, 7, 21, 2, 0, tzinfo=timezone.utc),
        id_factory=SequentialIds(),
    )


class FakeBatchRepository:
    def __init__(
        self,
        replay_reply: str | None = None,
        events=None,
        persisted_calls=(),
        missing_update_target=False,
    ) -> None:
        self.replay_reply = replay_reply
        self.events = events
        self.persisted_calls = persisted_calls
        self.missing_update_target = missing_update_target
        self.started = []
        self.write_batches = []
        self.completed_replies = []

    def begin_batch(self, request, accepted_calls):
        self.started.append((request, accepted_calls))
        if self.replay_reply is not None:
            return BatchStart(batch_id="batch-1", stored_reply=self.replay_reply)
        return BatchStart(
            batch_id="batch-1",
            stored_reply=None,
            accepted_calls=tuple(self.persisted_calls),
        )

    def execute_writes(self, batch_id, commands):
        if self.missing_update_target:
            from core.function_batch_executor import UpdateTargetNotFoundError

            raise UpdateTargetNotFoundError
        self.write_batches.append(commands)
        if self.events is not None:
            self.events.append("writes_committed")
        results = {}
        for command in commands:
            if hasattr(command, "record"):
                results[command.call_index] = command.record
            else:
                results[command.call_index] = record("updated", "18.00")
        return results

    def complete_batch(self, batch_id, operation_results, reply_text):
        self.completed_replies.append(reply_text)


class FakePendingService:
    def __init__(self) -> None:
        self.saved = []
        self.removed = []

    def save(self, **kwargs):
        self.saved.append(kwargs)

    def remove(self, **kwargs):
        self.removed.append(kwargs)


class FakeStatistics:
    def __init__(self, *, events, fail=False):
        self.events = events
        self.fail = fail

    def get_spending_summary(self, **kwargs):
        self.events.append("summary_read")
        if self.fail:
            raise RuntimeError("read failed")
        return "今日支出共 12.50 SGD。"

    def list_recent_expenses(self, **kwargs):
        self.events.append("recent_read")
        if self.fail:
            raise RuntimeError("read failed")
        return "最近没有支出记录。"


class CapturingStatistics:
    def get_spending_summary(self, **kwargs):
        self.date_range = kwargs["date_range"]
        return "done"


class SequentialIds:
    def __init__(self) -> None:
        self.value = 0

    def __call__(self) -> str:
        self.value += 1
        return f"txn-{self.value}"
