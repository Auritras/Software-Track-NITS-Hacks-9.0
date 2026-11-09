"""Generic fault injection: break *any* service and simulate how the failure spreads.

Unlike the hand-written scenarios, nothing here is scripted per service. A fault
is placed on a chosen service, and symptoms propagate up the real dependency
graph (callee -> callers) with realistic delays. This powers:

* "Break anything" mode in the dashboard - a judge picks the failing service.
* The accuracy benchmark - hundreds of random faults, optionally made harder with
  noise, missing cause signals and clock skew.
"""
from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass, field

from .topology import Topology

FAULTS_BY_KIND: dict[str, list[tuple[str, str]]] = {
    "database": [("connection_pool_exhausted", "Connection pool exhausted"), ("disk_full", "Disk full"),
                 ("crash", "Primary crash")],
    "cache": [("oom", "Out of memory"), ("crash", "Node crash")],
    "service": [("deploy", "Faulty deployment"), ("oom", "Memory leak (OOM)"), ("cpu_saturation", "CPU saturation")],
    "gateway": [("config_change", "Bad config push"), ("cpu_saturation", "CPU saturation")],
    "external": [("external_outage", "Provider outage")],
    "queue": [("crash", "Broker crash"), ("disk_full", "Disk full")],
    "frontend": [("deploy", "Faulty release")],
    "unknown": [("crash", "Process crash"), ("oom", "Out of memory")],
}

# (level, message template, event type) emitted by the faulty service itself.
CAUSE_EVENTS: dict[str, list[tuple[str, str, str]]] = {
    "connection_pool_exhausted": [
        ("warning", "Active connections at {p}% of max_connections", "connection_pool_exhausted"),
        ("critical", "FATAL: connection pool exhausted ({n}/{n} slots in use)", "connection_pool_exhausted")],
    "disk_full": [
        ("warning", "Disk usage {p}% on data volume", "disk_full"),
        ("critical", "No space left on device: writes failing", "disk_full")],
    "crash": [("critical", "Process crashed: exited with code 137, restarting", "crash")],
    "oom": [
        ("warning", "Memory usage {p}% of limit", "resource_saturation"),
        ("critical", "Container OOMKilled: out of memory", "oom")],
    "deploy": [
        ("info", "Deployment rolled out: v{ver} (3/3 pods ready)", "deploy"),
        ("critical", "Unhandled exception after release v{ver}: NullReference in request handler", "exception")],
    "cpu_saturation": [
        ("warning", "CPU at {p}% across all pods", "cpu_saturation"),
        ("critical", "CPU saturation: request queue growing, throttling enabled", "cpu_saturation")],
    "config_change": [
        ("info", "Configuration change applied: routing rules v{ver}", "config_change"),
        ("critical", "Routing failures after configuration change: HTTP 502 on {p}% of requests", "http_5xx")],
    "external_outage": [
        ("warning", "Provider latency {ms}ms (normal 400ms)", "latency"),
        ("critical", "Upstream provider unreachable: HTTP 503", "external_outage")],
}

ONGOING_BY_KIND = {
    "database": ("error", "Query timeout after 5000ms (queue={q})", "timeout"),
    "cache": ("error", "Connection refused on :6379", "connection_error"),
    "external": ("error", "Request to provider timed out after 15s", "timeout"),
    "queue": ("error", "Publish failed: broker unavailable (backlog {q}k)", "queue_backlog"),
    "gateway": ("error", "HTTP 502 on {p}% of upstream calls", "http_5xx"),
    "frontend": ("error", "Client errors spiking: {p}% of page loads failing", "error"),
}


@dataclass
class FaultPlan:
    root: str
    fault: str
    steps: list[tuple[float, dict]] = field(default_factory=list)
    affected: list[str] = field(default_factory=list)
    noise_indices: set[int] = field(default_factory=set)


def faults_for(topo: Topology, service: str) -> list[tuple[str, str]]:
    return FAULTS_BY_KIND.get(topo.kind(service), FAULTS_BY_KIND["unknown"])


def _ev(service: str, level: str, message: str, type_: str, **attrs) -> dict:
    return {"service": service, "level": level, "message": message, "type": type_, "attributes": attrs}


def _fmt(rng: random.Random, template: str) -> str:
    return template.format(p=rng.randint(85, 99), n=rng.choice([100, 200, 500]), ver=f"{rng.randint(2, 9)}.{rng.randint(0, 20)}.0",
                           ms=rng.randint(4000, 9000), q=rng.randint(40, 400))


