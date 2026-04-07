"""Thread-safe configuration manager with atomic save, backup, and hot-reload support."""

"""Thread-safe configuration manager with atomic save, backup, and hot-reload support."""

import configparser
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from policyd_py.config.settings import AppConfig


class ConfigManager:
    """Manages the lifecycle of ``AppConfig`` with atomic file writes and rollback."""
    """Manages the lifecycle of ``AppConfig`` with atomic file writes and rollback."""
    def __init__(self, config_path: str, initial_config: Optional[AppConfig] = None):
        self.config_path = config_path
        self._lock = threading.RLock()
        self._config = initial_config or AppConfig.load(config_path)

    def get_config(self) -> AppConfig:
        with self._lock:
            return self._config

    def reload(self) -> AppConfig:
        with self._lock:
            cfg = AppConfig.load(self.config_path)
            self._config = cfg
            return cfg

    def save(self, content: Optional[str] = None, updates: Optional[Dict[str, Dict[str, Any]]] = None) -> AppConfig:
        with self._lock:
            if content is None and updates is None:
                raise ValueError("either content or updates must be provided")

            if content is not None and updates is not None:
                raise ValueError("content and updates are mutually exclusive")

            self._backup_current()

            if content is not None:
                candidate_content = self._normalize_content(content)
            else:
                parser = configparser.ConfigParser()
                if os.path.exists(self.config_path):
                    parser.read(self.config_path)

                for section, values in (updates or {}).items():
                    if not parser.has_section(section):
                        parser.add_section(section)
                    for key, value in values.items():
                        parser.set(section, str(key), self._stringify(value))

                with tempfile.NamedTemporaryFile("w+", encoding="utf-8", delete=False, dir=str(Path(self.config_path).parent)) as handle:
                    parser.write(handle)
                    handle.flush()
                    handle.seek(0)
                    candidate_content = handle.read()
                    temp_rendered = handle.name
                os.unlink(temp_rendered)

            # Validate the rendered config before replacing the active file.
            parser = configparser.ConfigParser()
            parser.read_string(candidate_content)

            parent = Path(self.config_path).parent
            parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=str(parent)) as handle:
                handle.write(candidate_content)
                handle.flush()
                os.fsync(handle.fileno())
                temp_path = handle.name

            try:
                self._config = AppConfig.load(temp_path)
                os.replace(temp_path, self.config_path)
            except Exception:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
                raise

            return self._config

    def to_dict(self) -> Dict[str, Any]:
        cfg = self.get_config()
        return json.loads(cfg.model_dump_json())

    def _backup_current(self) -> str:
        path = Path(self.config_path)
        if not path.exists():
            return ""

        ts = time.strftime("%Y%m%d-%H%M%S", time.localtime())
        backup_path = path.with_name(f"{path.name}.backup.{ts}")
        backup_path.write_bytes(path.read_bytes())
        return str(backup_path)

    @staticmethod
    def _stringify(value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, list):
            return ",".join(str(x) for x in value)
        return str(value)

    @staticmethod
    def _normalize_content(content: str) -> str:
        if content.endswith("\n"):
            return content
        return content + "\n"
