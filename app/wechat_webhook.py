import hmac
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha1
from xml.etree import ElementTree

from fastapi import APIRouter, Body, HTTPException, Response

from core.messages import InboundMessage, TextMessageHandler


DEFAULT_TEXT_MESSAGE_REPLY = (
    "I received your message. Expense parsing is not enabled yet."
)
VOICE_RECOGNITION_FAILURE_MESSAGE = "语音没识别清楚，可以发文字，例如：午饭 13。"
LOCATION_ACK_MESSAGE = "已收到位置。"
SUBSCRIBE_WELCOME_MESSAGE = "欢迎使用记账助手，可以直接发送：午饭 13。"
WECHAT_PLATFORM = "wechat"
WECHAT_SUCCESS_RESPONSE = "success"


WeChatTextHandler = TextMessageHandler

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WeChatLocationSignal:
    source_platform: str
    source_user_id: str
    source_chat_id: str
    source_message_id: str | None
    latitude: float
    longitude: float
    received_at: datetime
    precision: float | None = None
    scale: int | None = None
    label: str | None = None


WeChatLocationHandler = Callable[[WeChatLocationSignal], None]


def create_wechat_webhook_router(
    *,
    wechat_token: str | None,
    wechat_text_handler: WeChatTextHandler | None = None,
    wechat_location_handler: WeChatLocationHandler | None = None,
) -> APIRouter:
    router = APIRouter()
    text_handler = wechat_text_handler or _default_text_handler
    location_handler = wechat_location_handler or _default_location_handler

    @router.get("/wechat/webhook")
    def verify_wechat_callback(
        signature: str,
        timestamp: str,
        nonce: str,
        echostr: str,
    ) -> Response:
        _verify_wechat_signature(
            configured_token=wechat_token,
            supplied_signature=signature,
            timestamp=timestamp,
            nonce=nonce,
        )
        return Response(content=echostr, media_type="text/plain")

    @router.post("/wechat/webhook")
    def wechat_webhook(
        signature: str,
        timestamp: str,
        nonce: str,
        body: bytes = Body(default=b"", media_type="application/xml"),
    ) -> Response:
        _verify_wechat_signature(
            configured_token=wechat_token,
            supplied_signature=signature,
            timestamp=timestamp,
            nonce=nonce,
        )

        root = _parse_wechat_xml(body)
        if root is None:
            return _wechat_success_response()

        msg_type = _xml_text(root, "MsgType")
        if msg_type == "text":
            parsed_message = _parse_wechat_text_message(root)
            if parsed_message is None:
                return _wechat_success_response()
            return _wechat_reply_response(
                parsed_message,
                text_handler(parsed_message.inbound_message),
            )

        if msg_type == "voice":
            parsed_message = _parse_wechat_voice_message(root)
            if parsed_message is None:
                return _wechat_addressed_reply_response(
                    root,
                    VOICE_RECOGNITION_FAILURE_MESSAGE,
                )
            return _wechat_reply_response(
                parsed_message,
                text_handler(parsed_message.inbound_message),
            )

        if msg_type == "location":
            location = _parse_wechat_location_message(root)
            if location is not None:
                _handle_location(location_handler, location)
            return _wechat_addressed_reply_response(root, LOCATION_ACK_MESSAGE)

        if msg_type == "event":
            return _handle_wechat_event(
                root,
                location_handler=location_handler,
            )

        return _wechat_success_response()

    return router


def _default_text_handler(message: InboundMessage) -> str:
    return DEFAULT_TEXT_MESSAGE_REPLY


def _default_location_handler(location: WeChatLocationSignal) -> None:
    return None


def _wechat_success_response() -> Response:
    return Response(content=WECHAT_SUCCESS_RESPONSE, media_type="text/plain")


def _wechat_reply_response(
    parsed_message: "_ParsedWeChatMessage",
    reply_text: str,
) -> Response:
    return Response(
        content=_wechat_text_reply_xml(
            to_user=parsed_message.from_user_name,
            from_user=parsed_message.to_user_name,
            content=reply_text,
        ),
        media_type="application/xml",
    )


def _wechat_addressed_reply_response(root: ElementTree.Element, content: str) -> Response:
    to_user_name = _xml_text(root, "ToUserName")
    from_user_name = _xml_text(root, "FromUserName")
    if to_user_name is None or from_user_name is None:
        return _wechat_success_response()

    return Response(
        content=_wechat_text_reply_xml(
            to_user=from_user_name,
            from_user=to_user_name,
            content=content,
        ),
        media_type="application/xml",
    )


def _handle_location(
    location_handler: WeChatLocationHandler,
    location: WeChatLocationSignal,
) -> None:
    try:
        location_handler(location)
    except Exception:
        logger.exception("Failed to persist WeChat location signal.")


def _handle_wechat_event(
    root: ElementTree.Element,
    *,
    location_handler: WeChatLocationHandler,
) -> Response:
    event = _xml_text(root, "Event")
    if event == "LOCATION":
        location = _parse_wechat_location_event(root)
        if location is not None:
            _handle_location(location_handler, location)
        return _wechat_success_response()

    if event == "subscribe":
        return _wechat_addressed_reply_response(root, SUBSCRIBE_WELCOME_MESSAGE)

    return _wechat_success_response()


