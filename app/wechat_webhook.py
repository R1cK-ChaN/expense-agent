import hmac
from datetime import datetime, timezone
from hashlib import sha1
from xml.etree import ElementTree

from fastapi import APIRouter, Body, HTTPException, Response

from core.messages import InboundMessage, TextMessageHandler


DEFAULT_TEXT_MESSAGE_REPLY = (
    "I received your message. Expense parsing is not enabled yet."
)
WECHAT_PLATFORM = "wechat"
WECHAT_SUCCESS_RESPONSE = "success"


WeChatTextHandler = TextMessageHandler


def create_wechat_webhook_router(
    *,
    wechat_token: str | None,
    wechat_text_handler: WeChatTextHandler | None = None,
) -> APIRouter:
    router = APIRouter()
    text_handler = wechat_text_handler or _default_text_handler

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

        parsed_message = _parse_wechat_text_message(body)
        if parsed_message is None:
            return Response(content=WECHAT_SUCCESS_RESPONSE, media_type="text/plain")

        reply_text = text_handler(parsed_message.inbound_message)
        return Response(
            content=_wechat_text_reply_xml(
                to_user=parsed_message.from_user_name,
                from_user=parsed_message.to_user_name,
                content=reply_text,
            ),
            media_type="application/xml",
        )

    return router


def _default_text_handler(message: InboundMessage) -> str:
    return DEFAULT_TEXT_MESSAGE_REPLY


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


def _parse_wechat_text_message(body: bytes) -> "_ParsedWeChatMessage | None":
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        return None

    if _xml_text(root, "MsgType") != "text":
        return None

    to_user_name = _xml_text(root, "ToUserName")
    from_user_name = _xml_text(root, "FromUserName")
    create_time = _xml_text(root, "CreateTime")
    content = _xml_text(root, "Content")
    message_id = _xml_text(root, "MsgId")
    received_at = _wechat_timestamp(create_time)
    if (
        to_user_name is None
        or from_user_name is None
        or content is None
        or content == ""
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
