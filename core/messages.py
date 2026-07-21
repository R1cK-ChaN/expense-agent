from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class ConversationKind(str, Enum):
    PERSONAL = "personal"
    GROUP = "group"


@dataclass(frozen=True)
class InboundMessage:
    source_platform: str
    source_user_id: str
    source_chat_id: str
    source_message_id: str
    message_text: str
    received_at: datetime
    source_username: str | None = None
    source_user_display_name: str | None = None
    conversation_kind: ConversationKind = ConversationKind.PERSONAL


TextMessageHandler = Callable[[InboundMessage], str]
