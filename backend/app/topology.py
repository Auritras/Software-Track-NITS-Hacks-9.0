"""Service dependency graph of the monitored platform.

Edges point from a caller to the service it depends on:
``api-gateway -> auth-service`` means the gateway calls auth-service, so a failure
in auth-service propagates *upwards* to the gateway.
"""
from __future__ import annotations

import threading
from collections import deque

DEFAULT_SERVICES: dict[str, dict] = {
    "web-frontend": {"label": "Web Frontend", "kind": "frontend", "tier": 0,
                     "capability": "Customer web app"},
    "mobile-app": {"label": "Mobile App", "kind": "frontend", "tier": 0,
                   "capability": "Customer mobile app"},
    "api-gateway": {"label": "API Gateway", "kind": "gateway", "tier": 1,
                    "capability": "All public API traffic"},
    "auth-service": {"label": "Auth Service", "kind": "service", "tier": 2,
                     "capability": "User login & sessions"},
    "payment-service": {"label": "Payment Service", "kind": "service", "tier": 2,
                        "capability": "Payments & checkout"},
    "account-service": {"label": "Account Service", "kind": "service", "tier": 2,
                        "capability": "Balances & profiles"},
    "reporting-service": {"label": "Reporting Service", "kind": "service", "tier": 2,
                          "capability": "Internal analytics reports"},
    "fraud-service": {"label": "Fraud Detection", "kind": "service", "tier": 3,
                      "capability": "Fraud screening"},
    "notification-service": {"label": "Notifications", "kind": "service", "tier": 3,
                             "capability": "Receipts & alerts"},
    "primary-db": {"label": "Primary DB", "kind": "database", "tier": 4,
                   "capability": "Core transactional data"},
    "redis-cache": {"label": "Redis Cache", "kind": "cache", "tier": 4,
                    "capability": "Sessions & hot data"},
    "bank-connector": {"label": "Bank Connector", "kind": "external", "tier": 4,
                       "capability": "Bank / UPI settlement"},
    "message-queue": {"label": "Message Queue", "kind": "queue", "tier": 4,
                      "capability": "Async messaging"},
    "warehouse-db": {"label": "Analytics Warehouse", "kind": "database", "tier": 4,
                     "capability": "Analytics data"},
}

DEFAULT_DEPENDENCIES: dict[str, list[str]] = {
    "web-frontend": ["api-gateway"],
    "mobile-app": ["api-gateway"],
    "api-gateway": ["auth-service", "payment-service", "account-service"],
    "auth-service": ["primary-db", "redis-cache"],
    "payment-service": ["primary-db", "fraud-service", "bank-connector", "notification-service"],
    "account-service": ["primary-db", "redis-cache"],
    "fraud-service": ["redis-cache"],
    "notification-service": ["message-queue"],
    "reporting-service": ["warehouse-db"],
}

USER_FACING_KINDS = {"frontend", "gateway"}


class Topology:
    def __init__(self, services: dict[str, dict] | None = None,
                 dependencies: dict[str, list[str]] | None = None):
        self._lock = threading.RLock()
        self.services: dict[str, dict] = {k: dict(v) for k, v in (services or DEFAULT_SERVICES).items()}
        self.deps: dict[str, list[str]] = {k: list(v) for k, v in (dependencies or DEFAULT_DEPENDENCIES).items()}
        self._down_cache: dict[str, frozenset[str]] = {}
        self._up_cache: dict[str, frozenset[str]] = {}

    # -- mutation -------------------------------------------------------
    def ensure(self, service: str) -> None:
        """Register a service seen in events but missing from the topology."""
        with self._lock:
            if service not in self.services:
                self.services[service] = {
                    "label": service, "kind": "unknown", "tier": 5,
                    "capability": "Unmapped service",
                }
                self._invalidate()

    def _invalidate(self) -> None:
        self._down_cache.clear()
        self._up_cache.clear()

    # -- queries --------------------------------------------------------
    def label(self, service: str) -> str:
        return self.services.get(service, {}).get("label", service)

    def kind(self, service: str) -> str:
        return self.services.get(service, {}).get("kind", "unknown")

    def capability(self, service: str) -> str:
        return self.services.get(service, {}).get("capability", "")

    def is_user_facing(self, service: str) -> bool:
        return self.kind(service) in USER_FACING_KINDS

    def downstream(self, service: str) -> frozenset[str]:
        """Every service that ``service`` depends on, transitively."""
        with self._lock:
            if service not in self._down_cache:
                self._down_cache[service] = frozenset(self._walk(service, self.deps))
            return self._down_cache[service]

    def upstream(self, service: str) -> frozenset[str]:
        """Every service that depends on ``service``, transitively."""
        with self._lock:
            if service not in self._up_cache:
                reverse: dict[str, list[str]] = {}
                for caller, callees in self.deps.items():
                    for callee in callees:
                        reverse.setdefault(callee, []).append(caller)
                self._up_cache[service] = frozenset(self._walk(service, reverse))
            return self._up_cache[service]

    def related(self, a: str, b: str) -> bool:
        return a == b or b in self.downstream(a) or a in self.downstream(b)

    def path(self, src: str, dst: str) -> list[str]:
        """Shortest dependency path from caller ``src`` down to ``dst`` (inclusive)."""
        if src == dst:
            return [src]
        prev: dict[str, str] = {}
        queue = deque([src])
        seen = {src}
        while queue:
            node = queue.popleft()
            for nxt in self.deps.get(node, []):
                if nxt in seen:
                    continue
                prev[nxt] = node
                if nxt == dst:
                    out = [dst]
                    while out[-1] != src:
                        out.append(prev[out[-1]])
                    return list(reversed(out))
                seen.add(nxt)
                queue.append(nxt)
        return []

    @staticmethod
    def _walk(start: str, graph: dict[str, list[str]]) -> set[str]:
        seen: set[str] = set()
        stack = list(graph.get(start, []))
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            stack.extend(graph.get(node, []))
        seen.discard(start)
        return seen

    def to_dict(self) -> dict:
        with self._lock:
            return {
                "nodes": [{"id": sid, **meta} for sid, meta in self.services.items()],
                "edges": [{"source": caller, "target": callee}
                          for caller, callees in self.deps.items() for callee in callees],
            }
