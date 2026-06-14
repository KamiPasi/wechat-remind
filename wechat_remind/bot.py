import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .ilink_client import IlinkClient
from .llm import AssistantDecision, ReminderAssistant
from .store import Account, Reminder, ReminderStore, from_iso, to_utc_iso, utc_now


LOG = logging.getLogger(__name__)


def load_timezone(name: str):
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        if name == "Asia/Shanghai":
            return timezone(timedelta(hours=8), "Asia/Shanghai")
        raise


def parse_due_at(value: str, local_tz) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=local_tz)
    return parsed.astimezone(timezone.utc)


def extract_text(message: Dict[str, Any]) -> str:
    for item in message.get("item_list") or []:
        if item.get("type") == 1:
            text_item = item.get("text_item") or {}
            text = text_item.get("text")
            if text:
                return str(text).strip()
        if item.get("type") == 3:
            voice_item = item.get("voice_item") or {}
            text = voice_item.get("text")
            if text:
                return str(text).strip()
    return ""


def format_local_time(utc_iso: str, local_tz) -> str:
    local = from_iso(utc_iso).astimezone(local_tz)
    return local.strftime("%Y-%m-%d %H:%M")


def format_reminder_list(reminders: List[Reminder], local_tz) -> str:
    if not reminders:
        return "当前没有待提醒事项。"
    lines = ["待提醒："]
    for reminder in reminders:
        lines.append(
            "#%s %s - %s"
            % (reminder.id, format_local_time(reminder.due_at_utc, local_tz), reminder.message)
        )
    return "\n".join(lines)


