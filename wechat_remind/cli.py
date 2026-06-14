import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

from .bot import ReminderBot, format_local_time, load_timezone
from .config import load_env_file
from .ilink_client import DEFAULT_BASE_URL, IlinkClient, IlinkError, monotonic_deadline
from .llm import DirectOpenAIClient, ReminderAssistant, normalize_openai_base_url
from .store import ReminderStore


def default_db_path() -> Path:
    raw = os.environ.get("WECHAT_REMIND_DB")
    if raw:
        path = Path(raw)
        if path.is_absolute():
            return path
        return Path(os.environ.get("WECHAT_REMIND_ENV_DIR", Path.cwd())) / path
    return Path(os.environ.get("WECHAT_REMIND_ENV_DIR", Path.cwd())) / "data" / "wechat_remind.sqlite3"


def default_timezone() -> str:
    return os.environ.get("BOT_TIMEZONE", "Asia/Shanghai")


def build_client() -> IlinkClient:
    return IlinkClient(bot_agent=os.environ.get("WECHAT_BOT_AGENT", "WechatRemind/0.1.0"))


def display_qr(data: str) -> None:
    try:
        import qrcode

        qr = qrcode.QRCode(border=1)
        qr.add_data(data)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
    except Exception:
        pass
    print(data)


def login_command(args: argparse.Namespace) -> int:
    store = ReminderStore(Path(args.db))
    client = build_client()
    current_base_url = DEFAULT_BASE_URL
    pending_verify_code: Optional[str] = None
    refresh_count = 0

    start = client.fetch_qr_code(local_tokens=store.local_tokens())
    qrcode = start.qrcode
    print("Use WeChat to scan this QR code:")
    display_qr(start.qrcode_url)

    deadline = monotonic_deadline(args.timeout)
    while time.monotonic() < deadline:
        status = client.poll_qr_status(qrcode, current_base_url, pending_verify_code)
        name = status.get("status")
        if name == "wait":
            print(".", end="", flush=True)
        elif name == "scaned":
            pending_verify_code = None
            print("\nScanned. Waiting for confirmation...")
        elif name == "need_verifycode":
            pending_verify_code = input("\nEnter the number shown in WeChat: ").strip()
        elif name == "verify_code_blocked":
            pending_verify_code = None
            print("\nVerify code blocked. Refreshing QR code...")
            refresh_count += 1
            if refresh_count > 3:
                raise IlinkError("QR verification failed too many times")
            start = client.fetch_qr_code(local_tokens=store.local_tokens())
            qrcode = start.qrcode
            display_qr(start.qrcode_url)
        elif name == "expired":
            print("\nQR expired. Refreshing QR code...")
            refresh_count += 1
            if refresh_count > 3:
                raise IlinkError("QR expired too many times")
            start = client.fetch_qr_code(local_tokens=store.local_tokens())
            qrcode = start.qrcode
            display_qr(start.qrcode_url)
        elif name == "scaned_but_redirect":
            redirect_host = status.get("redirect_host")
            if redirect_host:
                current_base_url = "https://" + str(redirect_host)
                print("\nRedirected to %s" % current_base_url)
        elif name == "binded_redirect":
            print("\nThis bot is already bound. Existing local credentials remain unchanged.")
            return 0
        elif name == "confirmed":
            account_id = status.get("ilink_bot_id")
            token = status.get("bot_token")
            if not account_id or not token:
                raise IlinkError("Login confirmed but token or bot id is missing")
            base_url = status.get("baseurl") or current_base_url
            store.save_account(str(account_id), str(token), str(base_url), status.get("ilink_user_id"))
            print("\nLogin saved for account %s" % account_id)
            return 0
        else:
            print("\nUnexpected login status: %s" % status)
        time.sleep(1)

    raise IlinkError("Timed out waiting for QR login")


def run_command(args: argparse.Namespace) -> int:
    logging.basicConfig(level=getattr(logging, args.log_level.upper()), format="%(asctime)s %(levelname)s %(message)s")
    store = ReminderStore(Path(args.db))
    assistant = ReminderAssistant.from_env()
    client = build_client()
    bot = ReminderBot(
        store=store,
        client=client,
        assistant=assistant,
        timezone_name=args.timezone,
        poll_timeout_seconds=args.poll_timeout,
        schedule_interval_seconds=args.schedule_interval,
    )
    print("wechat-remind is running. Press Ctrl+C to stop.")
    try:
        bot.run_forever()
    except KeyboardInterrupt:
        bot.stop()
        print("\nStopped.")
    return 0


def list_command(args: argparse.Namespace) -> int:
    store = ReminderStore(Path(args.db))
    tz = load_timezone(args.timezone)
    reminders = store.list_reminders(("pending",), limit=50)
    if not reminders:
        print("No pending reminders.")
        return 0
    for reminder in reminders:
        print("#%s %s %s" % (reminder.id, format_local_time(reminder.due_at_utc, tz), reminder.message))
    return 0


