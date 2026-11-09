"""Accuracy benchmark: inject random faults and measure how often J.A.R.V.I.S. finds the true root cause.

Every trial picks a random service that has at least one dependent (so the
answer is not trivial), a random fault for it, and a test condition:

* clean          - full propagation, all signals present
* noise          - unrelated warnings on other services at random times
* silent_cause   - the faulty service logs only generic errors, no cause signal
* partial_spread - only ~60% of dependents are affected
* clock_skew     - one dependent's clock runs 3s early, so its first symptom predates the root
* hard           - all of the above at once
"""
from __future__ import annotations

import random
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from .engine import analyse
from .faults import faults_for, plan_fault
from .store import EventStore
from .topology import Topology

CONDITIONS = {
    "clean": {},
    "noise": {"noise": 5},
    "silent_cause": {"silent_cause": True},
    "partial_spread": {"spread_probability": 0.6},
    "clock_skew": {"clock_skew": 3.0},
    "hard": {"noise": 5, "silent_cause": True, "spread_probability": 0.6, "clock_skew": 3.0},
}
CONDITION_LABELS = {
    "clean": "Clean signals", "noise": "Unrelated noise", "silent_cause": "No cause signal",
    "partial_spread": "Partial propagation", "clock_skew": "Clock skew", "hard": "All combined",
}


def run_trial(topo: Topology, root: str, fault: str, condition: str, seed: int) -> dict:
    rng = random.Random(seed)
    plan = plan_fault(topo, root, fault, rng, **CONDITIONS[condition])
    store = EventStore(topology=topo)
    base = datetime(2026, 11, 9, 10, 0, tzinfo=timezone.utc)
    raws = [{**raw, "timestamp": (base + timedelta(seconds=off)).isoformat()} for off, raw in plan.steps]
    events = store.add_raw(raws, source="benchmark")
    noise_ids = {events[i].id for i in plan.noise_indices}
    signal_ids = {e.id for e in events} - noise_ids
    incidents = analyse(store.events(), topo, now=base + timedelta(minutes=10))
    main = max(incidents, key=lambda i: len(signal_ids & set(i["event_ids"])))
    main_ids = set(main["event_ids"])
    ranked = [c["service"] for c in main["candidates"]]
    return {
        "root": root, "fault": fault, "condition": condition,
        "predicted": main["root_cause"]["service"],
        "correct": main["root_cause"]["service"] == root,
        "top3": root in ranked[:3],
        "confidence": main["root_cause"]["confidence"],
        "grouping": len(signal_ids & main_ids) / len(signal_ids),
        "noise_total": len(noise_ids),
        "noise_kept_out": len(noise_ids - main_ids),
    }


def run_benchmark(trials: int = 300, seed: int = 2026) -> dict:
    topo = Topology()
    reverse_targets = {callee for callees in topo.deps.values() for callee in callees}
    roots = sorted(reverse_targets)  # services with at least one dependent
    rng = random.Random(seed)
    names = list(CONDITIONS)
    started = time.perf_counter()
    results = []
    for i in range(trials):
        root = rng.choice(roots)
        fault = rng.choice(faults_for(topo, root))[0]
        results.append(run_trial(topo, root, fault, names[i % len(names)], rng.randint(1, 10**9)))
    elapsed = time.perf_counter() - started

    def pct(items, key):
        return round(100 * sum(1 for r in items if r[key]) / len(items), 1) if items else 0.0

    by_condition = defaultdict(list)
    by_kind = defaultdict(list)
    for r in results:
        by_condition[r["condition"]].append(r)
        by_kind[topo.kind(r["root"])].append(r)
    correct = [r for r in results if r["correct"]]
    wrong = [r for r in results if not r["correct"]]
    noise_total = sum(r["noise_total"] for r in results)
    return {
        "trials": trials,
        "seed": seed,
        "root_candidates": len(roots),
        "top1_accuracy": pct(results, "correct"),
        "top3_accuracy": pct(results, "top3"),
        "avg_confidence_correct": round(sum(r["confidence"] for r in correct) / len(correct), 1) if correct else 0,
        "avg_confidence_wrong": round(sum(r["confidence"] for r in wrong) / len(wrong), 1) if wrong else 0,
        "grouping_completeness": round(100 * sum(r["grouping"] for r in results) / len(results), 1),
        "noise_separation": round(100 * sum(r["noise_kept_out"] for r in results) / noise_total, 1) if noise_total else None,
        "by_condition": [
            {"condition": c, "label": CONDITION_LABELS[c], "trials": len(by_condition[c]),
             "top1": pct(by_condition[c], "correct"), "top3": pct(by_condition[c], "top3")}
            for c in names
        ],
        "by_kind": [
            {"kind": k, "trials": len(v), "top1": pct(v, "correct")}
            for k, v in sorted(by_kind.items())
        ],
        "misses": [{k: r[k] for k in ("root", "fault", "condition", "predicted", "confidence")} for r in wrong[:8]],
        "duration_ms": round(elapsed * 1000),
        "throughput": measure_throughput(seed=seed),
    }


def measure_throughput(incidents: int = 60, seed: int = 2026, repeats: int = 3) -> dict:
    """Time the full analysis (correlate + rank + impact + timeline + recommend) on one large
    event stream: ``incidents`` random failures spaced 10 minutes apart, with background noise."""
    topo = Topology()
    reverse_targets = {callee for callees in topo.deps.values() for callee in callees}
    roots = sorted(reverse_targets)
    rng = random.Random(seed)
    base = datetime(2026, 11, 9, 0, 0, tzinfo=timezone.utc)
    raws = []
    for k in range(incidents):
        root = rng.choice(roots)
        fault = rng.choice(faults_for(topo, root))[0]
        plan = plan_fault(topo, root, fault, rng, noise=3)
        start = base + timedelta(minutes=10 * k)
        raws += [{**raw, "timestamp": (start + timedelta(seconds=off)).isoformat()} for off, raw in plan.steps]
    store = EventStore(topology=topo, max_events=len(raws) + 1)
    store.add_raw(raws, source="benchmark")
    events = store.events()
    now = base + timedelta(minutes=10 * incidents + 10)
    best = float("inf")
    found = 0
    for _ in range(repeats):
        t0 = time.perf_counter()
        found = len(analyse(events, topo, now=now))
        best = min(best, time.perf_counter() - t0)
    return {
        "events": len(events),
        "outages": incidents,
        "incidents_found": found,
        "analysis_ms": round(best * 1000, 1),
        "events_per_second": round(len(events) / best) if best else None,
    }
