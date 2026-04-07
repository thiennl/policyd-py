from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass
class ServerInfo:
    id: str
    name: str
    host: str
    port: int
    username: str = ""
    password: str = ""
    enabled: bool = True
    description: str = ""
    tags: List[str] = field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0


@dataclass
class ServerStatus:
    server_id: str
    server_name: str = ""
    online: bool = False
    last_check: float = 0.0
    response_time_ms: float = 0.0
    stats: Optional[Dict[str, Any]] = None
    error: str = ""
    version: str = ""


@dataclass
class AggregatedStats:
    total_servers: int = 0
    online_servers: int = 0
    offline_servers: int = 0
    total_requests: int = 0
    total_accepted: int = 0
    total_rejected: int = 0
    total_rate_limited: int = 0
    total_blacklisted: int = 0
    total_users_locked: int = 0
    active_connections: int = 0
    total_sliding_window_checks: int = 0
    total_adaptive_adjustments: int = 0
    total_adaptive_tightened: int = 0
    total_adaptive_relaxed: int = 0
    total_penalty_applied: int = 0
    total_penalty_escalations: int = 0
    server_stats: Dict[str, Dict[str, Any]] = field(default_factory=dict)


@dataclass
class RemoteAction:
    server_id: str
    action: str
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RemoteActionResult:
    server_id: str
    success: bool
    message: str = ""
    error: str = ""
    timestamp: float = 0.0



def to_json_dict(obj: Any) -> Dict[str, Any]:
    return asdict(obj)
