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


def test_wechat_post_text_message_preserves_content_and_returns_passive_reply_xml():
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
            <Content><![CDATA[  午饭 12.5 麦当劳  ]]></Content>
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
            message_text="  午饭 12.5 麦当劳  ",
            received_at=received_at,
        )
    ]
    reply = ElementTree.fromstring(response.text)
    assert xml_text(reply, "ToUserName") == "user-openid"
    assert xml_text(reply, "FromUserName") == "official-account"
    assert xml_text(reply, "MsgType") == "text"
    assert xml_text(reply, "Content") == "fixed reply"


def test_wechat_post_voice_with_recognition_uses_text_handler_path():
    handled_messages: list[InboundMessage] = []
    app = create_app(
        wechat_token=WECHAT_TOKEN,
        wechat_text_handler=lambda message: handled_messages.append(message)
        or "voice reply",
    )
    client = TestClient(app)
    received_at = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)

    response = post_wechat_xml(
        client,
        timestamp=str(int(received_at.timestamp())),
        content="""<xml>
            <ToUserName><![CDATA[official-account]]></ToUserName>
            <FromUserName><![CDATA[user-openid]]></FromUserName>
            <CreateTime>1779278400</CreateTime>
            <MsgType><![CDATA[voice]]></MsgType>
            <MediaId><![CDATA[voice-media-id]]></MediaId>
            <Format><![CDATA[amr]]></Format>
            <Recognition><![CDATA[午饭 13]]></Recognition>
            <MsgId>voice-9001</MsgId>
        </xml>""",
    )

    assert response.status_code == 200
    assert handled_messages == [
        InboundMessage(
            source_platform="wechat",
            source_user_id="user-openid",
            source_chat_id="official-account",
            source_message_id="voice-9001",
            message_text="午饭 13",
            received_at=received_at,
        )
    ]
    reply = ElementTree.fromstring(response.text)
    assert xml_text(reply, "Content") == "voice reply"


def test_wechat_post_voice_without_recognition_replies_without_text_handler():
    app = create_app(
        wechat_token=WECHAT_TOKEN,
        wechat_text_handler=raising_text_handler,
    )
    client = TestClient(app)

    response = post_wechat_xml(
        client,
        content="""<xml>
            <ToUserName><![CDATA[official-account]]></ToUserName>
            <FromUserName><![CDATA[user-openid]]></FromUserName>
            <CreateTime>1779278400</CreateTime>
            <MsgType><![CDATA[voice]]></MsgType>
            <MediaId><![CDATA[voice-media-id]]></MediaId>
            <Format><![CDATA[amr]]></Format>
            <MsgId>voice-9002</MsgId>
        </xml>""",
    )

    assert response.status_code == 200
    reply = ElementTree.fromstring(response.text)
    assert (
        xml_text(reply, "Content")
        == "语音没识别清楚，可以发文字，例如：午饭 13。"
    )


def test_wechat_post_voice_with_blank_recognition_replies_without_text_handler():
    app = create_app(
        wechat_token=WECHAT_TOKEN,
        wechat_text_handler=raising_text_handler,
    )
    client = TestClient(app)

    response = post_wechat_xml(
        client,
        content="""<xml>
            <ToUserName><![CDATA[official-account]]></ToUserName>
            <FromUserName><![CDATA[user-openid]]></FromUserName>
            <CreateTime>1779278400</CreateTime>
            <MsgType><![CDATA[voice]]></MsgType>
            <MediaId><![CDATA[voice-media-id]]></MediaId>
            <Format><![CDATA[amr]]></Format>
            <Recognition><![CDATA[   ]]></Recognition>
            <MsgId>voice-9003</MsgId>
        </xml>""",
    )

    assert response.status_code == 200
    reply = ElementTree.fromstring(response.text)
    assert (
        xml_text(reply, "Content")
        == "语音没识别清楚，可以发文字，例如：午饭 13。"
    )