def _verify_wechat_signature(
    *,
    configured_token: str | None,
    supplied_signature: str,
    timestamp: str,
    nonce: str,
) -> None:
    if not configured_token:
        raise HTTPException(
            status_code=503,
            detail="WeChat token is not configured.",
        )

    expected_signature = _wechat_signature(
        token=configured_token,
        timestamp=timestamp,
        nonce=nonce,
    )
    if not hmac.compare_digest(supplied_signature, expected_signature):
        raise HTTPException(
            status_code=401,
            detail="Invalid WeChat signature.",
        )


def _wechat_signature(*, token: str, timestamp: str, nonce: str) -> str:
    payload = "".join(sorted([token, timestamp, nonce]))
    return sha1(payload.encode()).hexdigest()


def _parse_wechat_xml(body: bytes) -> ElementTree.Element | None:
    try:
        return ElementTree.fromstring(body)
    except ElementTree.ParseError:
        return None


def _parse_wechat_text_message(
    root: ElementTree.Element,
) -> "_ParsedWeChatMessage | None":
    return _parse_wechat_text_like_message(root, text_field="Content")


def _parse_wechat_voice_message(
    root: ElementTree.Element,
) -> "_ParsedWeChatMessage | None":
    return _parse_wechat_text_like_message(
        root,
        text_field="Recognition",
        reject_blank_text=True,
    )


def _parse_wechat_text_like_message(
    root: ElementTree.Element,
    *,
    text_field: str,
    reject_blank_text: bool = False,
) -> "_ParsedWeChatMessage | None":
    to_user_name = _xml_text(root, "ToUserName")
    from_user_name = _xml_text(root, "FromUserName")
    create_time = _xml_text(root, "CreateTime")
    content = _xml_text(root, text_field)
    message_id = _xml_text(root, "MsgId")
    received_at = _wechat_timestamp(create_time)
    if (
        to_user_name is None
        or from_user_name is None
        or content is None
        or content == ""
        or (reject_blank_text and content.strip() == "")
        or message_id is None
        or received_at is None
    ):
        return None

    return _ParsedWeChatMessage(
        to_user_name=to_user_name,
        from_user_name=from_user_name,
        inbound_message=InboundMessage(
            source_platform=WECHAT_PLATFORM,
            source_user_id=from_user_name,
            source_chat_id=to_user_name,
            source_message_id=message_id,
            message_text=content,
            received_at=received_at,
        ),
    )


def _parse_wechat_location_message(
    root: ElementTree.Element,
) -> WeChatLocationSignal | None:
    return _parse_location_signal(
        root,
        latitude_field="Location_X",
        longitude_field="Location_Y",
        precision_field=None,
        scale_field="Scale",
        label_field="Label",
        message_id_field="MsgId",
    )


def _parse_wechat_location_event(
    root: ElementTree.Element,
) -> WeChatLocationSignal | None:
    return _parse_location_signal(
        root,
        latitude_field="Latitude",
        longitude_field="Longitude",
        precision_field="Precision",
        scale_field=None,
        label_field=None,
        message_id_field=None,
    )


def _parse_location_signal(
    root: ElementTree.Element,
    *,
    latitude_field: str,
    longitude_field: str,
    precision_field: str | None,
    scale_field: str | None,
    label_field: str | None,
    message_id_field: str | None,
) -> WeChatLocationSignal | None:
    to_user_name = _xml_text(root, "ToUserName")
    from_user_name = _xml_text(root, "FromUserName")
    received_at = _wechat_timestamp(_xml_text(root, "CreateTime"))
    latitude = _xml_float(root, latitude_field)
    longitude = _xml_float(root, longitude_field)
    if (
        to_user_name is None
        or from_user_name is None
        or received_at is None
        or latitude is None
        or longitude is None
    ):
        return None

    return WeChatLocationSignal(
        source_platform=WECHAT_PLATFORM,
        source_user_id=from_user_name,
        source_chat_id=to_user_name,
        source_message_id=(
            None if message_id_field is None else _xml_text(root, message_id_field)
        ),
        latitude=latitude,
        longitude=longitude,
        precision=None if precision_field is None else _xml_float(root, precision_field),
        scale=None if scale_field is None else _xml_int(root, scale_field),
        label=None if label_field is None else _xml_text(root, label_field),
        received_at=received_at,
    )


class _ParsedWeChatMessage:
    def __init__(
        self,
        *,
        to_user_name: str,
        from_user_name: str,
        inbound_message: InboundMessage,
    ) -> None:
        self.to_user_name = to_user_name
        self.from_user_name = from_user_name
        self.inbound_message = inbound_message


def _xml_text(element: ElementTree.Element, name: str) -> str | None:
    child = element.find(name)
    if child is None:
        return None
    return child.text


def _xml_float(element: ElementTree.Element, name: str) -> float | None:
    text = _xml_text(element, name)
    if text is None:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _xml_int(element: ElementTree.Element, name: str) -> int | None:
    text = _xml_text(element, name)
    if text is None:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _wechat_timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value), timezone.utc)
    except (OverflowError, TypeError, ValueError):
        return None


def _wechat_text_reply_xml(
    *,
    to_user: str,
    from_user: str,
    content: str,
) -> str:
    root = ElementTree.Element("xml")
    _add_xml_text(root, "ToUserName", to_user)
    _add_xml_text(root, "FromUserName", from_user)
    _add_xml_text(root, "CreateTime", str(int(datetime.now(timezone.utc).timestamp())))
    _add_xml_text(root, "MsgType", "text")
    _add_xml_text(root, "Content", content)
    return ElementTree.tostring(root, encoding="unicode")


def _add_xml_text(root: ElementTree.Element, name: str, text: str) -> None:
    child = ElementTree.SubElement(root, name)
    child.text = text
