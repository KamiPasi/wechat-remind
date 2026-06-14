import base64
import json
import secrets
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional
from uuid import uuid4

from . import __version__


DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
DEFAULT_CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"
DEFAULT_BOT_TYPE = "3"

MESSAGE_TYPE_BOT = 2
MESSAGE_STATE_FINISH = 2
MESSAGE_ITEM_TEXT = 1


class IlinkError(RuntimeError):
    pass


class IlinkTimeout(IlinkError):
    pass


@dataclass
class QrLoginStart:
    qrcode: str
    qrcode_url: str


@dataclass
class QrLoginResult:
    connected: bool
    already_connected: bool = False
    account_id: Optional[str] = None
    token: Optional[str] = None
    base_url: Optional[str] = None
    user_id: Optional[str] = None
    message: str = ""


class IlinkClient:
    def __init__(
        self,
        bot_agent: str = "WechatRemind/0.1.0",
        channel_version: str = __version__,
        app_id: str = "bot",
        timeout_seconds: float = 15.0,
    ) -> None:
        self.bot_agent = bot_agent
        self.channel_version = channel_version
        self.app_id = app_id
        self.client_version = self._encode_client_version(channel_version)
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _encode_client_version(version: str) -> int:
        parts = []
        for chunk in version.split(".")[:3]:
            try:
                parts.append(int(chunk))
            except ValueError:
                parts.append(0)
        while len(parts) < 3:
            parts.append(0)
        major, minor, patch = parts[:3]
        return ((major & 0xFF) << 16) | ((minor & 0xFF) << 8) | (patch & 0xFF)

    @staticmethod
    def _random_wechat_uin() -> str:
        value = str(secrets.randbits(32)).encode("utf-8")
        return base64.b64encode(value).decode("ascii")

    @staticmethod
    def _join_url(base_url: str, endpoint: str) -> str:
        base = base_url if base_url.endswith("/") else base_url + "/"
        return urllib.parse.urljoin(base, endpoint)

    def _base_info(self) -> Dict[str, str]:
        return {
            "channel_version": self.channel_version,
            "bot_agent": self.bot_agent,
        }

    def _common_headers(self) -> Dict[str, str]:
        return {
            "iLink-App-Id": self.app_id,
            "iLink-App-ClientVersion": str(self.client_version),
        }

    def _post_headers(self, token: Optional[str] = None) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "X-WECHAT-UIN": self._random_wechat_uin(),
        }
        headers.update(self._common_headers())
        if token:
            headers["Authorization"] = "Bearer " + token.strip()
        return headers

    def _request_json(
        self,
        method: str,
        base_url: str,
        endpoint: str,
        body: Optional[Dict[str, Any]] = None,
        token: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        url = self._join_url(base_url, endpoint)
        timeout = self.timeout_seconds if timeout_seconds is None else timeout_seconds
        data = None
        headers = self._common_headers()
        if method.upper() == "POST":
            data = json.dumps(body or {}, ensure_ascii=False).encode("utf-8")
            headers = self._post_headers(token)

        request = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
        except (TimeoutError, socket.timeout) as exc:
            raise IlinkTimeout(str(exc)) from exc
        except urllib.error.URLError as exc:
            if isinstance(getattr(exc, "reason", None), socket.timeout):
                raise IlinkTimeout(str(exc)) from exc
            raise IlinkError(str(exc)) from exc
        except urllib.error.HTTPError as exc:
            raw_error = exc.read().decode("utf-8", errors="replace")
            raise IlinkError("HTTP %s from %s: %s" % (exc.code, url, raw_error)) from exc

        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise IlinkError("Invalid JSON from %s: %s" % (url, raw[:200])) from exc

    def _post_json(
        self,
        base_url: str,
        endpoint: str,
        body: Dict[str, Any],
        token: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        return self._request_json("POST", base_url, endpoint, body, token, timeout_seconds)

    def _get_json(
        self,
        base_url: str,
        endpoint: str,
        timeout_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        return self._request_json("GET", base_url, endpoint, None, None, timeout_seconds)

    def fetch_qr_code(
        self,
        local_tokens: Optional[Iterable[str]] = None,
        bot_type: str = DEFAULT_BOT_TYPE,
    ) -> QrLoginStart:
        response = self._post_json(
            DEFAULT_BASE_URL,
            "ilink/bot/get_bot_qrcode?bot_type=%s" % urllib.parse.quote(bot_type),
            {"local_token_list": list(local_tokens or [])},
        )
        qrcode = str(response.get("qrcode") or "")
        qrcode_url = str(response.get("qrcode_img_content") or "")
        if not qrcode or not qrcode_url:
            raise IlinkError("QR response missing qrcode or qrcode_img_content")
        return QrLoginStart(qrcode=qrcode, qrcode_url=qrcode_url)

    def poll_qr_status(
        self,
        qrcode: str,
        base_url: str = DEFAULT_BASE_URL,
        verify_code: Optional[str] = None,
        timeout_seconds: float = 35.0,
    ) -> Dict[str, Any]:
        endpoint = "ilink/bot/get_qrcode_status?qrcode=%s" % urllib.parse.quote(qrcode)
        if verify_code:
            endpoint += "&verify_code=%s" % urllib.parse.quote(verify_code)
        try:
            return self._get_json(base_url, endpoint, timeout_seconds)
        except IlinkTimeout:
            return {"status": "wait"}

    def get_updates(
        self,
        base_url: str,
        token: str,
        get_updates_buf: str = "",
        timeout_seconds: float = 35.0,
    ) -> Dict[str, Any]:
        try:
            return self._post_json(
                base_url,
                "ilink/bot/getupdates",
                {
                    "get_updates_buf": get_updates_buf or "",
                    "base_info": self._base_info(),
                },
                token=token,
                timeout_seconds=timeout_seconds,
            )
        except IlinkTimeout:
            return {"ret": 0, "msgs": [], "get_updates_buf": get_updates_buf}

    def build_text_message_request(
        self,
        to_user_id: str,
        text: str,
        context_token: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return {
            "msg": {
                "from_user_id": "",
                "to_user_id": to_user_id,
                "client_id": "wechat-remind-" + uuid4().hex,
                "message_type": MESSAGE_TYPE_BOT,
                "message_state": MESSAGE_STATE_FINISH,
                "item_list": [
                    {
                        "type": MESSAGE_ITEM_TEXT,
                        "text_item": {"text": text},
                    }
                ],
                "context_token": context_token or None,
                "run_id": run_id or None,
            }
        }

    def send_text(
        self,
        base_url: str,
        token: str,
        to_user_id: str,
        text: str,
        context_token: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> None:
        body = self.build_text_message_request(to_user_id, text, context_token, run_id)
        body["base_info"] = self._base_info()
        self._post_json(base_url, "ilink/bot/sendmessage", body, token=token)

    def notify_start(self, base_url: str, token: str) -> Dict[str, Any]:
        return self._post_json(
            base_url,
            "ilink/bot/msg/notifystart",
            {"base_info": self._base_info()},
            token=token,
            timeout_seconds=10.0,
        )

    def notify_stop(self, base_url: str, token: str) -> Dict[str, Any]:
        return self._post_json(
            base_url,
            "ilink/bot/msg/notifystop",
            {"base_info": self._base_info()},
            token=token,
            timeout_seconds=10.0,
        )


def monotonic_deadline(seconds: float) -> float:
    return time.monotonic() + seconds