def test_wechat_post_location_acknowledges_and_does_not_call_text_handler():
    handled_locations: list[object] = []
    app = create_app(
        wechat_token=WECHAT_TOKEN,
        wechat_text_handler=raising_text_handler,
        wechat_location_handler=lambda location: handled_locations.append(location),
    )
    client = TestClient(app)
    received_at = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)

    response = post_wechat_xml(
        client,
        timestamp=str(int(received_at.timestamp())),
        content="""<xml>
            <ToUserName><![CDATA[official-account]]></ToUserName>
            <FromUserName><![CDATA[user-openid]]></FromUserName>
            <CreateTime>1779278400</CreateTime>
            <MsgType><![CDATA[location]]></MsgType>
            <Location_X>1.300000</Location_X>
            <Location_Y>103.800000</Location_Y>
            <Scale>15</Scale>
            <Label><![CDATA[Singapore]]></Label>
            <MsgId>location-9001</MsgId>
        </xml>""",
    )

    assert response.status_code == 200
    reply = ElementTree.fromstring(response.text)
    assert xml_text(reply, "Content") == "已收到位置。"
    assert len(handled_locations) == 1
    location = handled_locations[0]
    assert location.source_platform == "wechat"
    assert location.source_user_id == "user-openid"
    assert location.source_chat_id == "official-account"
    assert location.latitude == 1.3
    assert location.longitude == 103.8
    assert location.received_at == received_at


def test_wechat_post_location_event_stores_location_and_returns_success():
    handled_locations: list[object] = []
    app = create_app(
        wechat_token=WECHAT_TOKEN,
        wechat_text_handler=raising_text_handler,
        wechat_location_handler=lambda location: handled_locations.append(location),
    )
    client = TestClient(app)
    received_at = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)

    response = post_wechat_xml(
        client,
        timestamp=str(int(received_at.timestamp())),
        content="""<xml>
            <ToUserName><![CDATA[official-account]]></ToUserName>
            <FromUserName><![CDATA[user-openid]]></FromUserName>
            <CreateTime>1779278400</CreateTime>
            <MsgType><![CDATA[event]]></MsgType>
            <Event><![CDATA[LOCATION]]></Event>
            <Latitude>1.300000</Latitude>
            <Longitude>103.800000</Longitude>
            <Precision>65.0</Precision>
        </xml>""",
    )

    assert response.status_code == 200
    assert response.text == "success"
    assert len(handled_locations) == 1
    location = handled_locations[0]
    assert location.latitude == 1.3
    assert location.longitude == 103.8
    assert location.precision == 65.0
    assert location.received_at == received_at


def test_wechat_post_subscribe_event_returns_welcome_without_text_handler():
    app = create_app(
        wechat_token=WECHAT_TOKEN,
        wechat_text_handler=raising_text_handler,
    )
    client = TestClient(app)

    response = post_wechat_xml(
        client,
        content="""<xml>
            <ToUserName><![CDATA[official-account]]></ToUserName>
            <FromUserName><![CDATA[user-openid]]></FromUserName>
            <CreateTime>1779278400</CreateTime>
            <MsgType><![CDATA[event]]></MsgType>
            <Event><![CDATA[subscribe]]></Event>
        </xml>""",
    )

    assert response.status_code == 200
    reply = ElementTree.fromstring(response.text)
    assert xml_text(reply, "Content") == "欢迎使用记账助手，可以直接发送：午饭 13。"


def test_wechat_post_unsupported_message_type_returns_success_without_text_handler():
    app = create_app(
        wechat_token=WECHAT_TOKEN,
        wechat_text_handler=raising_text_handler,
    )
    client = TestClient(app)

    response = post_wechat_xml(
        client,
        content="""<xml>
            <ToUserName><![CDATA[official-account]]></ToUserName>
            <FromUserName><![CDATA[user-openid]]></FromUserName>
            <CreateTime>1779278400</CreateTime>
            <MsgType><![CDATA[image]]></MsgType>
            <MsgId>image-9001</MsgId>
        </xml>""",
    )

    assert response.status_code == 200
    assert response.text == "success"


def test_wechat_post_unsupported_event_returns_success_without_text_handler():
    app = create_app(
        wechat_token=WECHAT_TOKEN,
        wechat_text_handler=raising_text_handler,
    )
    client = TestClient(app)

    response = post_wechat_xml(
        client,
        content="""<xml>
            <ToUserName><![CDATA[official-account]]></ToUserName>
            <FromUserName><![CDATA[user-openid]]></FromUserName>
            <CreateTime>1779278400</CreateTime>
            <MsgType><![CDATA[event]]></MsgType>
            <Event><![CDATA[CLICK]]></Event>
        </xml>""",
    )

    assert response.status_code == 200
    assert response.text == "success"


def post_wechat_xml(
    client: TestClient,
    *,
    content: str,
    timestamp: str = "1779251400",
) -> object:
    return client.post(
        "/wechat/webhook",
        params=signed_params(timestamp=timestamp),
        content=content,
        headers={"Content-Type": "application/xml"},
    )


def raising_text_handler(message: InboundMessage) -> str:
    raise AssertionError("text handler should not be called")


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
