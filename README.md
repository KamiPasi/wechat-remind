# wechat-remind

Local single-owner WeChat reminder bot.

This project intentionally implements only the small path needed for one local
WeChat reminder bot:

- one Weixin bot account
- one owner contact
- direct-message text reminders
- local SQLite storage
- OpenAI Responses API with `gpt-5.5`

The Weixin HTTP protocol is derived from the public
`@tencent-weixin/openclaw-weixin` implementation. It is not treated here as a
stable public API.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
copy .env.example .env
```

Edit `.env` with your model endpoint and API key. The app loads `.env`
automatically from the current directory or any parent directory. Values in
`.env` override same-named process environment variables for this app, so stale
shell/user-level OpenAI keys do not leak into a run.

Optional configuration:

```powershell
WECHAT_REMIND_ENV_FILE=Z:\wechat-remind\.env
```

`qrcode` is optional. If it is not installed, `login` prints the QR URL directly.

## Run

```powershell
python -m wechat_remind login
python -m wechat_remind run
```

The first private WeChat sender seen by the bot becomes the owner. Other
senders are ignored by default.

Useful local commands:

```powershell
python -m wechat_remind list
python -m wechat_remind cancel 1
python -m wechat_remind tool-logs --limit 20
```

`tool-logs` shows recent model decisions and local tool executions, including
the source (`model`, `local_parser`, or `tool_execute`), action, arguments,
reply/result, and errors.

## Custom prompt

Edit `prompts/custom.md` to add your own behavior rules. Keep reminder parsing
requirements in the built-in prompt unless you know you want to change tool
calling behavior.
