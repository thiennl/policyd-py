import asyncio
import logging
import os
import signal
from typing import List, Optional, Optional

from policyd_py.actions.manager import ActionManager, ManagerConfig
from policyd_py.actions.provider import ActionProvider
from policyd_py.actions.script_provider import ScriptActionProvider
from policyd_py.actions.webhook.provider import WebhookActionProvider
from policyd_py.config.manager import ConfigManager
from policyd_py.config.settings import AppConfig
from policyd_py.core.server import PolicydServer
from policyd_py.ldap.client import LDAPClient
from policyd_py.management.api_server import ManagementAPIServer
from policyd_py.management.service import ManagementService
from policyd_py.notification.notifier import Notifier
from policyd_py.policy.handler import PolicyHandler
from policyd_py.policy.matcher import PolicyMatcher
from policyd_py.ratelimit.limiter import RateLimiter
from policyd_py.storage.redis_client import RedisClient
from policyd_py.validation.validator import EmailValidator

try:
    import uvloop

    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except ImportError:
    pass


logger = logging.getLogger("policyd_main")


class Runtime:
    def __init__(self):
        self.server: PolicydServer | None = None
        self.redis_client: RedisClient | None = None
        self.ldap_client: LDAPClient | None = None
        self.handler: PolicyHandler | None = None
        self.notifier: Notifier | None = None
        self.action_manager: ActionManager | None = None
        self.config_manager: ConfigManager | None = None
        self.management_api: ManagementAPIServer | None = None
        self.rate_limiter: RateLimiter | None = None
        self.matcher: PolicyMatcher | None = None
        self.validator: EmailValidator | None = None

    async def shutdown(self, signal_name: str):
        logger.info("Received exit signal %s", signal_name)

        if self.management_api:
            await self.management_api.stop()
        if self.server:
            await self.server.wait_closed()
        if self.handler:
            await self.handler.stop()
        if self.notifier:
            await self.notifier.close()
        if self.redis_client:
            await self.redis_client.close()

        tasks = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
        for task in tasks:
            task.cancel()

        logger.info("Shutdown complete")


def _build_action_provider(name: str, config: AppConfig) -> ActionProvider | None:
    provider = name.strip().lower()
    if not provider:
        return None

    if provider == "webhook":
        wh = config.webhook
        if not wh.lock_url or not wh.unlock_url:
            logger.warning("External action provider 'webhook' skipped: lock_url/unlock_url is missing")
            return None
        return WebhookActionProvider(
            lock_url=wh.lock_url,
            unlock_url=wh.unlock_url,
            status_url=wh.status_url,
            lock_method=wh.lock_method,
            unlock_method=wh.unlock_method,
            status_method=wh.status_method,
            lock_body=wh.lock_body,
            unlock_body=wh.unlock_body,
            status_field=wh.status_field,
            auth_type=wh.auth_type,
            auth_token=wh.auth_token,
            auth_user=wh.auth_user,
            auth_pass=wh.auth_pass,
            headers=wh.headers,
            sign_requests=wh.sign_requests,
            sign_secret=wh.sign_secret,
            sign_header=wh.sign_header,
            timeout_seconds=wh.timeout_seconds,
            retry_count=wh.retry_count,
            retry_delay_seconds=wh.retry_delay_seconds,
        )

    if provider == "script":
        script_cfg = config.script
        if not script_cfg.lock_command or not script_cfg.unlock_command:
            logger.warning("External action provider 'script' skipped: lock_command/unlock_command is missing")
            return None
        return ScriptActionProvider(script_cfg)

    logger.warning("External action provider '%s' is not supported", name)
    return None


