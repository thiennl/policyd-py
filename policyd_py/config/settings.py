"""Configuration models and INI parsing for policyd-py.

Uses pydantic models for typed configuration with a custom INI loader.
pydantic-settings is available for env-var overrides but the primary
source remains the INI file parsed by ``AppConfig.load()``.
"""

import configparser
import os
import re
from typing import Any, Dict, List

from pydantic import BaseModel, Field


class GeneralConfig(BaseModel):
    """General daemon settings."""

    socket: str = "/var/run/gopolicyd.sock"
    socket_permission: int = 0o666
    debug: bool = False
    worker_count: int = 200
    client_read_timeout: int = 30
    client_write_timeout: int = 30


class ActionsConfig(BaseModel):
    """Default Postfix policy actions."""

    success_action: str = "DUNNO"
    fail_action: str = "DEFER"
    db_error_action: str = "DUNNO"


class KeyDBConfig(BaseModel):
    """Redis / KeyDB connection settings."""

    hosts: List[str] = Field(default_factory=lambda: ["localhost:6379"])
    password: str = ""
    db: int = 0
    cluster_mode: bool = False
    connect_timeout: int = 5
    read_timeout: int = 3
    write_timeout: int = 3


class LocksConfig(BaseModel):
    """Account lock behaviour."""

    lock_duration: int = 600
    unlock_ttl_threshold: int = 11


class ValidationConfig(BaseModel):
    """Email validation and blacklist settings."""

    monitor_only: bool = False
    dns_timeout: int = 10
    allow_smtputf8: bool = True
    allow_quoted_local: bool = False
    allow_domain_literal: bool = False
    validate_sender_syntax: bool = False
    enable_blacklist: bool = False
    validate_recipient_syntax: bool = False
    validate_recipient_deliverability: bool = False
    validate_recipient: bool = False
    blacklist_auto_reload: bool = True
    blacklist_reload_interval: int = 300
    sender_blacklist_file: str = ""
    recipient_blacklist_file: str = ""
    domain_blacklist_file: str = ""


class LDAPConfig(BaseModel):
    """LDAP directory connection for domain loading."""

    host: str = ""
    port: int = 389
    use_ssl: bool = False
    bind_dn: str = ""
    bind_password: str = ""
    base_dn: str = ""
    search_filter: str = "(associatedDomain=*)"
    domain_attribute: str = "associatedDomain"
    timeout: int = 10
    refresh_interval: int = 60


class LoggingConfig(BaseModel):
    """Logging output configuration."""

    log_file: str = ""
    level: str = "info"


class TelegramConfig(BaseModel):
    """Telegram notification channel."""

    enable: bool = False
    bot_token: str = ""
    chat_id: str = ""


class DiscordConfig(BaseModel):
    """Discord notification channel."""

    enable: bool = False
    bot_token: str = ""
    channel_id: str = ""
    message_template: str = "{message}"


class EmailConfig(BaseModel):
    """SMTP email notification channel."""

    enable: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    from_addr: str = ""
    from_display_name: str = ""
    to: List[str] = Field(default_factory=list)
    subject: str = "Rate Limit Alert"
    template: str = "{message}"


class ExternalActionConfig(BaseModel):
    """Pluggable action provider (lock/unlock) settings."""

    enable: bool = False
    provider: str = ""
    fallback_providers: List[str] = Field(default_factory=list)
    continue_on_error: bool = False
    async_execution: bool = False
    async_queue_size: int = 1000
    async_workers: int = 10


class WebhookConfig(BaseModel):
    """HTTP webhook action provider."""

    lock_url: str = ""
    unlock_url: str = ""
    status_url: str = ""
    lock_method: str = "POST"
    unlock_method: str = "POST"
    status_method: str = "GET"
    lock_body: str = ""
    unlock_body: str = ""
    status_field: str = ""
    auth_type: str = ""
    auth_token: str = ""
    auth_user: str = ""
    auth_pass: str = ""
    headers: Dict[str, str] = Field(default_factory=dict)
    sign_requests: bool = False
    sign_secret: str = ""
    sign_header: str = "X-Webhook-Signature"
    timeout_seconds: int = 10
    retry_count: int = 3
    retry_delay_seconds: int = 2


class ScriptConfig(BaseModel):
    """Shell script action provider."""

    lock_command: str = ""
    unlock_command: str = ""
    status_command: str = ""
    notify_command: str = ""
    timeout_seconds: int = 10


