from wechat_remind.ilink_client import IlinkClient


class RecordingClient(IlinkClient):
    def __init__(self):
        super().__init__(bot_agent="TestBot/0.1.0", channel_version="0.1.0")
        self.calls = []

    def _post_json(self, base_url, endpoint, body, token=None, timeout_seconds=None):
        self.calls.append(
            {
                "base_url": base_url,
                "endpoint": endpoint,
                "body": body,
                "token": token,
                "timeout_seconds": timeout_seconds,
            }
        )
        return {}


def test_post_headers_match_ilink_shape():
    client = IlinkClient()
    headers = client._post_headers("token-123")

    assert headers["Content-Type"] == "application/json"
    assert headers["AuthorizationType"] == "ilink_bot_token"
    assert headers["Authorization"] == "Bearer token-123"
    assert headers["iLink-App-Id"] == "bot"
    assert headers["iLink-App-ClientVersion"]
    assert headers["X-WECHAT-UIN"]


def test_send_text_uses_weixin_message_shape():
    client = RecordingClient()

    client.send_text(
        base_url="https://example.test",
        token="token",
        to_user_id="user@im.wechat",
        text="hello",
        context_token="ctx",
    )

    call = client.calls[0]
    assert call["endpoint"] == "ilink/bot/sendmessage"
    assert call["token"] == "token"
    body = call["body"]
    assert body["base_info"]["bot_agent"] == "TestBot/0.1.0"
    msg = body["msg"]
    assert msg["to_user_id"] == "user@im.wechat"
    assert msg["message_type"] == 2
    assert msg["message_state"] == 2
    assert msg["context_token"] == "ctx"
    assert msg["item_list"] == [{"type": 1, "text_item": {"text": "hello"}}]
