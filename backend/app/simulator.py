"""Simulated live data source: a digital payment platform with failure scenarios.

Each scenario is a deterministic (seeded) list of ``(offset_seconds, raw_event)``.
It can be streamed in real time ("live") or injected at once with back-dated
timestamps ("instant").
"""
from __future__ import annotations

import random
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Callable

Step = tuple[float, dict]

HEALTHY_MESSAGES = {
    "web-frontend": "GET /checkout 200 in 182ms",
    "mobile-app": "Session heartbeat ok (active users: 1,8{n}2)",
    "api-gateway": "Routed 4,{n}10 requests/min, p99 312ms",
    "auth-service": "POST /login 200 in 96ms",
    "payment-service": "Transaction TXN-{id} settled",
    "account-service": "GET /accounts/balance 200 in 41ms",
    "fraud-service": "Risk score computed in 12ms",
    "notification-service": "Receipt sent for TXN-{id}",
    "primary-db": "Active connections 6{n}/200, replication lag 0.2s",
    "redis-cache": "Hit ratio 97.{n}%, memory 61%",
    "bank-connector": "UPI settlement batch acknowledged",
    "message-queue": "Queue depth {n}",
    "reporting-service": "Hourly report generated",
    "warehouse-db": "ETL load completed",
}


def _ev(service: str, level: str, message: str, type_: str | None = None, **attrs) -> dict:
    raw = {"service": service, "level": level, "message": message, "attributes": attrs}
    if type_:
        raw["type"] = type_
    return raw


def _repeat(rng: random.Random, start: float, end: float, every: float,
            make: Callable[[random.Random], dict]) -> list[Step]:
    steps, t = [], start
    while t <= end:
        steps.append((round(t + rng.uniform(0, every * 0.4), 2), make(rng)))
        t += every
    return steps


def _baseline(rng: random.Random, duration: float, quiet: set[str], until: dict[str, float]) -> list[Step]:
    """Healthy info traffic; a service stops reporting healthy once it starts failing."""
    steps = []
    for svc, template in HEALTHY_MESSAGES.items():
        if svc in quiet:
            continue
        stop = until.get(svc, duration)
        steps += _repeat(rng, rng.uniform(0, 3), stop, 6.0, lambda r, s=svc, tpl=template: _ev(
            s, "info", tpl.format(n=r.randint(1, 9), id=r.randint(100000, 999999))))
    return steps


def _txn(r: random.Random) -> str:
    return f"TXN-{r.randint(100000, 999999)}"


# ---------------------------------------------------------------------------
def scenario_db_pool(rng: random.Random) -> list[Step]:
    s: list[Step] = [
        (10.0, _ev("primary-db", "warning", "Active connections at 92% of max_connections (184/200)",
                   "connection_pool_exhausted", active=184, max=200)),
        (14.0, _ev("primary-db", "critical",
                   "FATAL: remaining connection slots are reserved - connection pool exhausted (200/200)",
                   "connection_pool_exhausted", active=200, max=200)),
    ]
    s += _repeat(rng, 16, 44, 4, lambda r: _ev("primary-db", "error",
                 f"Query timeout after 5000ms (waiting for connection, queue={r.randint(40, 120)})", "timeout"))
    s += _repeat(rng, 18, 46, 3, lambda r: _ev("auth-service", "error",
                 "Login failed: could not acquire database connection within 3000ms", "login_failure",
                 count=r.randint(6, 22)))
    s += _repeat(rng, 20, 46, 4, lambda r: _ev("account-service", "error",
                 "GET /accounts/balance failed: upstream DB timeout", "timeout"))
    s += _repeat(rng, 21, 47, 2.5, lambda r: _ev("payment-service", "error",
                 f"Transaction {_txn(r)} failed: database unavailable", "transaction_failure",
                 count=1, amount=r.randint(150, 18000)))
    s += [(23.0, _ev("api-gateway", "warning", "p99 latency 4.2s on /api/v1/* (SLO 800ms)", "latency", p99_ms=4200))]
    s += _repeat(rng, 26, 46, 5, lambda r: _ev("api-gateway", "error",
                 f"HTTP 503 rate {r.randint(12, 31)}% on /api/v1/login and /api/v1/pay", "http_5xx"))
    s += _repeat(rng, 27, 46, 6, lambda r: _ev("web-frontend", "error",
                 "Checkout page: payment request timed out after 10s", "timeout",
                 users_affected=r.randint(40, 120)))
    s += [(29.0, _ev("mobile-app", "error", "Login screen errors spiking: 312 users in the last minute",
                     "login_failure", count=0, users_affected=312))]
    # Unrelated background noise -> must become its own, separate incident.
    s += [
        (15.0, _ev("warehouse-db", "warning", "Long-running analytical query: 44s (nightly ETL)", "latency")),
        (17.5, _ev("reporting-service", "warning", "Nightly revenue report slow: warehouse query took 45s", "latency")),
    ]
    quiet_after = {"primary-db": 10, "auth-service": 18, "account-service": 20, "payment-service": 21,
                   "api-gateway": 23, "web-frontend": 27, "mobile-app": 29, "warehouse-db": 15,
                   "reporting-service": 17}
    return s + _baseline(rng, 48, set(), quiet_after)