class WebConfig(BaseModel):
    """Management HTTP API server."""

    enable: bool = False
    host: str = "127.0.0.1"
    port: int = 8080
    username: str = "admin"
    password: str = ""
    bearer_token: str = ""
    cors_enabled: bool = False
    cors_origins: List[str] = Field(default_factory=lambda: ["*"])


class PenaltyConfig(BaseModel):
    """Progressive penalty escalation."""

    enable: bool = False
    ttl: int = 86400
    steps: List[int] = Field(default_factory=list)


class AdaptiveLimitConfig(BaseModel):
    """Dynamic rate limit multipliers per sender class."""

    enable: bool = False
    authenticated_multiplier: float = 1.0
    unauthenticated_multiplier: float = 1.0
    local_sender_multiplier: float = 1.0
    external_sender_multiplier: float = 1.0
    trusted_multiplier: float = 1.0
    trusted_account_lists: List[str] = Field(default_factory=list)
    trusted_domain_lists: List[str] = Field(default_factory=list)
    trusted_ip_lists: List[str] = Field(default_factory=list)
    minimum_multiplier: float = 0.25
    maximum_multiplier: float = 4.0


class RateLimit(BaseModel):
    """A single rate limit entry parsed from quota strings."""

    count: int = 0
    duration: int = 0
    refill_rate: float = 0.0
    algorithm: str = "token_bucket"
    unlimited: bool = False


class PolicyRule(BaseModel):
    """A named policy routing rule."""

    name: str
    sender: str
    recipient: str
    quota: str


class LimitsConfig(BaseModel):
    """Rate limits, policies, and domain/account/IP lists."""

    enable_policyd: bool = True
    policy_check_state: str = "RCPT"
    ratelimit_use_lua: bool = True
    default_quota: List[RateLimit] = Field(default_factory=list)
    quotas: Dict[str, List[RateLimit]] = Field(default_factory=dict)
    policies: List[PolicyRule] = Field(default_factory=list)
    domain_lists: Dict[str, List[str]] = Field(default_factory=dict)
    account_lists: Dict[str, List[str]] = Field(default_factory=dict)
    ip_lists: Dict[str, List[str]] = Field(default_factory=dict)