class ReminderBot:
    def __init__(
        self,
        store: ReminderStore,
        client: IlinkClient,
        assistant: ReminderAssistant,
        timezone_name: str = "Asia/Shanghai",
        poll_timeout_seconds: float = 35.0,
        schedule_interval_seconds: float = 10.0,
        ignore_unauthorized: bool = True,
    ) -> None:
        self.store = store
        self.client = client
        self.assistant = assistant
        self.timezone_name = timezone_name
        self.local_tz = load_timezone(timezone_name)
        self.poll_timeout_seconds = poll_timeout_seconds
        self.schedule_interval_seconds = schedule_interval_seconds
        self.ignore_unauthorized = ignore_unauthorized
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run_forever(self) -> None:
        account = self.store.get_account()
        if not account:
            raise RuntimeError("No Weixin account. Run: python -m wechat_remind login")

        try:
            self.client.notify_start(account.base_url, account.token)
        except Exception as exc:  # pragma: no cover - best effort for real runs
            LOG.warning("notify_start failed: %s", exc)

        scheduler = threading.Thread(target=self._scheduler_loop, args=(account,), daemon=True)
        scheduler.start()
        try:
            self._poll_loop(account)
        finally:
            self._stop.set()
            try:
                self.client.notify_stop(account.base_url, account.token)
            except Exception as exc:  # pragma: no cover - best effort for real runs
                LOG.warning("notify_stop failed: %s", exc)

    def _poll_loop(self, account: Account) -> None:
        get_updates_buf = self.store.get_poll_state()
        consecutive_errors = 0
        while not self._stop.is_set():
            try:
                response = self.client.get_updates(
                    account.base_url,
                    account.token,
                    get_updates_buf=get_updates_buf,
                    timeout_seconds=self.poll_timeout_seconds,
                )
                if response.get("ret") not in (None, 0) or response.get("errcode") not in (None, 0):
                    LOG.warning("getupdates API error: %s", response)
                    time.sleep(2)
                    continue
                new_buf = response.get("get_updates_buf")
                if new_buf:
                    get_updates_buf = str(new_buf)
                    self.store.save_poll_state(get_updates_buf)
                for message in response.get("msgs") or []:
                    self.handle_message(account, message)
                consecutive_errors = 0
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                consecutive_errors += 1
                LOG.exception("poll loop error: %s", exc)
                time.sleep(30 if consecutive_errors >= 3 else 2)

    def _scheduler_loop(self, account: Account) -> None:
        while not self._stop.is_set():
            try:
                self.handle_due_reminders(account)
            except Exception as exc:
                LOG.exception("scheduler error: %s", exc)
            self._stop.wait(self.schedule_interval_seconds)

    def handle_message(self, account: Account, message: Dict[str, Any]) -> None:
        from_user_id = str(message.get("from_user_id") or "").strip()
        if not from_user_id:
            return
        if not self.store.record_inbound_once(account.id, message):
            return

        text = extract_text(message)
        context_token = message.get("context_token")
        owner = self.store.get_owner()
        if not owner:
            owner = self.store.ensure_owner(from_user_id, context_token)
            LOG.info("owner bound to %s", from_user_id)
        if owner.weixin_user_id != from_user_id:
            if not self.ignore_unauthorized:
                self.client.send_text(account.base_url, account.token, from_user_id, "未授权。", context_token)
            return
        self.store.update_owner_context(from_user_id, context_token)

        if not text:
            return

        now_local = datetime.now(self.local_tz)
        try:
            history = self.store.list_conversation_messages(limit=8)
            self.store.append_conversation_message("user", text)
            decision = self.assistant.interpret(text, now_local, self.timezone_name, from_user_id, history=history)
        except Exception as exc:
            LOG.exception("assistant failed: %s", exc)
            self._log_model_tool(
                source="model",
                action="interpret",
                arguments={},
                message_text=text,
                status="error",
                error=str(exc),
            )
            self._send_text(account, from_user_id, "提醒解析失败：%s" % exc, context_token)
            return

        self._log_model_tool(
            source=decision.source,
            action=decision.action,
            arguments=decision.arguments,
            message_text=text,
            reply=decision.reply,
            status="decision",
        )
        try:
            reply = self.execute_decision(decision)
            if decision.action in ("create_reminder", "list_reminders", "cancel_reminder"):
                self._log_model_tool(
                    source="tool_execute",
                    action=decision.action,
                    arguments=decision.arguments,
                    message_text=text,
                    reply=reply,
                    status="success",
                    result=reply,
                )
        except Exception as exc:
            LOG.exception("tool execution failed: %s", exc)
            self._log_model_tool(
                source="tool_execute",
                action=decision.action,
                arguments=decision.arguments,
                message_text=text,
                status="error",
                error=str(exc),
            )
            self._send_text(account, from_user_id, "提醒执行失败：%s" % exc, context_token)
            return

        if reply:
            self._send_text(account, from_user_id, reply, context_token)
            self.store.append_conversation_message("assistant", reply)

    def execute_decision(self, decision: AssistantDecision) -> str:
        if decision.action == "create_reminder":
            return self._create_reminder(decision.arguments)
        if decision.action == "list_reminders":
            reminders = self.store.list_reminders(("pending",), limit=20)
            return format_reminder_list(reminders, self.local_tz)
        if decision.action == "cancel_reminder":
            return self._cancel_reminder(decision.arguments)
        return decision.reply or ""

    def _create_reminder(self, args: Dict[str, Any]) -> str:
        message = str(args.get("message") or "").strip()
        due_at = str(args.get("due_at") or "").strip()
        timezone_name = str(args.get("timezone") or self.timezone_name).strip() or self.timezone_name
        confidence = float(args.get("confidence") or 0)
        if not message or not due_at or confidence < 0.5:
            return "请告诉我要提醒的内容和具体时间。"
        local_tz = load_timezone(timezone_name)
        try:
            due_utc = parse_due_at(due_at, local_tz)
        except ValueError:
            return "我没能识别提醒时间，请换一种说法。"
        if due_utc <= utc_now():
            return "提醒时间已经过去了，请给一个未来时间。"
        reminder_id = self.store.create_reminder(message, due_utc, timezone_name)
        return "已创建提醒 #%s：%s，时间 %s。" % (
            reminder_id,
            message,
            format_local_time(to_utc_iso(due_utc), self.local_tz),
        )

    def _cancel_reminder(self, args: Dict[str, Any]) -> str:
        reminder_id = args.get("reminder_id")
        if reminder_id is None:
            reminder = self.store.get_last_pending_reminder()
            if not reminder:
                return "当前没有可取消的待提醒事项。"
            reminder_id = reminder.id
        try:
            parsed_id = int(reminder_id)
        except (TypeError, ValueError):
            return "请提供要取消的提醒编号。"
        if self.store.cancel_reminder(parsed_id):
            return "已取消提醒 #%s。" % parsed_id
        return "没有找到可取消的提醒 #%s。" % parsed_id

    def handle_due_reminders(self, account: Account) -> None:
        owner = self.store.get_owner()
        if not owner:
            return
        due = self.store.due_reminders(utc_now(), limit=25)
        for reminder in due:
            try:
                self._send_text(account, owner.weixin_user_id, "提醒：%s" % reminder.message, owner.context_token)
                self.store.mark_reminder_sent(reminder.id)
            except Exception as exc:
                self.store.mark_reminder_failed(reminder.id, str(exc))

    def _send_text(
        self,
        account: Account,
        to_user_id: str,
        text: str,
        context_token: Optional[str],
    ) -> None:
        self.client.send_text(
            account.base_url,
            account.token,
            to_user_id,
            text,
            context_token=context_token,
        )

    def _log_model_tool(
        self,
        source: str,
        action: str,
        arguments: Dict[str, Any],
        message_text: Optional[str],
        status: str,
        reply: Optional[str] = None,
        result: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        try:
            self.store.add_model_tool_log(
                source=source,
                model=getattr(self.assistant, "model", None),
                action=action,
                arguments=arguments,
                message_text=message_text,
                reply=reply,
                status=status,
                result=result,
                error=error,
            )
        except Exception as exc:  # pragma: no cover - logging must not break the bot
            LOG.warning("model tool log failed: %s", exc)