def scenario_cache(rng: random.Random) -> list[Step]:
    s: list[Step] = [
        (8.0, _ev("redis-cache", "warning", "Memory usage 97% of maxmemory, evictions disabled", "resource_saturation")),
        (12.0, _ev("redis-cache", "critical", "Redis process killed: Out of memory (OOMKilled), restarting", "oom")),
    ]
    s += _repeat(rng, 13, 40, 5, lambda r: _ev("redis-cache", "error", "Connection refused on :6379 (node restarting)",
                 "connection_error"))
    s += _repeat(rng, 14, 42, 3, lambda r: _ev("auth-service", "error", "Login failed: session store unavailable",
                 "login_failure", count=r.randint(10, 30)))
    s += _repeat(rng, 15, 42, 4, lambda r: _ev("fraud-service", "error",
                 "Risk check timed out: feature cache unreachable", "timeout"))
    s += _repeat(rng, 17, 42, 3, lambda r: _ev("payment-service", "error",
                 f"Transaction {_txn(r)} failed: fraud check timeout", "transaction_failure",
                 count=1, amount=r.randint(200, 9000)))
    s += _repeat(rng, 18, 42, 5, lambda r: _ev("account-service", "warning",
                 f"Cache miss storm: balance lookups p99 {r.randint(900, 1800)}ms", "latency"))
    s += _repeat(rng, 20, 42, 6, lambda r: _ev("api-gateway", "error",
                 f"HTTP 502 rate {r.randint(8, 19)}% on /api/v1/login", "http_5xx"))
    s += [(24.0, _ev("mobile-app", "error", "Users being logged out unexpectedly (845 sessions dropped)",
                     "login_failure", count=0, users_affected=845))]
    quiet_after = {"redis-cache": 8, "auth-service": 14, "fraud-service": 15, "payment-service": 17,
                   "account-service": 18, "api-gateway": 20, "mobile-app": 24}
    return s + _baseline(rng, 44, set(), quiet_after)


def scenario_bank(rng: random.Random) -> list[Step]:
    s: list[Step] = [
        (9.0, _ev("bank-connector", "warning", "Settlement API p99 latency 6.8s (normal 400ms)", "latency")),
        (13.0, _ev("bank-connector", "critical", "Upstream bank API unreachable: HTTP 503 from NPCI switch",
                   "external_outage")),
    ]
    s += _repeat(rng, 14, 42, 4, lambda r: _ev("bank-connector", "error", "Settlement request timed out after 15s",
                 "timeout"))
    s += _repeat(rng, 15, 44, 2, lambda r: _ev("payment-service", "error",
                 f"Transaction {_txn(r)} failed: bank settlement timeout", "transaction_failure",
                 count=1, amount=r.randint(100, 25000)))
    s += _repeat(rng, 19, 44, 6, lambda r: _ev("api-gateway", "warning",
                 f"HTTP 504 rate {r.randint(5, 14)}% on /api/v1/pay", "http_5xx"))
    s += [(22.0, _ev("web-frontend", "error", "Checkout conversion dropped 64% in 5 minutes", "error",
                     users_affected=1290))]
    quiet_after = {"bank-connector": 9, "payment-service": 15, "api-gateway": 19, "web-frontend": 22}
    return s + _baseline(rng, 46, set(), quiet_after)


