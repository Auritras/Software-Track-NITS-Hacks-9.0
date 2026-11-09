"""Normalisation of raw events from any source, and log-file parsing.

Supported log line formats (one event per line):
  * JSON lines:      {"timestamp": "...", "service": "auth-service", "level": "error", "message": "..."}
  * Bracketed text:  2026-11-09T10:00:01Z ERROR [auth-service] Login failed ...
  * Colon text:      2026-11-09 10:00:01,123 ERROR auth-service: Login failed ...
  * logfmt:          ts=2026-11-09T10:00:01Z level=error service=auth-service msg="Login failed"
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from .models import SEVERITY_RANK

SEVERITY_ALIASES = {
    "debug": "info", "trace": "info", "info": "info", "notice": "info", "ok": "info",
    "warn": "warning", "warning": "warning",
    "err": "error", "error": "error",
    "crit": "critical", "critical": "critical", "fatal": "critical",
    "emerg": "critical", "alert": "critical", "panic": "critical",
}

# Ordered: the first matching rule wins, so causes come before symptoms.
TYPE_RULES: list[tuple[str, re.Pattern]] = [
    ("connection_pool_exhausted", re.compile(
        r"pool exhausted|connection pool|too many connections|max_connections|connection slots", re.I)),
    ("oom", re.compile(r"out of memory|\boom\b|oomkill|memory limit", re.I)),
    ("disk_full", re.compile(r"disk full|no space left|disk usage", re.I)),
    ("deploy", re.compile(r"\bdeploy(ed|ment)?\b|rollout|released version|release v", re.I)),
    ("config_change", re.compile(r"config(uration)? (change|update|reload)", re.I)),
    ("cpu_saturation", re.compile(r"\bcpu\b|load average", re.I)),
    ("external_outage", re.compile(r"upstream .*(unreachable|down)|provider (down|outage)|bank api", re.I)),
    ("crash", re.compile(r"crash|segfault|panic|core dumped|exited with|killed", re.I)),
    ("login_failure", re.compile(r"login fail|authentication fail|auth fail|invalid session|sign-?in fail", re.I)),
    ("transaction_failure", re.compile(r"(payment|transaction|txn).*(fail|declin|error)", re.I)),
    ("timeout", re.compile(r"timeout|timed out|deadline exceeded", re.I)),
    ("connection_error", re.compile(r"connection refused|could not connect|econnrefused|unreachable|connection reset", re.I)),
    ("http_5xx", re.compile(r"\b5\d\d\b|internal server error|bad gateway|service unavailable", re.I)),
    ("latency", re.compile(r"latency|slow|p9\d|response time", re.I)),
    ("exception", re.compile(r"exception|traceback|stack trace", re.I)),
    ("error_rate", re.compile(r"error rate", re.I)),
    ("queue_backlog", re.compile(r"backlog|queue depth|lag", re.I)),
]

TS_KEYS = ("timestamp", "ts", "time", "@timestamp", "datetime", "date")
SERVICE_KEYS = ("service", "svc", "app", "application", "component", "source_service", "logger")
SEVERITY_KEYS = ("severity", "level", "lvl", "log_level", "priority")
MESSAGE_KEYS = ("message", "msg", "text", "description", "summary")
RESERVED = set(TS_KEYS + SERVICE_KEYS + SEVERITY_KEYS + MESSAGE_KEYS + ("type", "id"))

TEXT_LINE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\s+"
    r"\[?(?P<level>[A-Za-z]+)\]?\s+"
    r"(?:\[(?P<svc1>[\w.\-]+)\]|(?P<svc2>[\w.\-]+):)\s*"
    r"(?P<msg>.*)$"
)
LOGFMT_PAIR = re.compile(r'(\w[\w.\-]*)=("(?:[^"\\]|\\.)*"|\S+)')


def normalize_severity(value: Any) -> str:
    if value is None:
        return "info"
    if isinstance(value, (int, float)):  # syslog-style numeric priority
        return "critical" if value <= 2 else "error" if value == 3 else "warning" if value == 4 else "info"
    return SEVERITY_ALIASES.get(str(value).strip().lower(), "info")


def parse_timestamp(value: Any) -> datetime:
    if value is None or value == "":
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 1e11 else value
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    text = str(value).strip().replace(",", ".")
    try:
        if text.replace(".", "", 1).isdigit():
            return parse_timestamp(float(text))
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)


def infer_type(message: str) -> str:
    for type_name, pattern in TYPE_RULES:
        if pattern.search(message):
            return type_name
    return "info"


def _first(raw: dict, keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in raw and raw[key] not in (None, ""):
            return raw[key]
    return None


def normalize(raw: dict[str, Any]) -> dict[str, Any]:
    """Map an arbitrary event/alert dict onto the canonical J.A.R.V.I.S. fields."""
    message = str(_first(raw, MESSAGE_KEYS) or "").strip()
    severity = normalize_severity(_first(raw, SEVERITY_KEYS))
    event_type = str(raw.get("type") or "").strip().lower() or infer_type(message)
    if event_type == "info" and SEVERITY_RANK[severity] >= 1:
        event_type = "error"
    attributes = dict(raw.get("attributes") or {})
    attributes.update({k: v for k, v in raw.items() if k not in RESERVED and k != "attributes"})
    return {
        "timestamp": parse_timestamp(_first(raw, TS_KEYS)),
        "service": str(_first(raw, SERVICE_KEYS) or "unknown-service").strip().lower(),
        "severity": severity,
        "type": event_type,
        "message": message or f"{severity} event",
        "attributes": attributes,
    }


def _coerce(value: str) -> Any:
    value = value.strip('"')
    for cast in (int, float):
        try:
            return cast(value)
        except ValueError:
            pass
    return value


def parse_log_line(line: str) -> dict[str, Any] | None:
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if line.startswith("{"):
        try:
            data = json.loads(line)
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None
    match = TEXT_LINE.match(line)
    if match:
        return {
            "timestamp": match["ts"],
            "level": match["level"],
            "service": match["svc1"] or match["svc2"],
            "message": match["msg"],
        }
    pairs = LOGFMT_PAIR.findall(line)
    if pairs and any(k in ("level", "msg", "message", "service") for k, _ in pairs):
        return {k: _coerce(v) for k, v in pairs}
    return None


def parse_log_text(text: str) -> tuple[list[dict[str, Any]], int]:
    """Return (parsed raw events, number of lines that could not be parsed)."""
    events, skipped = [], 0
    for line in text.splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        parsed = parse_log_line(line)
        if parsed is None:
            skipped += 1
        else:
            events.append(parsed)
    return events, skipped
