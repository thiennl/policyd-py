import asyncio
import hashlib
import hmac
import json
import logging
import time
from string import Template
from typing import Dict, Optional
from urllib import request as urllib_request

from policyd_py.actions.provider import ActionProvider

logger = logging.getLogger(__name__)


class WebhookActionProvider(ActionProvider):
    def __init__(
        self,
        lock_url: str,
        unlock_url: str,
        status_url: str = "",
        lock_method: str = "POST",
        unlock_method: str = "POST",
        status_method: str = "GET",
        lock_body: str = "",
        unlock_body: str = "",
        status_field: str = "",
        auth_type: str = "",
        auth_token: str = "",
        auth_user: str = "",
        auth_pass: str = "",
        headers: Optional[Dict[str, str]] = None,
        sign_requests: bool = False,
        sign_secret: str = "",
        sign_header: str = "X-Webhook-Signature",
        timeout_seconds: int = 10,
        retry_count: int = 3,
        retry_delay_seconds: int = 2,
    ):
        self.lock_url = lock_url
        self.unlock_url = unlock_url
        self.status_url = status_url
        self.lock_method = lock_method
        self.unlock_method = unlock_method
        self.status_method = status_method
        self.lock_body_tpl = Template(lock_body) if lock_body else None
        self.unlock_body_tpl = Template(unlock_body) if unlock_body else None
        self.status_field = status_field
        self.auth_type = auth_type
        self.auth_token = auth_token
        self.auth_user = auth_user
        self.auth_pass = auth_pass
        self.headers = headers or {}
        self.sign_requests = sign_requests
        self.sign_secret = sign_secret
        self.sign_header = sign_header
        self.timeout_seconds = timeout_seconds
        self.retry_count = retry_count
        self.retry_delay_seconds = retry_delay_seconds

    def name(self) -> str:
        return "webhook"

    async def close(self) -> None:
        return

    async def lock_account(self, email: str, reason: str) -> None:
        if not self.lock_url:
            raise RuntimeError("lock_url is not configured")

        payload = {
            "email": email,
            "reason": reason,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        body = self._render_body(self.lock_body_tpl, payload)
        await self._execute_with_retry(self.lock_method, self.lock_url, body)

    async def unlock_account(self, email: str) -> None:
        if not self.unlock_url:
            raise RuntimeError("unlock_url is not configured")

        payload = {
            "email": email,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        body = self._render_body(self.unlock_body_tpl, payload)
        await self._execute_with_retry(self.unlock_method, self.unlock_url, body)

    async def get_account_status(self, email: str) -> str:
        if not self.status_url:
            raise RuntimeError("status_url is not configured")

        url = self.status_url.replace("${email}", email)
        body = await asyncio.to_thread(self._http_request, self.status_method, url, "")

        if self.status_field:
            try:
                parsed = json.loads(body)
                value = parsed.get(self.status_field)
                if value is not None:
                    return str(value)
            except json.JSONDecodeError:
                pass

        return body

    def _render_body(self, tpl: Optional[Template], payload: Dict[str, str]) -> str:
        if tpl is None:
            return ""
        try:
            return tpl.safe_substitute(payload)
        except Exception:
            return json.dumps(payload)

    async def _execute_with_retry(self, method: str, url: str, body: str) -> None:
        last_error = None
        for attempt in range(self.retry_count + 1):
            if attempt > 0:
                await asyncio.sleep(self.retry_delay_seconds * attempt)
            try:
                await asyncio.to_thread(self._http_request, method, url, body)
                return
            except Exception as exc:
                last_error = exc
                logger.warning("Webhook call failed attempt=%s url=%s error=%s", attempt, url, exc)
        raise RuntimeError(f"webhook request failed after retries: {last_error}")

    def _http_request(self, method: str, url: str, body: str) -> str:
        data = body.encode("utf-8") if body else None
        req = urllib_request.Request(url=url, method=method.upper(), data=data)

        for key, value in self.headers.items():
            req.add_header(key, value)

        if data is not None and "content-type" not in {k.lower() for k in self.headers.keys()}:
            req.add_header("Content-Type", "application/json")

        if self.auth_type == "bearer" and self.auth_token:
            req.add_header("Authorization", f"Bearer {self.auth_token}")
        elif self.auth_type == "basic" and self.auth_user:
            token = (f"{self.auth_user}:{self.auth_pass}").encode("utf-8")
            import base64

            req.add_header("Authorization", "Basic " + base64.b64encode(token).decode("ascii"))
        elif self.auth_type == "api_key" and self.auth_token:
            req.add_header("X-API-Key", self.auth_token)

        if self.sign_requests and self.sign_secret and data is not None:
            digest = hmac.new(self.sign_secret.encode("utf-8"), data, hashlib.sha256).hexdigest()
            req.add_header(self.sign_header, digest)

        with urllib_request.urlopen(req, timeout=self.timeout_seconds) as response:
            if response.status < 200 or response.status >= 300:
                raise RuntimeError(f"webhook HTTP status {response.status}")
            return response.read().decode("utf-8", errors="ignore")