def scenario_deploy(rng: random.Random) -> list[Step]:
    s: list[Step] = [
        (8.0, _ev("auth-service", "info", "Deployment auth-service v4.2.0 rolled out (3/3 pods ready)", "deploy",
                  version="4.2.0")),
        (11.0, _ev("auth-service", "critical",
                   "NullPointerException in TokenValidator.verify() - 100% of token checks failing", "exception")),
    ]
    s += _repeat(rng, 12, 40, 2.5, lambda r: _ev("auth-service", "error", "Login failed: token validation error",
                 "login_failure", count=r.randint(15, 40)))
    s += _repeat(rng, 15, 40, 5, lambda r: _ev("api-gateway", "error",
                 f"HTTP 500 rate {r.randint(20, 45)}% on /api/v1/login", "http_5xx"))
    s += [(18.0, _ev("mobile-app", "error", "Login failures reported by 1,204 users", "login_failure",
                     count=0, users_affected=1204)),
          (21.0, _ev("web-frontend", "error", "Sign-in page error rate 38%", "login_failure",
                     count=0, users_affected=530))]
    quiet_after = {"auth-service": 11, "api-gateway": 15, "mobile-app": 18, "web-frontend": 21}
    return s + _baseline(rng, 42, set(), quiet_after)


SCENARIOS = {
    "db_pool_exhaustion": {
        "name": "Primary DB connection pool exhaustion",
        "description": "Login failures, slow APIs and failed payments - all caused by one database "
                       "running out of connections. Includes unrelated analytics noise.",
        "build": scenario_db_pool,
    },
    "cache_outage": {
        "name": "Redis cache OOM crash",
        "description": "Session store crashes: users logged out, fraud checks time out, payments fail.",
        "build": scenario_cache,
    },
    "bank_outage": {
        "name": "External bank / UPI provider outage",
        "description": "Bank settlement API goes down; only the payment path is affected.",
        "build": scenario_bank,
    },
    "bad_deploy": {
        "name": "Faulty auth-service deployment",
        "description": "A new release breaks token validation right after rollout.",
        "build": scenario_deploy,
    },
}


def build(name: str, seed: int = 7) -> list[Step]:
    if name not in SCENARIOS:
        raise KeyError(name)
    steps = SCENARIOS[name]["build"](random.Random(seed))
    return sorted(steps, key=lambda s: s[0])


def materialize(name: str, base: datetime, seed: int = 7) -> list[dict]:
    """All scenario events with absolute timestamps starting at ``base``."""
    out = []
    for offset, raw in build(name, seed):
        out.append({**raw, "timestamp": (base + timedelta(seconds=offset)).isoformat()})
    return out


class SimulationRunner:
    """Streams a scenario into the store in (accelerated) real time."""

    def __init__(self, store):
        self.store = store
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.status: dict = {"running": False}

    def start(self, name: str, speed: float = 1.0, seed: int | None = None) -> dict:
        self.stop()
        steps = build(name, seed if seed is not None else random.randint(1, 10_000))
        duration = steps[-1][0] if steps else 0
        self._stop = threading.Event()
        self.status = {"running": True, "scenario": name, "speed": speed, "emitted": 0,
                       "total": len(steps), "duration_seconds": round(duration / speed, 1)}
        stop = self._stop

        def run():
            began = time.monotonic()
            for offset, raw in steps:
                wait = offset / speed - (time.monotonic() - began)
                if wait > 0 and stop.wait(wait):
                    break
                self.store.add_raw([{**raw, "timestamp": datetime.now(timezone.utc).isoformat()}],
                                   source="simulator")
                self.status["emitted"] += 1
            self.status["running"] = False

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()
        return self.status

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        self.status = {**self.status, "running": False}
