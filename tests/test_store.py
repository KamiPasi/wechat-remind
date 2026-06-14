from datetime import datetime, timedelta, timezone

from wechat_remind.store import ReminderStore, utc_now


def test_store_account_owner_poll_and_reminders(tmp_path):
    store = ReminderStore(tmp_path / "bot.sqlite3")

    store.save_account("bot@im.bot", "token", "https://base", "owner-user")
    account = store.get_account()
    assert account is not None
    assert account.id == "bot@im.bot"
    assert store.local_tokens() == ["token"]

    owner = store.ensure_owner("owner@im.wechat", "ctx-1")
    assert owner.weixin_user_id == "owner@im.wechat"
    store.update_owner_context("owner@im.wechat", "ctx-2")
    assert store.get_owner().context_token == "ctx-2"

    store.save_poll_state("buf")
    assert store.get_poll_state() == "buf"

    due_at = utc_now() + timedelta(minutes=5)
    reminder_id = store.create_reminder("drink water", due_at, "Asia/Shanghai")
    reminders = store.list_reminders()
    assert [r.id for r in reminders] == [reminder_id]
    assert store.cancel_reminder(reminder_id)
    assert store.list_reminders() == []


def test_due_reminders_and_dedup(tmp_path):
    store = ReminderStore(tmp_path / "bot.sqlite3")
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    future = datetime.now(timezone.utc) + timedelta(minutes=5)

    due_id = store.create_reminder("due", past, "Asia/Shanghai")
    store.create_reminder("future", future, "Asia/Shanghai")

    due = store.due_reminders(datetime.now(timezone.utc))
    assert [r.id for r in due] == [due_id]

    message = {"message_id": 42, "seq": 7}
    assert store.record_inbound_once("account", message)
    assert not store.record_inbound_once("account", message)


def test_conversation_history_is_limited_and_ordered(tmp_path):
    store = ReminderStore(tmp_path / "bot.sqlite3")

    store.append_conversation_message("user", "提醒我喝水")
    store.append_conversation_message("assistant", "什么时候提醒？")
    store.append_conversation_message("user", "10分钟后")

    history = store.list_conversation_messages(limit=2)
    assert [(item.role, item.content) for item in history] == [
        ("assistant", "什么时候提醒？"),
        ("user", "10分钟后"),
    ]


def test_model_tool_logs_are_persisted(tmp_path):
    store = ReminderStore(tmp_path / "bot.sqlite3")

    log_id = store.add_model_tool_log(
        source="model",
        model="gpt-5.5",
        action="create_reminder",
        arguments={"message": "喝水", "confidence": 0.9},
        message_text="10分钟后提醒我喝水",
        reply=None,
        status="decision",
    )

    logs = store.list_model_tool_logs()
    assert len(logs) == 1
    assert logs[0].id == log_id
    assert logs[0].source == "model"
    assert logs[0].model == "gpt-5.5"
    assert logs[0].action == "create_reminder"
    assert logs[0].arguments["message"] == "喝水"
    assert logs[0].message_text == "10分钟后提醒我喝水"
    assert logs[0].status == "decision"