async def _build_action_manager(config: AppConfig) -> ActionManager | None:
    ext = config.external_action
    if not ext.enable or not ext.provider:
        return None

    provider_names: List[str] = [ext.provider, *ext.fallback_providers]
    providers: List[ActionProvider] = []

    for name in provider_names:
        provider = _build_action_provider(name, config)
        if provider is not None:
            providers.append(provider)

    if not providers:
        logger.warning("ExternalAction is enabled but no valid providers are available")
        return None

    manager = ActionManager(
        primary=providers[0],
        fallbacks=providers[1:],
        config=ManagerConfig(
            continue_on_error=ext.continue_on_error,
            async_execution=ext.async_execution,
            queue_size=ext.async_queue_size,
            workers=ext.async_workers,
        ),
    )
    await manager.start()
    logger.info("External action manager started: primary=%s fallbacks=%s", providers[0].name(), [p.name() for p in providers[1:]])
    return manager


def _build_ldap_client(config: AppConfig) -> Optional[LDAPClient]:
    if config.ldap.host and config.ldap.base_dn:
        return LDAPClient(config.ldap)
    return None


async def _apply_reloaded_config(runtime: Runtime, config: AppConfig) -> None:
    if runtime.handler is None or runtime.matcher is None or runtime.rate_limiter is None or runtime.validator is None:
        return

    old_action_manager = runtime.action_manager

    runtime.ldap_client = _build_ldap_client(config)
    runtime.action_manager = await _build_action_manager(config)

    await runtime.handler.refresh_runtime_dependencies(
        config=config,
        ldap_client=runtime.ldap_client,
        action_manager=runtime.action_manager,
    )

    runtime.matcher.config = config
    runtime.rate_limiter.config = config
    runtime.rate_limiter.use_lua = config.limits.ratelimit_use_lua
    await runtime.rate_limiter.init_scripts()

    runtime.validator.config = config.validation
    await runtime.validator.stop()
    await runtime.validator.start()

    if runtime.notifier:
        runtime.notifier.config = config

    if runtime.server:
        runtime.server.config = config

    await runtime.handler.reload_lists()

    if old_action_manager and old_action_manager is not runtime.action_manager:
        try:
            await old_action_manager.close()
        except Exception:
            pass


def _configure_logging(config: AppConfig) -> None:
    level = logging.DEBUG if config.general.debug else logging.INFO
    handlers = None
    if config.logging.log_file:
        handlers = [logging.FileHandler(config.logging.log_file)]
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=handlers,
    )


async def main():
    config_path = os.getenv("POLICYD_CONFIG", "/etc/policyd/config.ini")
    config = AppConfig.load(config_path)

    _configure_logging(config)

    runtime = Runtime()
    runtime.config_manager = ConfigManager(config_path, initial_config=config)

    runtime.redis_client = RedisClient(config.keydb)
    await runtime.redis_client.connect()
    await runtime.redis_client.enable_keyspace_notifications()

    runtime.rate_limiter = RateLimiter(runtime.redis_client, config)
    await runtime.rate_limiter.init_scripts()

    runtime.validator = EmailValidator(config.validation)
    runtime.matcher = PolicyMatcher(config, runtime.redis_client)
    runtime.notifier = Notifier(config)

    runtime.ldap_client = _build_ldap_client(config)
    runtime.action_manager = await _build_action_manager(config)

    runtime.handler = PolicyHandler(
        config=config,
        redis_client=runtime.redis_client,
        ratelimit_engine=runtime.rate_limiter,
        validator=runtime.validator,
        matcher=runtime.matcher,
        notifier=runtime.notifier,
        ldap_client=runtime.ldap_client,
        action_manager=runtime.action_manager,
    )
    await runtime.handler.start()

    runtime.server = PolicydServer(config, runtime.handler)

    if config.web.enable and runtime.config_manager:
        service = ManagementService(
            handler=runtime.handler,
            config_manager=runtime.config_manager,
            reload_callback=lambda cfg: _apply_reloaded_config(runtime, cfg),
        )
        runtime.management_api = ManagementAPIServer(config.web, service)
        await runtime.management_api.start()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(runtime.shutdown(s.name)))

    if hasattr(signal, "SIGHUP"):
        loop.add_signal_handler(signal.SIGHUP, lambda: asyncio.create_task(runtime.handler.reload_lists()))

    await runtime.server.start()
    logger.info("policyd-py is running")

    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Process interrupted manually")
