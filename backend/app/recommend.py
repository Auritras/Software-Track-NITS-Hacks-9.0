"""Smart recommendations: a rule base keyed on the root cause (component kind +
failure type), plus mitigations for the symptoms observed in the blast radius."""
from __future__ import annotations

from .topology import Topology

IMMEDIATE, SHORT, LONG = "immediate", "short_term", "long_term"


def _rec(priority: str, title: str, detail: str, target: str, command: str | None = None) -> dict:
    return {"priority": priority, "title": title, "detail": detail, "target": target, "command": command}


def _root_rules(kind: str, cause: str, svc: str, label: str) -> list[dict]:
    recs: list[dict] = []
    if kind == "database" and cause in ("connection_pool_exhausted", "resource_saturation"):
        recs += [
            _rec(IMMEDIATE, f"Terminate idle / long-running sessions on {label}",
                 "Free connection slots by killing sessions idle in transaction or running > 60s.", svc,
                 "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                 "WHERE state = 'idle in transaction' OR now() - query_start > interval '60 seconds';"),
            _rec(IMMEDIATE, f"Shift read traffic to a {label} read replica",
                 "Route balance lookups and session reads to replicas to cut load on the primary.", svc),
            _rec(SHORT, "Put a connection pooler in front of the database",
                 "Deploy PgBouncer (transaction mode) and cap per-service pool sizes so one service "
                 "cannot starve the others.", svc),
            _rec(LONG, "Add circuit breakers to every database client",
                 "Fail fast instead of queueing requests when the pool is saturated, preventing cascades.", svc),
        ]
    elif kind == "database":
        recs += [
            _rec(IMMEDIATE, f"Check {label} health and fail over if degraded",
                 "Inspect replication, disk, locks and slow queries; promote a replica if the primary is unhealthy.", svc),
            _rec(SHORT, "Identify and optimise the slowest queries",
                 "Use the slow-query log / pg_stat_statements to find the queries driving load.", svc),
        ]
    elif kind == "cache":
        recs += [
            _rec(IMMEDIATE, f"Restart {label} with a memory limit and eviction policy",
                 "Set maxmemory and maxmemory-policy=allkeys-lru so the cache evicts instead of crashing.", svc,
                 "redis-cli CONFIG SET maxmemory-policy allkeys-lru"),
            _rec(IMMEDIATE, "Enable cache-miss fallback with rate limiting",
                 "Let services read through to the database, but rate-limit to avoid a thundering herd.", svc),
            _rec(LONG, "Run the cache as a replicated cluster",
                 "Use Redis Sentinel / Cluster so a single node failure does not take sessions down.", svc),
        ]
    elif kind == "external":
        recs += [
            _rec(IMMEDIATE, "Fail over to the backup payment provider",
                 f"{label} is failing upstream; route new transactions to the secondary bank/PSP.", svc),
            _rec(IMMEDIATE, "Queue failed transactions for automatic retry",
                 "Hold transactions in a durable queue and retry with exponential backoff once the provider recovers.", svc),
            _rec(SHORT, "Contact the provider and track their status page",
                 "Open a ticket with the bank / switch operator and record the outage window.", svc),
        ]
    elif cause == "deploy":
        recs += [
            _rec(IMMEDIATE, f"Roll back the latest {label} deployment",
                 "Errors started right after a deployment - revert to the last known-good version.", svc,
                 f"kubectl rollout undo deployment/{svc}"),
            _rec(SHORT, "Add canary releases with automatic rollback",
                 "Release to a small percentage of traffic first and roll back automatically on error-rate spikes.", svc),
        ]
    elif cause in ("oom", "crash"):
        recs += [
            _rec(IMMEDIATE, f"Restart {label} and raise its memory limit",
                 "Restore service capacity, then capture a heap dump to find the leak.", svc,
                 f"kubectl rollout restart deployment/{svc}"),
            _rec(SHORT, "Add autoscaling on memory and CPU", "Scale out before resources are exhausted.", svc),
        ]
    elif cause == "disk_full":
        recs += [_rec(IMMEDIATE, f"Free disk space on {label}",
                      "Rotate/compress logs and expand the volume.", svc)]
    else:
        recs += [_rec(IMMEDIATE, f"Investigate {label}",
                      f"{label} is the most likely origin; check its recent changes, resources and logs.", svc)]
    return recs


SYMPTOM_RULES = {
    "login_failure": (IMMEDIATE, "Extend existing session lifetimes temporarily",
                      "Keep already-logged-in users active while logins are failing, reducing user impact."),
    "transaction_failure": (IMMEDIATE, "Pause automatic payment retries from clients",
                            "Prevent duplicate charges and retry storms; show a 'payments delayed' banner instead."),
    "http_5xx": (SHORT, "Enable graceful degradation at the API gateway",
                 "Serve cached or degraded responses and return clear 503s with Retry-After headers."),
    "latency": (SHORT, "Tighten client timeouts and enable load shedding",
                "Shed low-priority traffic so critical paths (login, payments) keep their capacity."),
    "timeout": (SHORT, "Tighten client timeouts and enable load shedding",
                "Shed low-priority traffic so critical paths (login, payments) keep their capacity."),
}


def recommend(incident: dict, topo: Topology) -> list[dict]:
    root = incident["root_cause"]
    svc, label = root["service"], root["label"]
    recs = _root_rules(root["kind"], root["dominant_type"], svc, label)
    signals = set(root.get("cause_signals", []))
    if "config_change" in signals:
        recs.insert(0, _rec(IMMEDIATE, f"Revert the recent configuration change on {label}",
                            "A configuration change preceded the failure - restore the last known-good config.", svc))
    if "deploy" in signals and root["dominant_type"] != "deploy":
        recs.insert(0, _rec(IMMEDIATE, f"Roll back the latest {label} deployment",
                            "A deployment preceded the failure - revert to the last known-good version.", svc,
                            f"kubectl rollout undo deployment/{svc}"))

    seen_titles = {r["title"] for r in recs}
    symptom_types = {e["type"] for e in incident["timeline"]["events"] if e["service"] != svc}
    for t in ("login_failure", "transaction_failure", "http_5xx", "latency", "timeout"):
        if t in symptom_types:
            priority, title, detail = SYMPTOM_RULES[t]
            if title not in seen_titles:
                seen_titles.add(title)
                services = sorted({e["service"] for e in incident["timeline"]["events"] if e["type"] == t})
                recs.append(_rec(priority, title, detail, ", ".join(services)))

    if incident["impact"]["metrics"]["failed_transactions"]:
        recs.append(_rec(SHORT, "Reconcile and notify affected customers",
                         f"{incident['impact']['metrics']['failed_transactions']} transactions failed; "
                         "reconcile with the ledger and send status notifications.", "payment-service"))
    if incident["severity"] == "SEV1":
        recs.insert(0, _rec(IMMEDIATE, "Declare a SEV1 and page the on-call owner",
                            f"Open an incident channel and page the {label} owner; post a status-page update.", svc))
    order = {IMMEDIATE: 0, SHORT: 1, LONG: 2}
    recs.sort(key=lambda r: order[r["priority"]])
    return recs
