"""Runtime orchestration around selection, durable replay, and execution."""

import logging
from time import monotonic
from zoneinfo import ZoneInfo

from core.function_batch_executor import (
    FunctionBatchExecutor,
    FunctionBatchRepository,
    function_batch_from_serialized,
)
from core.function_selector import FunctionSelectionContext, FunctionSelector
from core.messages import InboundMessage
from core.pending_requests import PendingRequestService
from integrations.google_sheets.repository import TransactionRepositoryError


PROCESSING_FAILURE_REPLY = "抱歉，暂时没能处理这条请求，请稍后再试。"
logger = logging.getLogger(__name__)


class FunctionBatchHandler:
    def __init__(
        self,
        *,
        selector: FunctionSelector,
        executor: FunctionBatchExecutor,
        repository: FunctionBatchRepository,
        pending_requests: PendingRequestService,
        timezone: str,
        default_currency: str,
    ) -> None:
        self._selector = selector
        self._executor = executor
        self._repository = repository
        self._pending_requests = pending_requests
        self._timezone = timezone
        self._default_currency = default_currency

    def handle_message(self, message: InboundMessage) -> str:
        started = monotonic()
        try:
            existing = self._repository.find_batch(message)
            if existing is not None and existing.stored_reply is not None:
                self._log_result("replayed", (), started)
                return existing.stored_reply
            if existing is not None and existing.accepted_calls:
                batch = function_batch_from_serialized(existing.accepted_calls)
            else:
                claim = self._repository.begin_batch(message, ())
                if claim.stored_reply is not None:
                    return claim.stored_reply
                if claim.accepted_calls:
                    batch = function_batch_from_serialized(claim.accepted_calls)
                    reply = self._executor.execute(message, batch)
                    self._log_result("completed", tuple(
                        call.function.value for call in batch.calls
                    ), started)
                    return reply
                if not claim.is_new:
                    raise TransactionRepositoryError(
                        "Function selection is already in progress."
                    )
                pending = self._pending_requests.load(
                    platform=message.source_platform,
                    user_id=message.source_user_id,
                    chat_id=message.source_chat_id,
                )
                batch = self._selector.select(
                    message.message_text,
                    context=FunctionSelectionContext(
                        today=message.received_at.astimezone(
                            ZoneInfo(self._timezone)
                        ).date(),
                        timezone=self._timezone,
                        default_currency=self._default_currency,
                        conversation_kind=message.conversation_kind,
                        pending_request=(
                            None
                            if pending is None
                            else {
                                "proposed_function": pending.proposed_function,
                                "known_arguments": dict(pending.known_arguments),
                                "missing_fields": pending.missing_fields,
                                "expires_at": pending.expires_at.isoformat(),
                            }
                        ),
                    ),
                )
                self._repository.accept_calls(
                    claim.batch_id,
                    tuple(
                        {
                            "function": call.function.value,
                            "arguments": dict(call.arguments),
                        }
                        for call in batch.calls
                    ),
                )
            reply = self._executor.execute(message, batch)
            self._log_result(
                "completed",
                tuple(call.function.value for call in batch.calls),
                started,
            )
            return reply
        except TransactionRepositoryError:
            logger.exception("function_batch_retryable_failure")
            raise
        except Exception:
            logger.exception("function_batch_failed")
            return PROCESSING_FAILURE_REPLY

    @staticmethod
    def _log_result(
        outcome: str,
        functions: tuple[str, ...],
        started: float,
    ) -> None:
        logger.info(
            "function_batch_handled outcome=%s function_count=%d "
            "function_names=%s latency_ms=%d",
            outcome,
            len(functions),
            ",".join(functions),
            round((monotonic() - started) * 1000),
        )
