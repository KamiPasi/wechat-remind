import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from .config import load_env_file


BASE_SYSTEM_PROMPT = """You are a local WeChat reminder assistant for one owner.

Your job is to convert the owner's Chinese or English reminder instructions into tool calls.

Rules:
- Use create_reminder when the user asks to be reminded later.
- Use list_reminders when the user asks for current reminders.
- Use cancel_reminder when the user asks to cancel a reminder.
- The latest user message includes "Current local time" and "Timezone". Use them for all relative times like "10分钟后", "明天", "今晚", and "半小时后".
- due_at must be an ISO 8601 timestamp in the supplied timezone or with an explicit offset.
- Multi-turn clarification is allowed. If the latest message is only a missing detail, combine it with recent conversation history.
- If the reminder is still ambiguous after using history, do not guess. Reply briefly asking for the missing time or message.
- Keep natural language replies concise and in the user's language.

The line beginning with "Message:" is the exact owner message. For common
Chinese relative-time reminders, call create_reminder directly:
- "10分钟后提醒我喝水" means message="喝水", due_at=current time + 10 minutes.
- "半小时后提醒我出门" means message="出门", due_at=current time + 30 minutes.
- "明天上午9点提醒我开会" means message="开会", due_at=tomorrow 09:00 local time.
"""


TOOLS = [
    {
        "type": "function",
        "name": "create_reminder",
        "description": "Create a reminder for the owner.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "The reminder message to send when due.",
                },
                "due_at": {
                    "type": "string",
                    "description": "ISO 8601 due time in the supplied local timezone or with an explicit offset.",
                },
                "timezone": {
                    "type": "string",
                    "description": "IANA timezone name used to interpret due_at.",
                },
                "confidence": {
                    "type": "number",
                    "description": "0 to 1 confidence that the reminder is fully specified.",
                },
            },
            "required": ["message", "due_at", "timezone", "confidence"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "list_reminders",
        "description": "List the owner's pending reminders.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["pending"],
                    "description": "Reminder status to list.",
                }
            },
            "required": ["status"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "cancel_reminder",
        "description": "Cancel a pending reminder by id, or the most recently created pending reminder.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "reminder_id": {
                    "type": ["integer", "null"],
                    "description": "Reminder id to cancel. Null means cancel the latest pending reminder.",
                }
            },
            "required": ["reminder_id"],
            "additionalProperties": False,
        },
    },
]


@dataclass
class AssistantDecision:
    action: str
    arguments: Dict[str, Any]
    reply: Optional[str] = None
    source: str = "model"


