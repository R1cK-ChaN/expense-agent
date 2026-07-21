"""Small, structured continuation state for one chat-scoped clarification."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol


PENDING_REQUEST_TTL = timedelta(minutes=10)


@dataclass(frozen=True)
class PendingRequest:
    platform: str
    user_id: str
    chat_id: str
    proposed_function: str
    known_arguments: Mapping[str, object]
    missing_fields: tuple[str, ...]
    expires_at: datetime

    def __post_init__(self) -> None:
        if self.expires_at.tzinfo is None:
            raise ValueError("pending request expiry must be timezone-aware")

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.platform, self.user_id, self.chat_id)


class PendingRequestRepository(Protocol):
    def get(
        self,
        *,
        platform: str,
        user_id: str,
        chat_id: str,
    ) -> PendingRequest | None:
        raise NotImplementedError

    def upsert(self, request: PendingRequest) -> None:
        raise NotImplementedError

    def delete(self, *, platform: str, user_id: str, chat_id: str) -> None:
        raise NotImplementedError


class PendingRequestService:
    def __init__(
        self,
        *,
        repository: PendingRequestRepository,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock or _utc_now

    def load(
        self,
        *,
        platform: str,
        user_id: str,
        chat_id: str,
    ) -> PendingRequest | None:
        request = self._repository.get(
            platform=platform,
            user_id=user_id,
            chat_id=chat_id,
        )
        if request is None:
            return None
        if request.expires_at <= self._clock():
            self.remove(platform=platform, user_id=user_id, chat_id=chat_id)
            return None
        return request

    def save(
        self,
        *,
        platform: str,
        user_id: str,
        chat_id: str,
        proposed_function: str,
        known_arguments: Mapping[str, object],
        missing_fields: tuple[str, ...],
    ) -> PendingRequest:
        request = PendingRequest(
            platform=platform,
            user_id=user_id,
            chat_id=chat_id,
            proposed_function=proposed_function,
            known_arguments=dict(known_arguments),
            missing_fields=missing_fields,
            expires_at=self._clock() + PENDING_REQUEST_TTL,
        )
        self._repository.upsert(request)
        return request

    def remove(self, *, platform: str, user_id: str, chat_id: str) -> None:
        self._repository.delete(
            platform=platform,
            user_id=user_id,
            chat_id=chat_id,
        )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