def cancel_command(args: argparse.Namespace) -> int:
    store = ReminderStore(Path(args.db))
    if store.cancel_reminder(args.reminder_id):
        print("Cancelled reminder #%s." % args.reminder_id)
        return 0
    print("No pending reminder #%s." % args.reminder_id)
    return 1


def _short(value: Optional[str], limit: int = 160) -> str:
    if not value:
        return ""
    text = str(value).replace("\r", " ").replace("\n", " ")
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def tool_logs_command(args: argparse.Namespace) -> int:
    store = ReminderStore(Path(args.db))
    tz = load_timezone(args.timezone)
    logs = store.list_model_tool_logs(limit=args.limit)
    if not logs:
        print("No model/tool logs.")
        return 0

    for item in logs:
        args_text = json.dumps(item.arguments, ensure_ascii=False, sort_keys=True)
        model = " model=%s" % item.model if item.model else ""
        print(
            "#%s %s %s %s %s%s"
            % (
                item.id,
                format_local_time(item.created_at, tz),
                item.status,
                item.source,
                item.action,
                model,
            )
        )
        if item.message_text:
            print("  message: %s" % _short(item.message_text))
        print("  args: %s" % _short(args_text, limit=240))
        if item.reply:
            print("  reply: %s" % _short(item.reply))
        if item.result:
            print("  result: %s" % _short(item.result))
        if item.error:
            print("  error: %s" % _short(item.error))
    return 0


def doctor_command(args: argparse.Namespace) -> int:
    store = ReminderStore(Path(args.db))
    account = store.get_account()
    owner = store.get_owner()
    api_key = os.environ.get("OPENAI_API_KEY")
    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com")
    model = os.environ.get("OPENAI_MODEL", "gpt-5.5")
    effort = os.environ.get("OPENAI_REASONING_EFFORT", "low")

    ok = True
    print("db: %s" % Path(args.db))
    print("account: %s" % ("ok" if account and account.token else "missing"))
    print("owner: %s" % ("ok" if owner else "missing"))
    print("openai_api_key: %s" % ("set" if api_key else "missing"))
    print("openai_base_url: %s" % normalize_openai_base_url(base_url))
    print("openai_model: %s" % model)

    if not account or not account.token:
        ok = False
    if not owner:
        ok = False
    if not api_key:
        ok = False

    sample = "10\u5206\u949f\u540e\u63d0\u9192\u6211\u559d\u6c34"
    decision = ReminderAssistant(client=object()).interpret(
        sample,
        __import__("datetime").datetime.now(load_timezone(args.timezone)),
        args.timezone,
        owner.weixin_user_id if owner else "owner@im.wechat",
    )
    parser_ok = decision.action == "create_reminder" and decision.arguments.get("message")
    print("local_parser: %s" % ("ok" if parser_ok else "failed"))
    if not parser_ok:
        ok = False

    if api_key and not args.skip_api:
        try:
            client = DirectOpenAIClient(api_key=api_key, base_url=base_url, timeout_seconds=60)
            response = client.responses.create(
                model=model,
                input="Reply with exactly: OK",
                store=False,
                reasoning={"effort": effort},
            )
            text = ReminderAssistant._extract_text(response)
            api_ok = text.strip() == "OK"
            print("responses_api: %s" % ("ok" if api_ok else "unexpected response"))
            if not api_ok:
                ok = False
        except Exception as exc:
            print("responses_api: failed (%s)" % str(exc)[:300])
            ok = False
    elif args.skip_api:
        print("responses_api: skipped")

    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wechat-remind")
    parser.add_argument("--db", default=str(default_db_path()), help="SQLite database path.")
    parser.add_argument("--timezone", default=default_timezone(), help="Local timezone.")

    subparsers = parser.add_subparsers(dest="command", required=True)

    login = subparsers.add_parser("login", help="Login by WeChat QR code.")
    login.add_argument("--timeout", type=float, default=480.0)
    login.set_defaults(func=login_command)

    run = subparsers.add_parser("run", help="Run the local bot.")
    run.add_argument("--poll-timeout", type=float, default=float(os.environ.get("WECHAT_POLL_TIMEOUT_SECONDS", "35")))
    run.add_argument("--schedule-interval", type=float, default=10.0)
    run.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "INFO"))
    run.set_defaults(func=run_command)

    list_parser = subparsers.add_parser("list", help="List pending reminders.")
    list_parser.set_defaults(func=list_command)

    cancel = subparsers.add_parser("cancel", help="Cancel a pending reminder.")
    cancel.add_argument("reminder_id", type=int)
    cancel.set_defaults(func=cancel_command)

    logs = subparsers.add_parser("tool-logs", help="Show recent model decisions and tool executions.")
    logs.add_argument("--limit", type=int, default=20)
    logs.set_defaults(func=tool_logs_command)

    doctor = subparsers.add_parser("doctor", help="Check local account, owner, parser, and Responses API config.")
    doctor.add_argument("--skip-api", action="store_true")
    doctor.set_defaults(func=doctor_command)
    return parser


def main(argv: Optional[list] = None) -> int:
    load_env_file(override=True)
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as exc:
        print("Error: %s" % exc, file=sys.stderr)
        return 1
