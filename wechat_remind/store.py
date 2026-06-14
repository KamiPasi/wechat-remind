import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


def from_iso(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass
class Account:
    id: str
    token: str
    base_url: str
    user_id: Optional[str]
    saved_at: str


@dataclass
class Owner:
    weixin_user_id: str
    context_token: Optional[str]
    last_seen_at: str


@dataclass
class Reminder:
    id: int
    message: str
    due_at_utc: str
    timezone: str
    status: str
    created_at: str
    sent_at: Optional[str]
    last_error: Optional[str]
    fail_count: int


@dataclass
class ConversationMessage:
    role: str
    content: str
    created_at: str


@dataclass
class ModelToolLog:
    id: int
    created_at: str
    source: str
    model: Optional[str]
    action: str
    arguments: Dict[str, Any]
    message_text: Optional[str]
    reply: Optional[str]
    status: str
    result: Optional[str]
    error: Optional[str]


class ReminderStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS account (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    id TEXT NOT NULL,
                    token TEXT NOT NULL,
                    base_url TEXT NOT NULL,
                    user_id TEXT,
                    saved_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS owner (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    weixin_user_id TEXT NOT NULL,
                    context_token TEXT,
                    last_seen_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS poll_state (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    get_updates_buf TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message TEXT NOT NULL,
                    due_at_utc TEXT NOT NULL,
                    timezone TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    sent_at TEXT,
                    last_error TEXT,
                    fail_count INTEGER NOT NULL DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_reminders_due
                    ON reminders(status, due_at_utc);

                CREATE TABLE IF NOT EXISTS inbound_messages (
                    account_id TEXT NOT NULL,
                    message_key TEXT NOT NULL,
                    processed_at TEXT NOT NULL,
                    PRIMARY KEY (account_id, message_key)
                );

                CREATE TABLE IF NOT EXISTS conversation_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS model_tool_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    source TEXT NOT NULL,
                    model TEXT,
                    action TEXT NOT NULL,
                    arguments_json TEXT NOT NULL,
                    message_text TEXT,
                    reply TEXT,
                    status TEXT NOT NULL,
                    result TEXT,
                    error TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_model_tool_logs_created
                    ON model_tool_logs(created_at DESC, id DESC);
                """
            )

    @staticmethod
    def _row_to_account(row: sqlite3.Row) -> Account:
        return Account(
            id=row["id"],
            token=row["token"],
            base_url=row["base_url"],
            user_id=row["user_id"],
            saved_at=row["saved_at"],
        )

    @staticmethod
    def _row_to_owner(row: sqlite3.Row) -> Owner:
        return Owner(
            weixin_user_id=row["weixin_user_id"],
            context_token=row["context_token"],
            last_seen_at=row["last_seen_at"],
        )

    @staticmethod
    def _row_to_reminder(row: sqlite3.Row) -> Reminder:
        return Reminder(
            id=int(row["id"]),
            message=row["message"],
            due_at_utc=row["due_at_utc"],
            timezone=row["timezone"],
            status=row["status"],
            created_at=row["created_at"],
            sent_at=row["sent_at"],
            last_error=row["last_error"],
            fail_count=int(row["fail_count"]),
        )

    @staticmethod
    def _row_to_model_tool_log(row: sqlite3.Row) -> ModelToolLog:
        try:
            arguments = json.loads(row["arguments_json"] or "{}")
        except (TypeError, ValueError):
            arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        return ModelToolLog(
            id=int(row["id"]),
            created_at=row["created_at"],
            source=row["source"],
            model=row["model"],
            action=row["action"],
            arguments=arguments,
            message_text=row["message_text"],
            reply=row["reply"],
            status=row["status"],
            result=row["result"],
            error=row["error"],
        )

    def save_account(
        self,
        account_id: str,
        token: str,
        base_url: str,
        user_id: Optional[str] = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO account
                    (singleton, id, token, base_url, user_id, saved_at)
                VALUES (1, ?, ?, ?, ?, ?)
                """,
                (account_id, token, base_url, user_id, to_utc_iso(utc_now())),
            )

    def get_account(self) -> Optional[Account]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM account WHERE singleton = 1").fetchone()
        return self._row_to_account(row) if row else None

    def local_tokens(self) -> List[str]:
        account = self.get_account()
        return [account.token] if account and account.token else []

    def get_owner(self) -> Optional[Owner]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM owner WHERE singleton = 1").fetchone()
        return self._row_to_owner(row) if row else None

    def ensure_owner(self, weixin_user_id: str, context_token: Optional[str]) -> Owner:
        existing = self.get_owner()
        if existing:
            return existing
        now = to_utc_iso(utc_now())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO owner
                    (singleton, weixin_user_id, context_token, last_seen_at)
                VALUES (1, ?, ?, ?)
                """,
                (weixin_user_id, context_token, now),
            )
        return Owner(weixin_user_id=weixin_user_id, context_token=context_token, last_seen_at=now)

    def update_owner_context(self, weixin_user_id: str, context_token: Optional[str]) -> None:
        owner = self.get_owner()
        if not owner or owner.weixin_user_id != weixin_user_id:
            return
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE owner
                SET context_token = COALESCE(?, context_token),
                    last_seen_at = ?
                WHERE singleton = 1
                """,
                (context_token, to_utc_iso(utc_now())),
            )

    def get_poll_state(self) -> str:
        with self._connect() as conn:
            row = conn.execute("SELECT get_updates_buf FROM poll_state WHERE singleton = 1").fetchone()
        return str(row["get_updates_buf"]) if row else ""

    def save_poll_state(self, get_updates_buf: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO poll_state
                    (singleton, get_updates_buf, updated_at)
                VALUES (1, ?, ?)
                """,
                (get_updates_buf, to_utc_iso(utc_now())),
            )

    def record_inbound_once(self, account_id: str, message: Dict[str, Any]) -> bool:
        parts = [
            str(message.get("message_id") or ""),
            str(message.get("seq") or ""),
            str(message.get("client_id") or ""),
        ]
        key = "|".join(part for part in parts if part)
        if not key:
            return True
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO inbound_messages
                        (account_id, message_key, processed_at)
                    VALUES (?, ?, ?)
                    """,
                    (account_id, key, to_utc_iso(utc_now())),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def create_reminder(self, message: str, due_at_utc: datetime, timezone_name: str) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO reminders
                    (message, due_at_utc, timezone, status, created_at)
                VALUES (?, ?, ?, 'pending', ?)
                """,
                (message, to_utc_iso(due_at_utc), timezone_name, to_utc_iso(utc_now())),
            )
            return int(cur.lastrowid)

    def list_reminders(
        self,
        statuses: Iterable[str] = ("pending",),
        limit: int = 20,
    ) -> List[Reminder]:
        status_list = list(statuses)
        if not status_list:
            return []
        placeholders = ",".join("?" for _ in status_list)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM reminders
                WHERE status IN (%s)
                ORDER BY due_at_utc ASC
                LIMIT ?
                """ % placeholders,
                tuple(status_list) + (limit,),
            ).fetchall()
        return [self._row_to_reminder(row) for row in rows]

    def get_last_pending_reminder(self) -> Optional[Reminder]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM reminders
                WHERE status = 'pending'
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """
            ).fetchone()
        return self._row_to_reminder(row) if row else None

    def cancel_reminder(self, reminder_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE reminders
                SET status = 'cancelled'
                WHERE id = ? AND status = 'pending'
                """,
                (reminder_id,),
            )
            return cur.rowcount > 0

    def due_reminders(self, now_utc: datetime, limit: int = 25) -> List[Reminder]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM reminders
                WHERE status = 'pending' AND due_at_utc <= ?
                ORDER BY due_at_utc ASC
                LIMIT ?
                """,
                (to_utc_iso(now_utc), limit),
            ).fetchall()
        return [self._row_to_reminder(row) for row in rows]

    def mark_reminder_sent(self, reminder_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE reminders
                SET status = 'sent',
                    sent_at = ?,
                    last_error = NULL
                WHERE id = ?
                """,
                (to_utc_iso(utc_now()), reminder_id),
            )

    def mark_reminder_failed(
        self,
        reminder_id: int,
        error: str,
        max_failures: int = 3,
    ) -> None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT fail_count FROM reminders WHERE id = ?",
                (reminder_id,),
            ).fetchone()
            fail_count = int(row["fail_count"]) + 1 if row else 1
            status = "failed" if fail_count >= max_failures else "pending"
            conn.execute(
                """
                UPDATE reminders
                SET status = ?,
                    fail_count = ?,
                    last_error = ?
                WHERE id = ?
                """,
                (status, fail_count, error[:1000], reminder_id),
            )

    def append_conversation_message(self, role: str, content: str) -> None:
        clean_role = role if role in ("user", "assistant") else "user"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO conversation_messages (role, content, created_at)
                VALUES (?, ?, ?)
                """,
                (clean_role, content, to_utc_iso(utc_now())),
            )
            conn.execute(
                """
                DELETE FROM conversation_messages
                WHERE id NOT IN (
                    SELECT id FROM conversation_messages
                    ORDER BY id DESC
                    LIMIT 20
                )
                """
            )

    def list_conversation_messages(self, limit: int = 8) -> List[ConversationMessage]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT role, content, created_at
                FROM conversation_messages
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        items = [
            ConversationMessage(
                role=row["role"],
                content=row["content"],
                created_at=row["created_at"],
            )
            for row in rows
        ]
        return list(reversed(items))

    def clear_conversation_messages(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM conversation_messages")

    def reset_conversation_if_idle(
        self,
        max_idle_seconds: int = 3600,
        now_utc: Optional[datetime] = None,
    ) -> bool:
        now = now_utc or utc_now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        cutoff = now.astimezone(timezone.utc) - timedelta(seconds=max_idle_seconds)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT created_at
                FROM conversation_messages
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
            if not row:
                return False
            if from_iso(row["created_at"]) > cutoff:
                return False
            conn.execute("DELETE FROM conversation_messages")
            return True

    def add_model_tool_log(
        self,
        source: str,
        action: str,
        arguments: Optional[Dict[str, Any]] = None,
        message_text: Optional[str] = None,
        reply: Optional[str] = None,
        status: str = "decision",
        result: Optional[str] = None,
        error: Optional[str] = None,
        model: Optional[str] = None,
    ) -> int:
        arguments_json = json.dumps(arguments or {}, ensure_ascii=False, sort_keys=True)
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO model_tool_logs
                    (created_at, source, model, action, arguments_json, message_text, reply, status, result, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    to_utc_iso(utc_now()),
                    source,
                    model,
                    action,
                    arguments_json,
                    message_text,
                    reply,
                    status,
                    result,
                    error[:2000] if error else None,
                ),
            )
            return int(cur.lastrowid)

    def list_model_tool_logs(self, limit: int = 20) -> List[ModelToolLog]:
        safe_limit = max(1, min(int(limit), 200))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM model_tool_logs
                ORDER BY id DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [self._row_to_model_tool_log(row) for row in rows]