class AppConfig(BaseModel):
    """Top-level application configuration loaded from an INI file."""

    general: GeneralConfig = Field(default_factory=GeneralConfig)
    actions: ActionsConfig = Field(default_factory=ActionsConfig)
    keydb: KeyDBConfig = Field(default_factory=KeyDBConfig)
    locks: LocksConfig = Field(default_factory=LocksConfig)
    penalty: PenaltyConfig = Field(default_factory=PenaltyConfig)
    adaptive: AdaptiveLimitConfig = Field(default_factory=AdaptiveLimitConfig)
    validation: ValidationConfig = Field(default_factory=ValidationConfig)
    ldap: LDAPConfig = Field(default_factory=LDAPConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    discord: DiscordConfig = Field(default_factory=DiscordConfig)
    email: EmailConfig = Field(default_factory=EmailConfig)
    external_action: ExternalActionConfig = Field(default_factory=ExternalActionConfig)
    webhook: WebhookConfig = Field(default_factory=WebhookConfig)
    script: ScriptConfig = Field(default_factory=ScriptConfig)
    web: WebConfig = Field(default_factory=WebConfig)

    @classmethod
    def load(cls, config_path: str) -> "AppConfig":
        """Load configuration from an INI file at *config_path*."""
        parser = configparser.ConfigParser()
        if os.path.exists(config_path):
            parser.read(config_path)

        cfg = cls()

        if "General" in parser:
            g = parser["General"]
            cfg.general.socket = g.get("socket", cfg.general.socket)
            cfg.general.socket_permission = int(g.get("socket_permission", oct(cfg.general.socket_permission)), 8)
            cfg.general.debug = g.getboolean("debug", cfg.general.debug)
            cfg.general.worker_count = g.getint("worker_count", cfg.general.worker_count)
            cfg.general.client_read_timeout = g.getint("client_read_timeout", cfg.general.client_read_timeout)
            cfg.general.client_write_timeout = g.getint("client_write_timeout", cfg.general.client_write_timeout)

        if "Actions" in parser:
            a = parser["Actions"]
            cfg.actions.success_action = a.get("success_action", cfg.actions.success_action)
            cfg.actions.fail_action = a.get("fail_action", cfg.actions.fail_action)
            cfg.actions.db_error_action = a.get("db_error_action", cfg.actions.db_error_action)

        if "KeyDB" in parser:
            k = parser["KeyDB"]
            hosts_str = k.get("hosts", "localhost:6379")
            cfg.keydb.hosts = [h.strip() for h in hosts_str.split(",") if h.strip()]
            cfg.keydb.password = k.get("password", cfg.keydb.password)
            cfg.keydb.db = k.getint("db", cfg.keydb.db)
            cfg.keydb.cluster_mode = k.getboolean("cluster_mode", cfg.keydb.cluster_mode)
            cfg.keydb.connect_timeout = k.getint("connect_timeout", cfg.keydb.connect_timeout)
            cfg.keydb.read_timeout = k.getint("read_timeout", cfg.keydb.read_timeout)
            cfg.keydb.write_timeout = k.getint("write_timeout", cfg.keydb.write_timeout)

        if "Locks" in parser:
            lk = parser["Locks"]
            cfg.locks.lock_duration = lk.getint("lock_duration", cfg.locks.lock_duration)
            cfg.locks.unlock_ttl_threshold = lk.getint("unlock_ttl_threshold", cfg.locks.unlock_ttl_threshold)

        if "Penalty" in parser:
            p = parser["Penalty"]
            cfg.penalty.enable = p.getboolean("enable", cfg.penalty.enable)
            cfg.penalty.ttl = parse_duration_to_seconds(p.get("ttl", str(cfg.penalty.ttl)))
            cfg.penalty.steps = _parse_duration_list(p.get("steps", ""))

        if "AdaptiveLimits" in parser:
            a = parser["AdaptiveLimits"]
            cfg.adaptive.enable = a.getboolean("enable", cfg.adaptive.enable)
            cfg.adaptive.authenticated_multiplier = a.getfloat(
                "authenticated_multiplier", cfg.adaptive.authenticated_multiplier
            )
            cfg.adaptive.unauthenticated_multiplier = a.getfloat(
                "unauthenticated_multiplier", cfg.adaptive.unauthenticated_multiplier
            )
            cfg.adaptive.local_sender_multiplier = a.getfloat(
                "local_sender_multiplier", cfg.adaptive.local_sender_multiplier
            )
            cfg.adaptive.external_sender_multiplier = a.getfloat(
                "external_sender_multiplier", cfg.adaptive.external_sender_multiplier
            )
            cfg.adaptive.trusted_multiplier = a.getfloat("trusted_multiplier", cfg.adaptive.trusted_multiplier)
            cfg.adaptive.trusted_account_lists = _parse_name_list(a.get("trusted_account_lists", ""))
            cfg.adaptive.trusted_domain_lists = _parse_name_list(a.get("trusted_domain_lists", ""))
            cfg.adaptive.trusted_ip_lists = _parse_name_list(a.get("trusted_ip_lists", ""))
            cfg.adaptive.minimum_multiplier = a.getfloat("minimum_multiplier", cfg.adaptive.minimum_multiplier)
            cfg.adaptive.maximum_multiplier = a.getfloat("maximum_multiplier", cfg.adaptive.maximum_multiplier)

        if "LDAP" in parser:
            l = parser["LDAP"]
            cfg.ldap.host = l.get("host", cfg.ldap.host)
            cfg.ldap.port = l.getint("port", cfg.ldap.port)
            cfg.ldap.use_ssl = l.getboolean("use_ssl", cfg.ldap.use_ssl)
            cfg.ldap.bind_dn = l.get("bind_dn", cfg.ldap.bind_dn)
            cfg.ldap.bind_password = l.get("bind_password", cfg.ldap.bind_password)
            cfg.ldap.base_dn = l.get("base_dn", cfg.ldap.base_dn)
            cfg.ldap.search_filter = l.get("search_filter", cfg.ldap.search_filter)
            cfg.ldap.domain_attribute = l.get("domain_attribute", cfg.ldap.domain_attribute)
            cfg.ldap.timeout = l.getint("timeout", cfg.ldap.timeout)
            cfg.ldap.refresh_interval = l.getint("refresh_interval", cfg.ldap.refresh_interval)

        if "Logging" in parser:
            lg = parser["Logging"]
            cfg.logging.log_file = lg.get("log_file", cfg.logging.log_file)
            cfg.logging.level = lg.get("level", cfg.logging.level)

        if "EmailValidation" in parser:
            v = parser["EmailValidation"]
            cfg.validation.monitor_only = v.getboolean("monitor_only", cfg.validation.monitor_only)
            cfg.validation.dns_timeout = v.getint("dns_timeout", cfg.validation.dns_timeout)
            cfg.validation.allow_smtputf8 = v.getboolean("allow_smtputf8", cfg.validation.allow_smtputf8)
            cfg.validation.allow_quoted_local = v.getboolean("allow_quoted_local", cfg.validation.allow_quoted_local)
            cfg.validation.allow_domain_literal = v.getboolean("allow_domain_literal", cfg.validation.allow_domain_literal)
            cfg.validation.validate_sender_syntax = v.getboolean("validate_sender_syntax", cfg.validation.validate_sender_syntax)
            cfg.validation.enable_blacklist = v.getboolean("enable_blacklist", cfg.validation.enable_blacklist)
            cfg.validation.validate_recipient_syntax = v.getboolean("validate_recipient_syntax", cfg.validation.validate_recipient_syntax)
            cfg.validation.validate_recipient_deliverability = v.getboolean(
                "validate_recipient_deliverability", cfg.validation.validate_recipient_deliverability
            )
            cfg.validation.validate_recipient = v.getboolean("validate_recipient", cfg.validation.validate_recipient)
            cfg.validation.blacklist_auto_reload = v.getboolean("blacklist_auto_reload", cfg.validation.blacklist_auto_reload)
            cfg.validation.blacklist_reload_interval = v.getint("blacklist_reload_interval", cfg.validation.blacklist_reload_interval)
            cfg.validation.sender_blacklist_file = v.get("sender_blacklist_file", cfg.validation.sender_blacklist_file)
            cfg.validation.recipient_blacklist_file = v.get("recipient_blacklist_file", cfg.validation.recipient_blacklist_file)
            cfg.validation.domain_blacklist_file = v.get("domain_blacklist_file", cfg.validation.domain_blacklist_file)

        if "Telegram" in parser:
            t = parser["Telegram"]
            cfg.telegram.enable = t.getboolean("enable", cfg.telegram.enable)
            cfg.telegram.bot_token = t.get("bot_token", cfg.telegram.bot_token)
            cfg.telegram.chat_id = t.get("chat_id", cfg.telegram.chat_id)

        if "Discord" in parser:
            d = parser["Discord"]
            cfg.discord.enable = d.getboolean("enable", cfg.discord.enable)
            cfg.discord.bot_token = d.get("bot_token", cfg.discord.bot_token)
            cfg.discord.channel_id = d.get("channel_id", cfg.discord.channel_id)
            cfg.discord.message_template = d.get("message_template", cfg.discord.message_template)

        if "Email" in parser:
            e = parser["Email"]
            cfg.email.enable = e.getboolean("enable", cfg.email.enable)
            cfg.email.smtp_host = e.get("smtp_host", cfg.email.smtp_host)
            cfg.email.smtp_port = e.getint("smtp_port", cfg.email.smtp_port)
            cfg.email.smtp_user = e.get("smtp_user", cfg.email.smtp_user)
            cfg.email.smtp_password = e.get("smtp_password", cfg.email.smtp_password)
            cfg.email.from_addr = e.get("from", cfg.email.from_addr)
            cfg.email.from_display_name = e.get("from_display_name", cfg.email.from_display_name)
            cfg.email.to = [x.strip() for x in e.get("to", "").split(",") if x.strip()]
            cfg.email.subject = e.get("subject", cfg.email.subject)
            cfg.email.template = e.get("template", cfg.email.template)

        if "ExternalAction" in parser:
            ea = parser["ExternalAction"]
            cfg.external_action.enable = ea.getboolean("enable", cfg.external_action.enable)
            cfg.external_action.provider = ea.get("provider", cfg.external_action.provider)
            cfg.external_action.fallback_providers = [
                x.strip() for x in ea.get("fallback_providers", "").split(",") if x.strip()
            ]
            cfg.external_action.continue_on_error = ea.getboolean("continue_on_error", cfg.external_action.continue_on_error)
            cfg.external_action.async_execution = ea.getboolean("async_execution", cfg.external_action.async_execution)
            cfg.external_action.async_queue_size = ea.getint("async_queue_size", cfg.external_action.async_queue_size)
            cfg.external_action.async_workers = ea.getint("async_workers", cfg.external_action.async_workers)

        if "Webhook" in parser:
            w = parser["Webhook"]
            cfg.webhook.lock_url = w.get("lock_url", cfg.webhook.lock_url)
            cfg.webhook.unlock_url = w.get("unlock_url", cfg.webhook.unlock_url)
            cfg.webhook.status_url = w.get("status_url", cfg.webhook.status_url)
            cfg.webhook.lock_method = w.get("lock_method", cfg.webhook.lock_method)
            cfg.webhook.unlock_method = w.get("unlock_method", cfg.webhook.unlock_method)
            cfg.webhook.status_method = w.get("status_method", cfg.webhook.status_method)
            cfg.webhook.lock_body = w.get("lock_body", cfg.webhook.lock_body)
            cfg.webhook.unlock_body = w.get("unlock_body", cfg.webhook.unlock_body)
            cfg.webhook.status_field = w.get("status_field", cfg.webhook.status_field)
            cfg.webhook.auth_type = w.get("auth_type", cfg.webhook.auth_type)
            cfg.webhook.auth_token = w.get("auth_token", cfg.webhook.auth_token)
            cfg.webhook.auth_user = w.get("auth_user", cfg.webhook.auth_user)
            cfg.webhook.auth_pass = w.get("auth_pass", cfg.webhook.auth_pass)
            cfg.webhook.headers = _parse_headers(w.get("headers", ""))
            cfg.webhook.sign_requests = w.getboolean("sign_requests", cfg.webhook.sign_requests)
            cfg.webhook.sign_secret = w.get("sign_secret", cfg.webhook.sign_secret)
            cfg.webhook.sign_header = w.get("sign_header", cfg.webhook.sign_header)
            cfg.webhook.timeout_seconds = w.getint("timeout_seconds", cfg.webhook.timeout_seconds)
            cfg.webhook.retry_count = w.getint("retry_count", cfg.webhook.retry_count)
            cfg.webhook.retry_delay_seconds = w.getint("retry_delay_seconds", cfg.webhook.retry_delay_seconds)

        if "Script" in parser:
            s = parser["Script"]
            cfg.script.lock_command = s.get("lock_command", cfg.script.lock_command)
            cfg.script.unlock_command = s.get("unlock_command", cfg.script.unlock_command)
            cfg.script.status_command = s.get("status_command", cfg.script.status_command)
            cfg.script.notify_command = s.get("notify_command", cfg.script.notify_command)
            cfg.script.timeout_seconds = s.getint("timeout_seconds", cfg.script.timeout_seconds)

        if "Web" in parser:
            wb = parser["Web"]
            cfg.web.enable = wb.getboolean("enable", cfg.web.enable)
            cfg.web.host = wb.get("host", cfg.web.host)
            cfg.web.port = wb.getint("port", cfg.web.port)
            cfg.web.username = wb.get("username", cfg.web.username)
            cfg.web.password = wb.get("password", cfg.web.password)
            cfg.web.bearer_token = wb.get("bearer_token", cfg.web.bearer_token)
            cfg.web.cors_enabled = wb.getboolean("cors_enabled", cfg.web.cors_enabled)
            cfg.web.cors_origins = [x.strip() for x in wb.get("cors_origins", "*").split(",") if x.strip()]

        cfg.limits = _parse_limits(parser)
        return cfg


def _parse_limits(parser: configparser.ConfigParser) -> LimitsConfig:
    """Parse Limits-related sections from the INI parser."""
    limits = LimitsConfig()

    if "Limits" in parser:
        section = parser["Limits"]
        limits.enable_policyd = section.getboolean("enable_policyd", limits.enable_policyd)
        limits.policy_check_state = section.get("policy_check_state", limits.policy_check_state)
        limits.ratelimit_use_lua = section.getboolean("ratelimit_use_lua", limits.ratelimit_use_lua)
        limits.default_quota = parse_rate_limits(section.get("default_quota", "10/1h:fixed_window"))

    if "DomainLists" in parser:
        for key, value in parser["DomainLists"].items():
            limits.domain_lists[key] = _parse_list_value(value)

    if "AccountLists" in parser:
        for key, value in parser["AccountLists"].items():
            limits.account_lists[key] = _parse_list_value(value)

    if "IPLists" in parser:
        for key, value in parser["IPLists"].items():
            limits.ip_lists[key] = _parse_list_value(value)

    if "Quotas" in parser:
        for key, value in parser["Quotas"].items():
            limits.quotas[key] = parse_rate_limits(value)

    if "Policies" in parser:
        for name, value in parser["Policies"].items():
            parts = [p.strip() for p in value.split(":")]
            if len(parts) != 3:
                continue
            limits.policies.append(PolicyRule(name=name, sender=parts[0], recipient=parts[1], quota=parts[2]))

    return limits


# ---------------------------------------------------------------------------
# Standalone parsing helpers (also used by config_manager for round-trip save)
# ---------------------------------------------------------------------------


def parse_rate_limits(value: str) -> List[RateLimit]:
    """Parse a quota string like ``100/1h:fixed_window,unlimited``."""
    if not value:
        return []

    results: List[RateLimit] = []
    for part in [x.strip() for x in value.split(",") if x.strip()]:
        if part.lower() == "unlimited":
            results.append(RateLimit(unlimited=True, algorithm="token_bucket"))
            continue

        algorithm = "token_bucket"
        base = part
        if part.endswith(":sliding_window_counter"):
            algorithm = "sliding_window_counter"
            base = part[: -len(":sliding_window_counter")]
        elif part.endswith(":sliding_window"):
            algorithm = "sliding_window_counter"
            base = part[: -len(":sliding_window")]
        elif part.endswith(":fixed_window"):
            algorithm = "fixed_window"
            base = part[: -len(":fixed_window")]
        elif part.endswith(":token_bucket"):
            algorithm = "token_bucket"
            base = part[: -len(":token_bucket")]

        m = re.match(r"^(\d+)\/([^:]+)(?::([^\/]+)\/([^\/]+))?$", base)
        if m:
            count = int(m.group(1))
            duration = parse_duration_to_seconds(m.group(2))
            refill_rate = 0.0
            if algorithm == "token_bucket":
                if m.group(3) and m.group(4):
                    refill_tokens = float(m.group(3))
                    refill_duration = parse_duration_to_seconds(m.group(4))
                    refill_rate = refill_tokens / float(refill_duration) if refill_duration > 0 else 0.0
                else:
                    refill_rate = float(count) / float(duration) if duration > 0 else 0.0
            results.append(RateLimit(count=count, duration=duration, refill_rate=refill_rate, algorithm=algorithm))
            continue

        sub = base.split(":")
        if len(sub) >= 2 and sub[0].isdigit() and sub[1].isdigit():
            count = int(sub[0])
            duration = int(sub[1])
            refill_rate = 0.0
            if algorithm == "token_bucket":
                if len(sub) >= 3 and sub[2]:
                    refill_rate = float(sub[2])
                else:
                    refill_rate = float(count) / float(duration) if duration > 0 else 0.0
            results.append(RateLimit(count=count, duration=duration, refill_rate=refill_rate, algorithm=algorithm))

    return results


def parse_duration_to_seconds(value: str) -> int:
    """Convert a duration string (``1d``, ``2h``, ``30m``, ``60s``, or plain int) to seconds."""
    value = value.strip().lower()
    if not value:
        return 0

    if value.isdigit():
        return int(value)
    if value.endswith("d") and value[:-1].isdigit():
        return int(value[:-1]) * 86400
    if value.endswith("h") and value[:-1].isdigit():
        return int(value[:-1]) * 3600
    if value.endswith("m") and value[:-1].isdigit():
        return int(value[:-1]) * 60
    if value.endswith("s") and value[:-1].isdigit():
        return int(value[:-1])

    raise ValueError(f"invalid duration {value}")


def _parse_list_value(value: str) -> List[str]:
    """Parse a comma-separated list, supporting ``ldap://`` and ``file://`` prefixes."""
    raw = value.strip()
    if not raw:
        return []

    if raw.startswith("ldap://"):
        return ["__LDAP__:" + raw]

    if raw.startswith("file://"):
        path = raw[len("file://") :]
        return _load_list_from_file(path)

    return [x.strip().lower() for x in raw.split(",") if x.strip()]


def _load_list_from_file(path: str) -> List[str]:
    """Read a newline-separated list from *path*, skipping blanks and comments."""
    if not os.path.exists(path):
        return []

    items: List[str] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            items.append(line.lower())
    return items


def _parse_duration_list(value: str) -> List[int]:
    if not value:
        return []
    return [parse_duration_to_seconds(x) for x in value.split(",") if x.strip()]


def _parse_name_list(value: str) -> List[str]:
    if not value:
        return []
    return [x.strip() for x in value.split(",") if x.strip()]


def _parse_headers(value: str) -> Dict[str, str]:
    if not value:
        return {}

    headers: Dict[str, str] = {}
    for item in [x.strip() for x in value.split(",") if x.strip()]:
        if ":" not in item:
            continue
        key, val = item.split(":", 1)
        key = key.strip()
        val = val.strip()
        if key:
            headers[key] = val
    return headers
