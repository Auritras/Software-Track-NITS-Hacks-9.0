"""Correlation, root-cause analysis, impact analysis and timelines.

Pipeline
--------
1. **Correlate** - anomalous events (warning+) and cause-type events (e.g. deploys)
   are linked when they occur within ``window_s`` of each other *and* are related:
   same service, shared trace/request/host id, connected in the dependency graph,
   or showing the same activity pattern (identical error signature once ids and
   numbers are stripped). The reason for every link is recorded for explanation.
   Linked events are merged with union-find; each connected group is one incident.
2. **Rank root causes** - every service in the incident is scored on four
   explainable factors (coverage, precedence, depth, causal signal).
3. **Analyse impact** - affected / at-risk services, business capabilities, and
   user-level metrics (failed logins, failed transactions, revenue at risk).
4. **Build timeline** - ordered events plus the propagation path root -> symptoms.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime, timezone

from .models import CAUSE_TYPES, SEVERITY_RANK, Event, humanize_type
from .topology import Topology

CORRELATION_KEYS = ("trace_id", "request_id", "correlation_id", "host")
DEFAULT_WINDOW_S = 120
ACTIVE_WINDOW_S = 90

WEIGHTS = {"coverage": 0.30, "precedence": 0.20, "depth": 0.15, "causal": 0.20, "intensity": 0.15}
CAUSALITY_TOLERANCE_S = 5.0


class _DSU:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def _participates(e: Event) -> bool:
    return e.is_anomaly or e.type in CAUSE_TYPES


_VARIABLE_TOKEN = re.compile(r"\b[\w-]*\d[\w.,:%-]*|\b[0-9a-f]{8,}\b", re.I)
LINK_LABELS = {
    "same_service": "repeated alerts from the same service",
    "shared_id": "a shared trace, request or host id",
    "dependency": "services connected in the dependency graph",
    "pattern": "the same activity pattern (matching error signature) on different services",
}


def activity_signature(message: str) -> str:
    """Message template with variable parts (ids, numbers, timings) removed."""
    return " ".join(_VARIABLE_TOKEN.sub("#", message.lower()).split())


def _link_reason(a: Event, b: Event, topo: Topology) -> str | None:
    """Why two events (already within the time window) belong to the same incident, if they do."""
    if a.service == b.service:
        return "same_service"
    for key in CORRELATION_KEYS:
        value = a.attributes.get(key)
        if value and value == b.attributes.get(key):
            return "shared_id"
    if topo.related(a.service, b.service):
        return "dependency"
    sig = activity_signature(a.message)
    if a.is_anomaly and b.is_anomaly and len(sig) >= 16 and sig == activity_signature(b.message):
        return "pattern"
    return None


def correlate_with_links(events: list[Event], topo: Topology,
                         window_s: float = DEFAULT_WINDOW_S) -> list[tuple[list[Event], Counter]]:
    """Group events into incidents. Each group comes with a count of *why* its events were linked."""
    parts = sorted((e for e in events if _participates(e)), key=lambda e: (e.timestamp, e.seq))
    dsu = _DSU(len(parts))
    merges: list[tuple[int, str]] = []
    start = 0
    for j, ej in enumerate(parts):
        while (ej.timestamp - parts[start].timestamp).total_seconds() > window_s:
            start += 1
        for i in range(start, j):
            if dsu.find(i) == dsu.find(j):
                continue
            reason = _link_reason(parts[i], ej, topo)
            if reason:
                dsu.union(i, j)
                merges.append((j, reason))
    groups: dict[int, list[Event]] = defaultdict(list)
    for idx, e in enumerate(parts):
        groups[dsu.find(idx)].append(e)
    links: dict[int, Counter] = defaultdict(Counter)
    for j, reason in merges:
        links[dsu.find(j)][reason] += 1
    result = [(g, links[root]) for root, g in groups.items() if any(e.is_anomaly for e in g)]
    result.sort(key=lambda item: (item[0][0].timestamp, item[0][0].seq))
    return result


def correlate(events: list[Event], topo: Topology, window_s: float = DEFAULT_WINDOW_S) -> list[list[Event]]:
    return [g for g, _ in correlate_with_links(events, topo, window_s)]


def _correlation_summary(events: list[Event], links: Counter, window_s: float) -> dict:
    span = (events[-1].timestamp - events[0].timestamp).total_seconds()
    explanation = [f"{len(events)} events occurred within {span:.0f}s of each other "
                   f"(correlation window {window_s:.0f}s between consecutive related events)."]
    for reason in ("dependency", "same_service", "shared_id", "pattern"):
        if links.get(reason):
            explanation.append(f"{links[reason]} link{'s' if links[reason] != 1 else ''} via {LINK_LABELS[reason]}.")
    return {
        "window_seconds": window_s,
        "span_seconds": round(span, 1),
        "links": {r: links.get(r, 0) for r in LINK_LABELS},
        "explanation": explanation,
    }


# ---------------------------------------------------------------------------
# Root cause
# ---------------------------------------------------------------------------
def _dominant_type(events: list[Event]) -> str:
    causes = [e for e in events if e.type in CAUSE_TYPES]
    if causes:  # the most severe cause signal wins (e.g. OOM crash beats the earlier memory warning)
        counts = Counter(e.type for e in causes)
        return max(causes, key=lambda e: (SEVERITY_RANK[e.severity], counts[e.type])).type
    pool = [e.type for e in events if e.is_anomaly] or [e.type for e in events]
    return Counter(pool).most_common(1)[0][0]


def rank_root_causes(events: list[Event], topo: Topology) -> list[dict]:
    by_service: dict[str, list[Event]] = defaultdict(list)
    for e in events:
        by_service[e.service].append(e)
    services = set(by_service)
    first_seen = {s: min(ev.timestamp for ev in evs) for s, evs in by_service.items()}
    start = min(first_seen.values())
    end = max(e.timestamp for e in events)
    span = max((end - start).total_seconds(), 1.0)

    candidates = []
    def gap(a: str, b: str) -> float:  # seconds from a's first anomaly to b's
        return (first_seen[b] - first_seen[a]).total_seconds()

    for s, evs in by_service.items():
        # A cause cannot come after its effects: s only explains dependents that started
        # failing after it (with a small tolerance for clock skew between hosts).
        explained = {s} | {d for d in topo.upstream(s) & services if gap(s, d) >= -CAUSALITY_TOLERANCE_S}
        coverage = len(explained) / len(services)
        lead_s = (first_seen[s] - start).total_seconds()
        precedence = 1.0 - lead_s / span
        # Likewise, only dependencies that were already failing could have caused s to fail.
        failing_deps = sorted(d for d in (topo.downstream(s) & services) - {s}
                              if gap(d, s) >= -CAUSALITY_TOLERANCE_S)
        depth = 0.0 if failing_deps else 1.0
        cause_events = [e for e in evs if e.type in CAUSE_TYPES]
        if cause_events:
            causal = 1.0
        elif any(e.severity == "critical" for e in evs):
            causal = 0.3
        else:
            causal = 0.0
        # A sustained failure is stronger evidence than a one-off blip.
        weight = sum(1.0 if e.severity in ("error", "critical") else 0.5 for e in evs if e.is_anomaly)
        intensity = min(1.0, weight / 3.0)
        factors = {"coverage": coverage, "precedence": precedence, "depth": depth,
                   "causal": causal, "intensity": intensity}
        score = sum(WEIGHTS[k] * v for k, v in factors.items())
        candidates.append({
            "service": s,
            "label": topo.label(s),
            "kind": topo.kind(s),
            "score": round(score, 4),
            "factors": {k: round(v, 3) for k, v in factors.items()},
            "first_seen": first_seen[s].isoformat(),
            "lead_seconds": round(lead_s, 1),
            "explains": sorted(explained),
            "failing_dependencies": failing_deps,
            "cause_signals": sorted({e.type for e in cause_events}),
            "dominant_type": _dominant_type(evs),
            "event_count": len(evs),
        })
    candidates.sort(key=lambda c: (-c["score"], c["lead_seconds"], c["service"]))

    top = candidates[0]
    top["verdict"] = "Most likely root cause"
    for c in candidates[1:]:
        c["verdict"] = _why_not(c, top, len(services), topo)
    second = candidates[1]["score"] if len(candidates) > 1 else 0.0
    margin = (top["score"] - second) / top["score"] if top["score"] else 0.0
    top["confidence"] = round(min(0.99, top["score"] * (0.55 + 0.45 * margin)) * 100)
    for c in candidates[1:]:
        c["confidence"] = round(min(0.99, c["score"] * (1 - margin) * 0.55) * 100)
    return candidates


def _why_not(c: dict, top: dict, n_services: int, topo: Topology) -> str:
    """One-line, human-readable reason a candidate was ruled out in favour of ``top``."""
    reasons = []
    gap = c["lead_seconds"] - top["lead_seconds"]
    if top["service"] in c["failing_dependencies"]:
        reasons.append(f"depends on {top['label']}, which was already failing"
                       + (f" {gap:.0f}s earlier" if gap >= 1 else ""))
    elif c["failing_dependencies"]:
        deps = ", ".join(topo.label(s) for s in c["failing_dependencies"][:2])
        reasons.append(f"its own dependencies were failing ({deps})")
    elif gap >= 1:
        reasons.append(f"started failing {gap:.0f}s after {top['label']}")
    if len(c["explains"]) < len(top["explains"]):
        reasons.append(f"explains only {len(c['explains'])} of {n_services} affected services")
    if not c["cause_signals"] and top["cause_signals"]:
        reasons.append(f"showed only symptoms ({humanize_type(c['dominant_type'])}), no cause-level signal")
    if not reasons:
        reasons.append(f"scored lower on the combined evidence ({c['score']:.2f} vs {top['score']:.2f})")
    text = "; ".join(reasons[:2])
    return text[0].upper() + text[1:] + "."


def _evidence(root: dict, events: list[Event], topo: Topology, n_services: int) -> list[str]:
    ev = []
    others = [e for e in events if e.service != root["service"]]
    if others:
        gap = (min(e.timestamp for e in others) - min(
            e.timestamp for e in events if e.service == root["service"])).total_seconds()
        if gap > 0:
            ev.append(f"{root['label']} raised the first signal, {gap:.0f}s before any other service.")
    if root["explains"] and n_services > 1:
        dependents = [topo.label(s) for s in root["explains"] if s != root["service"]]
        if dependents:
            ev.append(f"{len(root['explains'])} of {n_services} affected services depend on "
                      f"{root['label']} ({', '.join(dependents)}).")
    if root["cause_signals"]:
        ev.append(f"{root['label']} emitted cause-level signals: "
                  f"{', '.join(humanize_type(t) for t in root['cause_signals'])}.")
    if not root["failing_dependencies"]:
        ev.append(f"None of {root['label']}'s own dependencies are failing - it is the deepest failing component.")
    crit = [e for e in events if e.service == root["service"] and e.severity == "critical"]
    if crit:
        ev.append(f"Critical signal: \"{crit[0].message}\"")
    return ev


# ---------------------------------------------------------------------------
# Impact
# ---------------------------------------------------------------------------
def _sum_attr(events: list[Event], event_type: str, attr: str = "count") -> float:
    total = 0.0
    for e in events:
        if e.type == event_type:
            value = e.attributes.get(attr, 1 if attr == "count" else 0)
            try:
                total += float(value)
            except (TypeError, ValueError):
                total += 1 if attr == "count" else 0
    return total


def analyse_impact(events: list[Event], root: str, topo: Topology) -> dict:
    affected = sorted({e.service for e in events}, key=lambda s: (topo.services.get(s, {}).get("tier", 9), s))
    at_risk = sorted(topo.upstream(root) - set(affected))
    failed_logins = int(_sum_attr(events, "login_failure"))
    failed_txns = int(_sum_attr(events, "transaction_failure"))
    revenue = _sum_attr(events, "transaction_failure", "amount")
    users = sum(int(e.attributes.get("users_affected", 0) or 0) for e in events)
    users = max(users, failed_logins + failed_txns)
    return {
        "affected_services": [
            {"service": s, "label": topo.label(s), "kind": topo.kind(s), "capability": topo.capability(s),
             "events": sum(1 for e in events if e.service == s),
             "worst_severity": max((e.severity for e in events if e.service == s), key=SEVERITY_RANK.get)}
            for s in affected
        ],
        "at_risk_services": [{"service": s, "label": topo.label(s), "capability": topo.capability(s)} for s in at_risk],
        "capabilities_impacted": sorted({topo.capability(s) for s in affected if topo.capability(s)}),
        "user_facing": any(topo.is_user_facing(s) for s in affected),
        "metrics": {
            "failed_logins": failed_logins,
            "failed_transactions": failed_txns,
            "revenue_at_risk": round(revenue, 2),
            "users_affected": users,
            "alerts": sum(1 for e in events if e.is_anomaly),
        },
    }


def classify_severity(events: list[Event], impact: dict) -> str:
    worst = max(SEVERITY_RANK[e.severity] for e in events)
    n = len(impact["affected_services"])
    m = impact["metrics"]
    if worst == 3 and (impact["user_facing"] or n >= 4 or m["failed_transactions"] > 0):
        return "SEV1"
    if worst >= 2 or n >= 3:
        return "SEV2"
    return "SEV3"


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------
def build_timeline(events: list[Event], root: str, topo: Topology) -> dict:
    start = events[0].timestamp
    seen: set[str] = set()
    entries = []
    for e in events:
        milestone = e.service not in seen
        seen.add(e.service)
        entries.append({
            **e.to_dict(),
            "offset_seconds": round((e.timestamp - start).total_seconds(), 1),
            "milestone": milestone,
            "is_root": e.service == root,
        })
    propagation = []
    edges: set[tuple[str, str]] = set()
    for entry in (x for x in entries if x["milestone"]):
        svc = entry["service"]
        path = topo.path(svc, root)  # svc -> ... -> root (call direction)
        via = path[-2] if len(path) >= 2 else None
        for a, b in zip(path, path[1:]):
            edges.add((b, a))  # failure flows from callee to caller
        propagation.append({
            "service": svc, "label": topo.label(svc), "offset_seconds": entry["offset_seconds"],
            "first_message": entry["message"], "is_root": svc == root,
            "path_to_root": path, "via": via,
        })
    return {
        "events": entries,
        "propagation": propagation,
        "propagation_edges": [{"from": a, "to": b} for a, b in sorted(edges)],
    }


# ---------------------------------------------------------------------------
# Full incident
# ---------------------------------------------------------------------------
def build_incident(events: list[Event], topo: Topology, now: datetime | None = None,
                   links: Counter | None = None, window_s: float = DEFAULT_WINDOW_S) -> dict:
    from .recommend import recommend  # local import avoids a cycle

    now = now or datetime.now(timezone.utc)
    candidates = rank_root_causes(events, topo)
    root = candidates[0]
    impact = analyse_impact(events, root["service"], topo)
    severity = classify_severity(events, impact)
    timeline = build_timeline(events, root["service"], topo)
    cause_label = humanize_type(root["dominant_type"])
    start, end = events[0].timestamp, events[-1].timestamp
    status = "active" if (now - end).total_seconds() <= ACTIVE_WINDOW_S else "resolved"
    n_alerts = impact["metrics"]["alerts"]
    n_services = len(impact["affected_services"])
    incident_id = f"INC-{events[0].seq:05d}"
    summary = (
        f"{n_alerts} alerts from {n_services} service{'s' if n_services != 1 else ''} were correlated into "
        f"a single incident. Most likely root cause: {root['label']} ({cause_label}), "
        f"confidence {root['confidence']}%."
    )
    if impact["capabilities_impacted"]:
        summary += " Impacted: " + ", ".join(impact["capabilities_impacted"]) + "."
    incident = {
        "id": incident_id,
        "title": f"{root['label']}: {cause_label}",
        "severity": severity,
        "status": status,
        "started_at": start.isoformat(),
        "last_event_at": end.isoformat(),
        "duration_seconds": round((end - start).total_seconds(), 1),
        "alert_count": n_alerts,
        "event_count": len(events),
        "service_count": n_services,
        "summary": summary,
        "root_cause": {**root, "cause_label": cause_label,
                       "evidence": _evidence(root, events, topo, n_services)},
        "candidates": candidates[:6],
        "impact": impact,
        "timeline": timeline,
        "correlation": _correlation_summary(events, links or Counter(), window_s),
        "event_ids": [e.id for e in events],
    }
    incident["recommendations"] = recommend(incident, topo)
    return incident


def analyse(events: list[Event], topo: Topology, window_s: float = DEFAULT_WINDOW_S,
            now: datetime | None = None) -> list[dict]:
    incidents = [build_incident(g, topo, now, links, window_s)
                 for g, links in correlate_with_links(events, topo, window_s)]
    order = {"SEV1": 0, "SEV2": 1, "SEV3": 2}
    incidents.sort(key=lambda i: i["started_at"], reverse=True)  # newest first...
    incidents.sort(key=lambda i: (i["status"] != "active", order[i["severity"]]))  # ...within status/severity
    return incidents
