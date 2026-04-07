import json
import os
import threading
import time
import uuid
from dataclasses import asdict
from typing import Dict, List

from policyd_py.multiserver.models import ServerInfo


class Registry:
    def __init__(self, file_path: str, auto_save: bool = True):
        self._lock = threading.RLock()
        self.servers: Dict[str, ServerInfo] = {}
        self.file_path = file_path
        self.auto_save = auto_save
        if file_path and os.path.exists(file_path):
            self.load()

    def add_server(self, server: ServerInfo) -> ServerInfo:
        with self._lock:
            if not server.id:
                server.id = str(uuid.uuid4())
            now = time.time()
            server.created_at = now
            server.updated_at = now

            if not server.name:
                raise ValueError("server name is required")
            if not server.host:
                raise ValueError("server host is required")
            if not server.port:
                server.port = 8080

            self.servers[server.id] = server
            if self.auto_save:
                self.save()
            return server

    def update_server(self, server: ServerInfo) -> None:
        with self._lock:
            if server.id not in self.servers:
                raise KeyError(f"server not found: {server.id}")
            server.updated_at = time.time()
            self.servers[server.id] = server
            if self.auto_save:
                self.save()

    def remove_server(self, server_id: str) -> None:
        with self._lock:
            if server_id not in self.servers:
                raise KeyError(f"server not found: {server_id}")
            del self.servers[server_id]
            if self.auto_save:
                self.save()

    def get_server(self, server_id: str) -> ServerInfo:
        with self._lock:
            if server_id not in self.servers:
                raise KeyError(f"server not found: {server_id}")
            return ServerInfo(**asdict(self.servers[server_id]))

    def get_all_servers(self) -> List[ServerInfo]:
        with self._lock:
            return [ServerInfo(**asdict(s)) for s in self.servers.values()]

    def get_enabled_servers(self) -> List[ServerInfo]:
        with self._lock:
            return [ServerInfo(**asdict(s)) for s in self.servers.values() if s.enabled]

    def get_servers_by_tag(self, tag: str) -> List[ServerInfo]:
        with self._lock:
            return [ServerInfo(**asdict(s)) for s in self.servers.values() if tag in s.tags]

    def enable_server(self, server_id: str) -> None:
        with self._lock:
            server = self.servers[server_id]
            server.enabled = True
            server.updated_at = time.time()
            if self.auto_save:
                self.save()

    def disable_server(self, server_id: str) -> None:
        with self._lock:
            server = self.servers[server_id]
            server.enabled = False
            server.updated_at = time.time()
            if self.auto_save:
                self.save()

    def save(self) -> None:
        if not self.file_path:
            return
        with self._lock:
            payload = {sid: asdict(info) for sid, info in self.servers.items()}
            os.makedirs(os.path.dirname(self.file_path), exist_ok=True)
            with open(self.file_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)

    def load(self) -> None:
        if not self.file_path:
            return
        with self._lock:
            with open(self.file_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            self.servers = {sid: ServerInfo(**data) for sid, data in payload.items()}

    def count(self) -> int:
        with self._lock:
            return len(self.servers)

    def count_enabled(self) -> int:
        with self._lock:
            return sum(1 for s in self.servers.values() if s.enabled)
