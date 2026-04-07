"""HTTP REST API server for management operations (health, stats, config, user actions)."""

import base64
import hmac
import logging
from typing import Any, Dict

from policyd_py.config.settings import WebConfig
from policyd_py.management.service import ManagementService

logger = logging.getLogger(__name__)


class ManagementAPIServer:
    """Wraps an aiohttp web application with auth, CORS, and error middleware."""
    def __init__(self, config: WebConfig, service: ManagementService):
        self.config = config
        self.service = service
        self._runner = None
        self._site = None
        self._web = None

    async def start(self) -> None:
        try:
            from aiohttp import web
        except ImportError as exc:
            raise RuntimeError("aiohttp is required for management API") from exc

        self._web = web
        app = web.Application(middlewares=[self._error_middleware, self._auth_middleware, self._cors_middleware])

        app.router.add_get("/health", self._health)
        app.router.add_get("/api/v1/stats", self._stats)
        app.router.add_get("/api/v1/runtime/state/{email}", self._runtime_state)
        app.router.add_post("/api/v1/config/reload", self._reload_config)
        app.router.add_post("/api/v1/config/save", self._save_config)
        app.router.add_post("/api/v1/users/lock", self._lock_user)
        app.router.add_post("/api/v1/users/unlock", self._unlock_user)
        app.router.add_post("/api/v1/ratelimit/{email}/reset", self._reset_ratelimit)
        app.router.add_post("/api/v1/penalty/{email}/reset", self._reset_penalty)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host=self.config.host, port=self.config.port)
        await self._site.start()

        logger.info("Management API listening on %s:%s", self.config.host, self.config.port)

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
            self._site = None

    @property
    def _auth_required(self) -> bool:
        return bool(self.config.bearer_token or (self.config.username and self.config.password))

    @property
    def _allowed_origin(self) -> str:
        if not self.config.cors_origins:
            return "*"
        return self.config.cors_origins[0]

    @property
    def _error_middleware(self):
        assert self._web is not None
        web = self._web

        @web.middleware
        async def middleware(request, handler):
            try:
                return await handler(request)
            except web.HTTPException:
                raise
            except ValueError as exc:
                raise web.HTTPBadRequest(text=str(exc))
            except Exception as exc:
                logger.error("Management API error: %s", exc, exc_info=True)
                raise web.HTTPInternalServerError(text="internal error")

        return middleware

    @property
    def _auth_middleware(self):
        assert self._web is not None
        web = self._web

        @web.middleware
        async def middleware(request, handler):
            if request.path == "/health":
                return await handler(request)

            if not self._auth_required:
                return await handler(request)

            auth_header = request.headers.get("Authorization", "")
            if self.config.bearer_token and auth_header == f"Bearer {self.config.bearer_token}":
                return await handler(request)

            if self.config.username and self.config.password and auth_header.startswith("Basic "):
                encoded = auth_header.split(" ", 1)[1].strip()
                try:
                    raw = base64.b64decode(encoded).decode("utf-8")
                    username, password = raw.split(":", 1)
                except Exception:
                    raise web.HTTPUnauthorized(text="invalid authorization header")
                if hmac.compare_digest(username, self.config.username) and hmac.compare_digest(password, self.config.password):
                    return await handler(request)

            raise web.HTTPUnauthorized(text="unauthorized")

        return middleware

    @property
    def _cors_middleware(self):
        assert self._web is not None
        web = self._web

        @web.middleware
        async def middleware(request, handler):
            if request.method == "OPTIONS":
                resp = web.Response(status=204)
            else:
                resp = await handler(request)

            if self.config.cors_enabled:
                resp.headers["Access-Control-Allow-Origin"] = self._allowed_origin
                resp.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
                resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            return resp

        return middleware

    async def _json(self, payload: Dict[str, Any], status: int = 200):
        assert self._web is not None
        return self._web.json_response(payload, status=status)

    async def _require_json(self, request):
        if request.content_type != "application/json":
            raise ValueError("content-type must be application/json")
        return await request.json()

    async def _health(self, request):
        payload = await self.service.health()
        return await self._json(payload)

    async def _stats(self, request):
        payload = await self.service.stats()
        return await self._json(payload)

    async def _runtime_state(self, request):
        email = (request.match_info.get("email") or "").strip()
        if not email:
            raise ValueError("email is required")
        payload = await self.service.runtime_state(email)
        return await self._json(payload)

    async def _reload_config(self, request):
        payload = await self.service.reload_config()
        return await self._json(payload)

    async def _save_config(self, request):
        data = await self._require_json(request)
        payload = await self.service.save_config(content=data.get("content"), updates=data.get("updates"))
        return await self._json(payload)

    async def _lock_user(self, request):
        data = await self._require_json(request)
        email = (data.get("email") or "").strip()
        if not email:
            raise ValueError("email is required")
        reason = (data.get("reason") or "manual").strip()
        if not reason and data.get("duration") is not None:
            reason = f"manual duration={data.get('duration')}"
        payload = await self.service.lock_user(email, reason)
        return await self._json(payload)

    async def _unlock_user(self, request):
        data = await self._require_json(request)
        email = (data.get("email") or "").strip()
        if not email:
            raise ValueError("email is required")
        payload = await self.service.unlock_user(email)
        return await self._json(payload)

    async def _reset_ratelimit(self, request):
        email = (request.match_info.get("email") or "").strip()
        if not email:
            raise ValueError("email is required")
        payload = await self.service.reset_ratelimit(email)
        return await self._json(payload)

    async def _reset_penalty(self, request):
        email = (request.match_info.get("email") or "").strip()
        if not email:
            raise ValueError("email is required")
        payload = await self.service.reset_penalty(email)
        return await self._json(payload)
