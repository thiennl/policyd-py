import asyncio
import logging
from typing import List, Optional
from urllib.parse import urlparse

from policyd_py.config.settings import LDAPConfig

logger = logging.getLogger(__name__)


class LDAPClient:
    def __init__(self, config: LDAPConfig):
        self.config = config

    async def get_domains(self, ldap_uri: Optional[str] = None) -> List[str]:
        return await asyncio.to_thread(self._get_domains_sync, ldap_uri)

    def _get_domains_sync(self, ldap_uri: Optional[str] = None) -> List[str]:
        host = self.config.host
        port = self.config.port
        use_ssl = self.config.use_ssl
        base_dn = self.config.base_dn

        if ldap_uri:
            parsed = urlparse(ldap_uri)
            if parsed.hostname:
                host = parsed.hostname
            if parsed.port:
                port = parsed.port
            if parsed.scheme == "ldaps":
                use_ssl = True
            if parsed.path and parsed.path != "/":
                base_dn = parsed.path.lstrip("/")

        if not host or not base_dn:
            raise ValueError("LDAP host/base_dn are required for LDAP domain loading")

        try:
            from ldap3 import ALL, Connection, Server
        except ImportError as exc:
            raise RuntimeError("ldap3 is required for LDAP loading") from exc

        server = Server(host=host, port=port, use_ssl=use_ssl, connect_timeout=self.config.timeout, get_info=ALL)
        conn = Connection(
            server,
            user=self.config.bind_dn or None,
            password=self.config.bind_password or None,
            receive_timeout=self.config.timeout,
            auto_bind=True,
        )

        try:
            ok = conn.search(
                search_base=base_dn,
                search_filter=self.config.search_filter,
                attributes=[self.config.domain_attribute],
            )
            if not ok:
                return []

            results: List[str] = []
            for entry in conn.entries:
                value = getattr(entry, self.config.domain_attribute, None)
                if value is None:
                    continue
                text = str(value).strip().lower()
                if text:
                    results.append(text)
            return sorted(set(results))
        finally:
            conn.unbind()
