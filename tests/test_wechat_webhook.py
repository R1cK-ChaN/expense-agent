from datetime import datetime, timezone
from hashlib import sha1
from xml.etree import ElementTree

from fastapi.testclient import TestClient

from app.main import create_app
from core.messages import InboundMessage


WECHAT_TOKEN = "wechat-token"


def test_wechat_callback_validation_returns_echostr():
    app = create_app(wechat_token=WECHAT_TOKEN)
    client = TestClient(app)

    response = client.get(
        "/wechat/webhook",
        params=signed_params(
            timestamp="1779251400",
            nonce="nonce-1",
            echostr="callback-ok",
        ),
    )

    assert response.status_code == 200
    assert response.text == "callback-ok"


def test_wechat_callback_rejects_invalid_signature():
    app = create_app(wechat_token=WECHAT_TOKEN)
    client = TestClient(app)

    response = client.get(
        "/wechat/webhook",
        params={
            "signature": "wrong-signature",
            "timestamp": "1779251400",
            "nonce": "nonce-1",
            "echostr": "callback-ok",
        },
    )

    assert response.status_code == 401


def test_wechat_post_text_message_normalizes_and_returns_passive_reply_xml():
    handled_messages: list[InboundMessage] = []
    app = create_app(
        wechat_token=WECHAT_TOKEN,
        wechat_text_handler=lambda message: handled_messages.append(message)
        or "fixed reply",
    )
    client = TestClient(app)
    received_at = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)

    response = client.post(
        "/wechat/webhook",
        params=signed_params(timestamp=str(int(received_at.timestamp()))),
        content="""<xml>
            <ToUserName><![CDATA[official-account]]></ToUserName>
            <FromUserName><![CDATA[user-openid]]></FromUserName>
            <CreateTime>1779278400</CreateTime>
            <MsgType><![CDATA[text]]></MsgType>
            <Content><![CDATA[午饭 12.5 麦当劳]]></Content>
            <MsgId>msg-9001</MsgId>
        </xml>""",
        headers={"Content-Type": "application/xml"},
    )

    assert response.status_code == 200
    assert handled_messages == [
        InboundMessage(
            source_platform="wechat",
            source_user_id="user-openid",
            source_chat_id="official-account",
            source_message_id="msg-9001",
            message_text="午饭 12.5 麦当劳",
            received_at=received_at,
        )
    ]
    reply = ElementTree.fromstring(response.text)
    assert xml_text(reply, "ToUserName") == "user-openid"
    assert xml_text(reply, "FromUserName") == "official-account"
    assert xml_text(reply, "MsgType") == "text"
    assert xml_text(reply, "Content") == "fixed reply"


def signed_params(
    *,
    timestamp: str = "1779251400",
    nonce: str = "nonce-1",
    echostr: str | None = None,
) -> dict[str, str]:
    params = {
        "signature": wechat_signature(timestamp=timestamp, nonce=nonce),
        "timestamp": timestamp,
        "nonce": nonce,
    }
    if echostr is not None:
        params["echostr"] = echostr
    return params


def wechat_signature(*, timestamp: str, nonce: str) -> str:
    return sha1("".join(sorted([WECHAT_TOKEN, timestamp, nonce])).encode()).hexdigest()


def xml_text(element: ElementTree.Element, name: str) -> str | None:
    child = element.find(name)
    return None if child is None else child.text