class DirectResponsesResource:
    def __init__(self, api_key: str, base_url: str, timeout_seconds: float) -> None:
        self.api_key = api_key
        self.base_url = normalize_openai_base_url(base_url)
        self.timeout_seconds = timeout_seconds

    def create(self, **kwargs: Any) -> Dict[str, Any]:
        url = urllib.parse.urljoin(self.base_url + "/", "responses")
        payload = json.dumps(kwargs, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=payload,
            method="POST",
            headers={
                "Authorization": "Bearer " + self.api_key,
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError("Responses API HTTP %s: %s" % (exc.code, body[:1000])) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError("Responses API request failed: %s" % exc) from exc
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Responses API returned invalid JSON: %s" % raw[:1000]) from exc


class DirectOpenAIClient:
    def __init__(self, api_key: str, base_url: str, timeout_seconds: float = 60.0) -> None:
        self.responses = DirectResponsesResource(api_key, base_url, timeout_seconds)


def normalize_openai_base_url(raw: str) -> str:
    base = (raw or "https://api.openai.com").strip().rstrip("/")
    if base.endswith("/v1"):
        return base
    return base + "/v1"


class ReminderAssistant:
    def __init__(
        self,
        client: Optional[Any] = None,
        model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        custom_prompt_path: Optional[Path] = None,
    ) -> None:
        self.client = client
        self.model = model or os.environ.get("OPENAI_MODEL", "gpt-5.5")
        self.reasoning_effort = reasoning_effort or os.environ.get("OPENAI_REASONING_EFFORT", "low")
        self.custom_prompt_path = Path(custom_prompt_path) if custom_prompt_path else None

    @classmethod
    def from_env(cls) -> "ReminderAssistant":
        load_env_file(override=True)
        env_dir = Path(os.environ.get("WECHAT_REMIND_ENV_DIR", Path.cwd()))
        prompt_path = os.environ.get("BOT_SYSTEM_PROMPT_PATH")
        if prompt_path:
            path = Path(prompt_path)
            if not path.is_absolute():
                path = env_dir / path
        else:
            path = env_dir / "prompts" / "custom.md"
        return cls(custom_prompt_path=path)

    def _client(self) -> Any:
        if self.client is not None:
            return self.client
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")

        base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com")
        if os.environ.get("OPENAI_USE_SDK") == "1":
            from openai import OpenAI

            self.client = OpenAI(api_key=api_key, base_url=normalize_openai_base_url(base_url))
            return self.client

        timeout = float(os.environ.get("OPENAI_TIMEOUT_SECONDS", "60"))
        self.client = DirectOpenAIClient(api_key=api_key, base_url=base_url, timeout_seconds=timeout)
        return self.client

    def system_prompt(self) -> str:
        prompt = BASE_SYSTEM_PROMPT
        if self.custom_prompt_path and self.custom_prompt_path.exists():
            custom = self.custom_prompt_path.read_text(encoding="utf-8").strip()
            if custom:
                prompt += "\n\nOwner custom instructions:\n" + custom
        return prompt

    @staticmethod
    def _get(value: Any, key: str, default: Any = None) -> Any:
        if isinstance(value, dict):
            return value.get(key, default)
        return getattr(value, key, default)

    @classmethod
    def _extract_function_call(cls, response: Any) -> Optional[AssistantDecision]:
        output = cls._get(response, "output", []) or []
        for item in output:
            if cls._get(item, "type") != "function_call":
                continue
            name = cls._get(item, "name")
            raw_args = cls._get(item, "arguments", "{}")
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except (TypeError, ValueError):
                args = {}
            if name:
                return AssistantDecision(action=str(name), arguments=args, source="model")
        return None

    @classmethod
    def _extract_text(cls, response: Any) -> str:
        text = cls._get(response, "output_text")
        if text:
            return str(text)
        output = cls._get(response, "output", []) or []
        parts = []
        for item in output:
            if cls._get(item, "type") != "message":
                continue
            for content in cls._get(item, "content", []) or []:
                if cls._get(content, "type") in ("output_text", "text"):
                    content_text = cls._get(content, "text")
                    if content_text:
                        parts.append(str(content_text))
        return "\n".join(parts).strip()

    def interpret(
        self,
        message_text: str,
        now_local: datetime,
        timezone_name: str,
        owner_weixin_user_id: str,
        history: Optional[Iterable[Any]] = None,
    ) -> AssistantDecision:
        local = self._local_interpret(message_text, now_local, timezone_name)
        if local is not None:
            return local

        dynamic_context = (
            "Current local time: %s\n"
            "Timezone: %s\n"
            "Owner Weixin user id: %s\n"
            "Message: %s"
            % (now_local.isoformat(), timezone_name, owner_weixin_user_id, message_text)
        )
        input_items = [{"role": "system", "content": self.system_prompt()}]
        for item in history or []:
            role = self._get(item, "role", "user")
            content = self._get(item, "content", "")
            if role in ("user", "assistant") and content:
                input_items.append({"role": role, "content": str(content)})
        input_items.append({"role": "user", "content": dynamic_context})
        response = self._client().responses.create(
            model=self.model,
            input=input_items,
            tools=TOOLS,
            tool_choice="auto",
            reasoning={"effort": self.reasoning_effort},
            store=False,
        )
        function_call = self._extract_function_call(response)
        if function_call:
            return function_call
        reply = self._extract_text(response) or "我没有理解这条提醒指令。"
        return AssistantDecision(action="reply", arguments={}, reply=reply, source="model")

    @staticmethod
    def _clean_message(raw: str) -> str:
        return raw.strip(" \t\r\n，,。.!！：:")

    @classmethod
    def _local_interpret(
        cls,
        message_text: str,
        now_local: datetime,
        timezone_name: str,
    ) -> Optional[AssistantDecision]:
        text = message_text.strip()
        if not text:
            return None

        if "提醒" in text and any(word in text for word in ("列表", "清单", "有哪些", "待提醒")):
            return AssistantDecision(action="list_reminders", arguments={"status": "pending"}, source="local_parser")

        if any(word in text for word in ("取消", "删除")) and "提醒" in text:
            match = re.search(r"#?\s*(\d+)", text)
            reminder_id = int(match.group(1)) if match else None
            return AssistantDecision(
                action="cancel_reminder",
                arguments={"reminder_id": reminder_id},
                source="local_parser",
            )

        patterns = [
            (r"(?P<num>\d+)\s*(?:分钟|分)\s*后\s*提醒我?(?P<msg>.+)$", "minutes"),
            (r"(?P<num>\d+)\s*(?:小时|个小时)\s*后\s*提醒我?(?P<msg>.+)$", "hours"),
        ]
        for pattern, unit in patterns:
            match = re.search(pattern, text)
            if not match:
                continue
            number = int(match.group("num"))
            delta = timedelta(minutes=number) if unit == "minutes" else timedelta(hours=number)
            msg = cls._clean_message(match.group("msg"))
            if msg:
                return AssistantDecision(
                    action="create_reminder",
                    arguments={
                        "message": msg,
                        "due_at": (now_local + delta).isoformat(),
                        "timezone": timezone_name,
                        "confidence": 1.0,
                    },
                    source="local_parser",
                )

        match = re.search(r"半\s*小时\s*后\s*提醒我?(?P<msg>.+)$", text)
        if match:
            msg = cls._clean_message(match.group("msg"))
            if msg:
                return AssistantDecision(
                    action="create_reminder",
                    arguments={
                        "message": msg,
                        "due_at": (now_local + timedelta(minutes=30)).isoformat(),
                        "timezone": timezone_name,
                        "confidence": 1.0,
                    },
                    source="local_parser",
                )

        match = re.search(
            r"(?P<day>今天|明天)?\s*(?P<period>上午|早上|下午|晚上|中午)?\s*"
            r"(?P<hour>\d{1,2})\s*(?:点|:|：)\s*(?P<minute>\d{1,2})?\s*分?\s*"
            r"提醒我?(?P<msg>.+)$",
            text,
        )
        if match:
            hour = int(match.group("hour"))
            minute = int(match.group("minute") or 0)
            period = match.group("period") or ""
            if period in ("下午", "晚上") and hour < 12:
                hour += 12
            if period == "中午" and hour < 11:
                hour += 12
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                due = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if match.group("day") == "明天":
                    due += timedelta(days=1)
                elif due <= now_local:
                    due += timedelta(days=1)
                msg = cls._clean_message(match.group("msg"))
                if msg:
                    return AssistantDecision(
                        action="create_reminder",
                        arguments={
                            "message": msg,
                            "due_at": due.isoformat(),
                            "timezone": timezone_name,
                            "confidence": 1.0,
                        },
                        source="local_parser",
                    )

        return None
