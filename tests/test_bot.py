from datetime import datetime, timedelta, timezone

from wechat_remind.bot import ReminderBot
from wechat_remind.llm import AssistantDecision
from wechat_remind.store import ReminderStore, to_utc_iso, utc_now


class FakeClient:
    def __init__(self):
        self.sent = []

    def send_text(self, base_url, token, to_user_id, text, context_token=None, run_id=None):
        self.sent.append(
            {
                "base_url": base_url,
                "token": token,
                "to_user_id": to_user_id,
                "text": text,
                "context_token": context_token,
            }
        )


class FakeAssistant:
    def __init__(self, decision):
        self.decision = decision

    def interpret(self, message_text, now_local, timezone_name, owner_weixin_user_id, history=None):
        return self.decision


def test_message_creates_reminder_and_scheduler_sends_it(tmp_path):
    store = ReminderStore(tmp_path / "bot.sqlite3")
    store.save_account("bot@im.bot", "token", "https://base")
    account = store.get_account()
    due = datetime.now(timezone.utc) + timedelta(minutes=10)
    assistant = FakeAssistant(
        AssistantDecision(
            action="create_reminder",
            arguments={
                "message": "喝水",
                "due_at": due.isoformat(),
                "timezone": "Asia/Shanghai",
                "confidence": 0.99,
            },
        )
    )
    client = FakeClient()
    bot = ReminderBot(store, client, assistant)

    bot.handle_message(
        account,
        {
            "message_id": 1,
            "seq": 1,
            "from_user_id": "owner@im.wechat",
            "context_token": "ctx",
            "item_list": [{"type": 1, "text_item": {"text": "10分钟后提醒我喝水"}}],
        },
    )

    assert "已创建提醒" in client.sent[-1]["text"]
    reminders = store.list_reminders()
    assert len(reminders) == 1
    logs = list(reversed(store.list_model_tool_logs()))
    assert [(item.source, item.action, item.status) for item in logs] == [
        ("model", "create_reminder", "decision"),
        ("tool_execute", "create_reminder", "success"),
    ]
    assert logs[0].arguments["message"] == "喝水"

    with store._connect() as conn:
        conn.execute(
            "UPDATE reminders SET due_at_utc = ? WHERE id = ?",
            (to_utc_iso(utc_now() - timedelta(seconds=1)), reminders[0].id),
        )

    bot.handle_due_reminders(account)

    assert client.sent[-1]["text"] == "提醒：喝水"
    assert client.sent[-1]["to_user_id"] == "owner@im.wechat"
    assert client.sent[-1]["context_token"] == "ctx"
    assert store.list_reminders() == []


def test_non_owner_is_ignored(tmp_path):
    store = ReminderStore(tmp_path / "bot.sqlite3")
    store.save_account("bot@im.bot", "token", "https://base")
    store.ensure_owner("owner@im.wechat", "ctx")
    account = store.get_account()
    client = FakeClient()
    bot = ReminderBot(store, client, FakeAssistant(AssistantDecision("reply", {}, "ok")))

    bot.handle_message(
        account,
        {
            "message_id": 2,
            "from_user_id": "other@im.wechat",
            "context_token": "other-ctx",
            "item_list": [{"type": 1, "text_item": {"text": "hello"}}],
        },
    )

    assert client.sent == []
