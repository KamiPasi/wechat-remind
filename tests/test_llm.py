import json

from wechat_remind.llm import ReminderAssistant


class FakeResponses:
    def __init__(self):
        self.last_request = None

    def create(self, **kwargs):
        self.last_request = kwargs
        return {
            "output": [
                {
                    "type": "function_call",
                    "name": "create_reminder",
                    "arguments": json.dumps(
                        {
                            "message": "drink water",
                            "due_at": "2026-06-14T10:00:00+08:00",
                            "timezone": "Asia/Shanghai",
                            "confidence": 0.95,
                        }
                    ),
                }
            ]
        }


class FakeClient:
    def __init__(self):
        self.responses = FakeResponses()


def test_responses_function_call_is_parsed():
    fake = FakeClient()
    assistant = ReminderAssistant(client=fake, model="gpt-5.5", reasoning_effort="low")

    decision = assistant.interpret(
        "please ask the model to create a reminder",
        now_local=__import__("datetime").datetime.fromisoformat("2026-06-14T09:00:00+08:00"),
        timezone_name="Asia/Shanghai",
        owner_weixin_user_id="owner@im.wechat",
    )

    assert decision.action == "create_reminder"
    assert decision.arguments["message"] == "drink water"
    request = fake.responses.last_request
    assert request["model"] == "gpt-5.5"
    assert request["reasoning"] == {"effort": "low"}
    assert request["store"] is False
    assert request["tools"][0]["strict"] is True


def test_history_is_sent_to_responses_request():
    fake = FakeClient()
    assistant = ReminderAssistant(client=fake, model="gpt-5.5", reasoning_effort="low")

    assistant.interpret(
        "10 minutes later",
        now_local=__import__("datetime").datetime.fromisoformat("2026-06-14T09:00:00+08:00"),
        timezone_name="Asia/Shanghai",
        owner_weixin_user_id="owner@im.wechat",
        history=[
            {"role": "user", "content": "提醒我喝水"},
            {"role": "assistant", "content": "什么时候提醒？"},
        ],
    )

    request = fake.responses.last_request
    assert request["input"][1]["content"] == "提醒我喝水"
    assert request["input"][2]["content"] == "什么时候提醒？"
    assert "Current local time" in request["input"][-1]["content"]


def test_local_relative_minutes_parser():
    assistant = ReminderAssistant(client=FakeClient(), model="gpt-5.5", reasoning_effort="low")

    decision = assistant.interpret(
        "10分钟后提醒我喝水",
        now_local=__import__("datetime").datetime.fromisoformat("2026-06-14T13:30:00+08:00"),
        timezone_name="Asia/Shanghai",
        owner_weixin_user_id="owner@im.wechat",
    )

    assert decision.action == "create_reminder"
    assert decision.arguments["message"] == "喝水"
    assert decision.arguments["due_at"] == "2026-06-14T13:40:00+08:00"
    assert decision.arguments["confidence"] == 1.0


def test_local_list_and_cancel_parser():
    assistant = ReminderAssistant(client=FakeClient(), model="gpt-5.5", reasoning_effort="low")
    now = __import__("datetime").datetime.fromisoformat("2026-06-14T13:30:00+08:00")

    listed = assistant.interpret("提醒列表", now, "Asia/Shanghai", "owner@im.wechat")
    cancelled = assistant.interpret("取消提醒 #3", now, "Asia/Shanghai", "owner@im.wechat")

    assert listed.action == "list_reminders"
    assert cancelled.action == "cancel_reminder"
    assert cancelled.arguments["reminder_id"] == 3