def _symptom(rng: random.Random, topo: Topology, svc: str, upstream: str) -> dict:
    """What a caller logs when something it depends on is failing."""
    up = topo.label(upstream)
    kind = topo.kind(svc)
    if svc == "auth-service":
        return _ev(svc, "error", f"Login failed: {up} unavailable", "login_failure", count=rng.randint(5, 30))
    if svc == "payment-service":
        return _ev(svc, "error", f"Transaction TXN-{rng.randint(100000, 999999)} failed: {up} error",
                   "transaction_failure", count=1, amount=rng.randint(150, 20000))
    if kind == "gateway":
        if rng.random() < 0.35:
            return _ev(svc, "warning", f"p99 latency {rng.randint(2, 9)}.{rng.randint(0, 9)}s on routes via {up}", "latency")
        return _ev(svc, "error", f"HTTP 503 rate {rng.randint(8, 40)}% on routes via {up}", "http_5xx")
    if kind == "frontend":
        return _ev(svc, "error", f"Requests timing out: {topo.label(svc)} cannot reach {up}", "timeout",
                   users_affected=rng.randint(30, 400))
    return _ev(svc, "error", f"Call to {up} timed out after {rng.randint(2, 10) * 500}ms", "timeout")


def plan_fault(topo: Topology, root: str, fault: str, rng: random.Random, *, duration: float = 40.0,
               spread_probability: float = 1.0, noise: int = 0, silent_cause: bool = False,
               clock_skew: float = 0.0) -> FaultPlan:
    """Build the event sequence for ``fault`` on ``root`` and its propagation to dependents."""
    plan = FaultPlan(root=root, fault=fault)
    steps = plan.steps
    t0 = 5.0

    # 1. The faulty service itself.
    cause = CAUSE_EVENTS.get(fault, CAUSE_EVENTS["crash"])
    for i, (level, template, type_) in enumerate(cause):
        msg = _fmt(rng, template)
        if silent_cause:  # monitoring only caught generic errors, not the real cause
            level, type_, msg = ("error", "error", f"Requests failing on {topo.label(root)}")
        steps.append((t0 + i * 3.0, _ev(root, level, msg, type_)))
    failing_from = t0 + 3.0 * (len(cause) - 1)
    level, template, type_ = ONGOING_BY_KIND.get(topo.kind(root), ("error", "Request handling failed with errors", "error"))
    t = failing_from + 2
    while t < duration:
        steps.append((t + rng.uniform(0, 1.5), _ev(root, level, _fmt(rng, template), type_)))
        t += rng.uniform(4, 7)

    # 2. Propagate up the dependency graph: callers of a failing service start failing a little later.
    reverse: dict[str, list[str]] = {}
    for caller, callees in topo.deps.items():
        for callee in callees:
            reverse.setdefault(callee, []).append(caller)
    start_at = {root: failing_from}
    queue = deque([root])
    while queue:
        svc = queue.popleft()
        for caller in sorted(reverse.get(svc, [])):
            if caller in start_at or rng.random() > spread_probability:
                continue
            start_at[caller] = start_at[svc] + rng.uniform(1.5, 4.0)
            queue.append(caller)
            # which failing dependency the caller blames: the one closest to it on the path to root
            path = topo.path(caller, root)
            blamed = path[1] if len(path) > 1 else svc
            t = start_at[caller]
            while t < duration:
                steps.append((t, _symptom(rng, topo, caller, blamed)))
                t += rng.uniform(2.5, 6.0)
    plan.affected = sorted(start_at)

    # Clock skew: one dependent's clock runs early, so its first symptom predates the root's first signal.
    if clock_skew and len(start_at) > 1:
        victim = rng.choice([s for s in start_at if s != root])
        steps[:] = [(max(0.0, off - clock_skew) if raw["service"] == victim else off, raw) for off, raw in steps]

    # 3. Unrelated noise on random other services.
    candidates = [s for s in topo.services if s not in start_at]
    for _ in range(noise):
        if not candidates:
            break
        svc = rng.choice(candidates)
        steps.append((rng.uniform(0, duration),
                      _ev(svc, "warning", f"{topo.label(svc)}: transient slow response ({rng.randint(800, 2500)}ms)",
                          "latency", noise=True)))

    order = sorted(range(len(steps)), key=lambda i: steps[i][0])
    steps[:] = [steps[i] for i in order]
    plan.noise_indices = {i for i, (_, raw) in enumerate(steps) if raw["attributes"].get("noise")}
    for _, raw in steps:
        raw["attributes"].pop("noise", None)
    return plan
