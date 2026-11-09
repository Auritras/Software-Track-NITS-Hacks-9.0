"""Core event model and the event-type taxonomy used across J.A.R.V.I.S."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

SEVERITY_RANK = {"info": 0, "warning": 1, "error": 2, "critical": 3}
ANOMALY_SEVERITIES = {"warning", "error", "critical"}

# Event types that describe a *cause* (something broke or changed) rather than a
# *symptom* (something downstream noticed the breakage). Root-cause ranking gives
# services that emit these a strong boost.
CAUSE_TYPES = {
    "connection_pool_exhausted",
    "resource_saturation",
    "oom",
    "disk_full",
    "crash",
    "deploy",
    "config_change",
    "cpu_saturation",
    "external_outage",
}

SYMPTOM_TYPES = {
    "timeout",
    "latency",
    "connection_error",
    "login_failure",
    "transaction_failure",
    "http_5xx",
    "exception",
    "error_rate",
    "queue_backlog",
    "error",
    "info",
}

TYPE_LABELS = {
    "connection_pool_exhausted": "connection pool exhausted",
    "resource_saturation": "resource saturation",
    "oom": "out of memory",
    "disk_full": "disk full",
    "crash": "process crash",
    "deploy": "faulty deployment",
    "config_change": "configuration change",
    "cpu_saturation": "CPU saturation",
    "external_outage": "external provider outage",
    "timeout": "timeouts",
    "latency": "high latency",
    "connection_error": "connection errors",
    "login_failure": "login failures",
    "transaction_failure": "failed transactions",
    "http_5xx": "HTTP 5xx errors",
    "exception": "unhandled exceptions",
    "error_rate": "elevated error rate",
    "queue_backlog": "queue backlog",
    "error": "errors",
    "info": "informational",
}


def humanize_type(event_type: str) -> str:
    return TYPE_LABELS.get(event_type, event_type.replace("_", " "))


@dataclass
class Event:
    seq: int
    id: str
    timestamp: datetime
    service: str
    severity: str
    type: str
    message: str
    source: str
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def is_anomaly(self) -> bool:
        return self.severity in ANOMALY_SEVERITIES

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "seq": self.seq,
            "timestamp": self.timestamp.isoformat(),
            "service": self.service,
            "severity": self.severity,
            "type": self.type,
            "type_label": humanize_type(self.type),
            "message": self.message,
            "source": self.source,
            "attributes": self.attributes,
        }
