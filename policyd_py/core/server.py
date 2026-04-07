"""Unix socket server that accepts Postfix policy delegation requests."""

"""Unix socket server that accepts Postfix policy delegation requests."""

import asyncio
import contextlib
import logging
import os

from policyd_py.config.settings import AppConfig
from policyd_py.core.models import PolicyRequest
from policyd_py.policy.handler import PolicyHandler

logger = logging.getLogger(__name__)


class PolicydServer:
    """Listens on a Unix socket and delegates each request to a PolicyHandler."""

    def __init__(self, config: AppConfig, handler: PolicyHandler):
        self.config = config
        self.handler = handler
        self.server: asyncio.AbstractServer | None = None
        self._connection_semaphore = asyncio.Semaphore(max(config.general.worker_count * 2, 1))

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        peer = writer.get_extra_info("peername")
        logger.debug("New connection from %s", peer)
        self.handler.on_connection_open()

        async with self._connection_semaphore:
            try:
                while True:
                    data_dict = {}
                    while True:
                        line = await asyncio.wait_for(reader.readline(), timeout=self.config.general.client_read_timeout)
                        if not line:
                            return

                        line = line.decode("utf-8", errors="ignore").strip()
                        if not line:
                            break

                        parts = line.split("=", 1)
                        if len(parts) == 2:
                            data_dict[parts[0].strip()] = parts[1].strip()

                    if not data_dict:
                        break

                    req = PolicyRequest.parse_from_dict(data_dict)
                    resp = await self.handler.handle(req)

                    writer.write(resp.format().encode("utf-8"))
                    await asyncio.wait_for(writer.drain(), timeout=self.config.general.client_write_timeout)

            except asyncio.CancelledError:
                pass
            except TimeoutError:
                logger.warning("Client %s timed out", peer)
            except Exception as exc:
                logger.error("Error handling postfix connection from %s: %s", peer, exc, exc_info=True)
            finally:
                self.handler.on_connection_close()
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()

    async def start(self):
        sock_path = self.config.general.socket
        if os.path.exists(sock_path):
            os.remove(sock_path)

        self.server = await asyncio.start_unix_server(self._handle_client, path=sock_path, limit=1024 * 16)

        try:
            os.chmod(sock_path, self.config.general.socket_permission)
        except OSError as exc:
            logger.warning("Failed to set permission on %s: %s", sock_path, exc)

        logger.info("Serving Unix socket on %s", sock_path)

    async def wait_closed(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            logger.info("Server shut down gracefully")
