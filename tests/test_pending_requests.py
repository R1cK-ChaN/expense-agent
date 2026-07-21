from datetime import datetime, timedelta, timezone

from core.pending_requests import PendingRequest, PendingRequestService


NOW = datetime(2026, 7, 21, 10, 0, tzinfo=timezone.utc)


def test_save_replaces_chat_pending_request_with_ten_minute_expiry():
    repository = MemoryPendingRepository()
    service = PendingRequestService(repository=repository, clock=lambda: NOW)

    saved = service.save(
        platform="telegram",
        user_id="42",
        chat_id="100",
        proposed_function="record_expense",
        known_arguments={"merchant": "Toast Box"},
        missing_fields=("amount",),
    )
    replacement = service.save(
        platform="telegram",
        user_id="42",
        chat_id="100",
        proposed_function="record_expense",
        known_arguments={"amount": "8.50"},
        missing_fields=("category",),
    )

    assert saved.expires_at == NOW + timedelta(minutes=10)
    assert repository.values[saved.key] == replacement
    assert len(repository.values) == 1


def test_load_removes_expired_request_without_extending_it():
    repository = MemoryPendingRepository()
    service = PendingRequestService(repository=repository, clock=lambda: NOW)
    expired = PendingRequest(
        platform="telegram",
        user_id="42",
        chat_id="100",
        proposed_function="record_expense",
        known_arguments={},
        missing_fields=("amount",),
        expires_at=NOW,
    )
    repository.upsert(expired)

    assert service.load(platform="telegram", user_id="42", chat_id="100") is None
    assert expired.key not in repository.values


def test_success_removes_pending_request_for_that_chat_only():
    repository = MemoryPendingRepository()
    service = PendingRequestService(repository=repository, clock=lambda: NOW)
    first = service.save(
        platform="telegram",
        user_id="42",
        chat_id="100",
        proposed_function="record_expense",
        known_arguments={},
        missing_fields=("amount",),
    )
    second = service.save(
        platform="telegram",
        user_id="42",
        chat_id="200",
        proposed_function="record_expense",
        known_arguments={},
        missing_fields=("amount",),
    )

    service.remove(platform="telegram", user_id="42", chat_id="100")

    assert first.key not in repository.values
    assert second.key in repository.values


class MemoryPendingRepository:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str, str], PendingRequest] = {}

    def get(self, *, platform: str, user_id: str, chat_id: str):
        return self.values.get((platform, user_id, chat_id))

    def upsert(self, request: PendingRequest) -> None:
        self.values[request.key] = request

    def delete(self, *, platform: str, user_id: str, chat_id: str) -> None:
        self.values.pop((platform, user_id, chat_id), None)
