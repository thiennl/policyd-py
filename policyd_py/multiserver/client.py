import base64
import json
import time
from typing import Any, Dict, Optional
from urllib import request as urllib_request

from policyd_py.multiserver.models import ServerInfo, ServerStatus


class Client:
    def __init__(self, timeout_seconds: int = 10):
        self.timeout_seconds = timeout_seconds or 10

    def _build_url(self, server: ServerInfo, endpoint: str) -> str:
        return f"http://{server.host}:{server.port}{endpoint}"

    def _auth_header(self, server: ServerInfo) -> str:
        raw = f"{server.username}:{server.password}".encode("utf-8")
        return "Basic " + base64.b64encode(raw).decode("ascii")

    def _request(self, server: ServerInfo, method: str, endpoint: str, payload: Optional[Dict[str, Any]] = None) -> Any:
        url = self._build_url(server, endpoint)
        data = json.dumps(payload).encode("utf-8") if payload is not None else None

        req = urllib_request.Request(url=url, method=method, data=data)
        if server.username:
            req.add_header("Authorization", self._auth_header(server))
        if payload is not None:
            req.add_header("Content-Type", "application/json")

        with urllib_request.urlopen(req, timeout=self.timeout_seconds) as response:
            body = response.read().decode("utf-8", errors="ignore")
            if not body:
                return {}
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                return {"raw": body}

    def get_stats(self, server: ServerInfo) -> Dict[str, Any]:
        return self._request(server, "GET", "/api/v1/stats")

    def health_check(self, server: ServerInfo) -> ServerStatus:
        start = time.time()
        try:
            self._request(server, "GET", "/health")
            return ServerStatus(
                server_id=server.id,
                server_name=server.name,
                online=True,
                last_check=time.time(),
                response_time_ms=(time.time() - start) * 1000,
            )
        except Exception as exc:
            return ServerStatus(
                server_id=server.id,
                server_name=server.name,
                online=False,
                last_check=time.time(),
                response_time_ms=(time.time() - start) * 1000,
                error=str(exc),
            )

    def reload_config(self, server: ServerInfo) -> None:
        self._request(server, "POST", "/api/v1/config/reload")

    def save_config(self, server: ServerInfo) -> None:
        self._request(server, "POST", "/api/v1/config/save")

    def lock_user(self, server: ServerInfo, email: str, reason: str = "manual") -> None:
        self._request(server, "POST", "/api/v1/users/lock", {"email": email, "reason": reason})

    def unlock_user(self, server: ServerInfo, email: str) -> None:
        self._request(server, "POST", "/api/v1/users/unlock", {"email": email})

    def reset_ratelimit(self, server: ServerInfo, email: str) -> None:
        self._request(server, "POST", f"/api/v1/ratelimit/{email}/reset")

    def reset_penalty(self, server: ServerInfo, email: str) -> None:
        self._request(server, "POST", f"/api/v1/penalty/{email}/reset")

    def get_runtime_state(self, server: ServerInfo, email: str) -> Dict[str, Any]:
        return self._request(server, "GET", f"/api/v1/runtime/state/{email}")

    def execute_action(self, server: ServerInfo, method: str, endpoint: str, payload: Optional[Dict[str, Any]] = None):
        return self._request(server, method.upper(), endpoint, payload)
