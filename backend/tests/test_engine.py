from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import simulator
from app.engine import analyse
from app.parsing import parse_log_line, parse_log_text
from app.report import render_markdown
from app.store import EventStore

BASE = datetime(2026, 11, 9, 10, 0, 0, tzinfo=timezone.utc)
NOW = BASE + timedelta(seconds=60)


def run(scenario: str, seed: int = 7):
    store = EventStore()
    store.add_raw(simulator.materialize(scenario, BASE, seed), source="simulator")
    return analyse(store.events(), store.topology, now=NOW)


@pytest.mark.parametrize("scenario,root", [
    ("db_pool_exhaustion", "primary-db"),
    ("cache_outage", "redis-cache"),
    ("bank_outage", "bank-connector"),
    ("bad_deploy", "auth-service"),
])
@pytest.mark.parametrize("seed", [1, 7, 42, 999])
def test_root_cause_identified(scenario, root, seed):
    incidents = run(scenario, seed)
    main = max(incidents, key=lambda i: i["alert_count"])
    assert main["root_cause"]["service"] == root
    assert main["root_cause"]["confidence"] >= 60
    assert main["severity"] == "SEV1"


def test_db_scenario_correlates_symptoms_and_separates_noise():
    incidents = run("db_pool_exhaustion")
    assert len(incidents) == 2
    main = max(incidents, key=lambda i: i["alert_count"])
    noise = min(incidents, key=lambda i: i["alert_count"])
    assert set(main["impact"]["affected_services"][i]["service"] for i in range(len(main["impact"]["affected_services"]))) == {
        "primary-db", "auth-service", "account-service", "payment-service", "api-gateway",
        "web-frontend", "mobile-app"}
    assert {s["service"] for s in noise["impact"]["affected_services"]} == {"warehouse-db", "reporting-service"}
    assert noise["root_cause"]["service"] == "warehouse-db"
    assert noise["severity"] == "SEV3"
    m = main["impact"]["metrics"]
    assert m["failed_logins"] > 0 and m["failed_transactions"] > 0 and m["revenue_at_risk"] > 0


def test_timeline_and_propagation():
    main = max(run("db_pool_exhaustion"), key=lambda i: i["alert_count"])
    prop = main["timeline"]["propagation"]
    assert prop[0]["service"] == "primary-db" and prop[0]["is_root"]
    offsets = [p["offset_seconds"] for p in prop]
    assert offsets == sorted(offsets)
    edges = {(e["from"], e["to"]) for e in main["timeline"]["propagation_edges"]}
    assert ("primary-db", "auth-service") in edges
    assert ("api-gateway", "web-frontend") in edges


def test_recommendations_match_cause():
    main = max(run("db_pool_exhaustion"), key=lambda i: i["alert_count"])
    titles = " ".join(r["title"] for r in main["recommendations"]).lower()
    assert "session" in titles and "replica" in titles
    deploy = max(run("bad_deploy"), key=lambda i: i["alert_count"])
    assert any("roll back" in r["title"].lower() for r in deploy["recommendations"])


def test_events_far_apart_are_separate_incidents():
    store = EventStore()
    store.add_raw([
        {"timestamp": BASE.isoformat(), "service": "auth-service", "level": "error", "message": "Login failed"},
        {"timestamp": (BASE + timedelta(minutes=30)).isoformat(), "service": "auth-service",
         "level": "error", "message": "Login failed"},
    ], source="api")
    assert len(analyse(store.events(), store.topology, now=NOW)) == 2


def test_unknown_services_correlate_by_trace_id():
    store = EventStore()
    store.add_raw([
        {"timestamp": BASE.isoformat(), "service": "svc-a", "level": "error", "message": "boom", "trace_id": "t1"},
        {"timestamp": (BASE + timedelta(seconds=2)).isoformat(), "service": "svc-b", "level": "error",
         "message": "upstream failed", "trace_id": "t1"},
        {"timestamp": (BASE + timedelta(seconds=3)).isoformat(), "service": "svc-c", "level": "error",
         "message": "unrelated"},
    ], source="api")
    incidents = analyse(store.events(), store.topology, now=NOW)
    assert sorted(i["service_count"] for i in incidents) == [1, 2]


def test_activity_pattern_links_unmapped_services():
    store = EventStore()
    store.add_raw([
        {"timestamp": BASE.isoformat(), "service": "billing-worker-1", "level": "error",
         "message": "Disk quota exceeded for tenant 4411 on volume vol-88af21"},
        {"timestamp": (BASE + timedelta(seconds=5)).isoformat(), "service": "billing-worker-2", "level": "error",
         "message": "Disk quota exceeded for tenant 9020 on volume vol-12cc90"},
        {"timestamp": (BASE + timedelta(seconds=6)).isoformat(), "service": "search-indexer", "level": "error",
         "message": "Index shard rebalance failed"},
    ], source="api")
    incidents = analyse(store.events(), store.topology, now=NOW)
    grouped = next(i for i in incidents if i["service_count"] == 2)
    assert grouped["correlation"]["links"]["pattern"] == 1
    assert len(incidents) == 2


def test_correlation_explains_links():
    main = max(run("db_pool_exhaustion"), key=lambda i: i["alert_count"])
    corr = main["correlation"]
    assert corr["links"]["dependency"] > 0 and corr["links"]["same_service"] > 0
    assert sum(corr["links"].values()) == main["event_count"] - 1  # spanning links
    assert any("dependency graph" in line for line in corr["explanation"])


def test_log_line_formats():
    a = parse_log_line("2026-11-09T10:00:01Z ERROR [auth-service] Login failed: db timeout")
    b = parse_log_line("2026-11-09 10:00:01,123 WARN payment-service: Transaction TXN-1 failed")
    c = parse_log_line('ts=2026-11-09T10:00:01Z level=error service=primary-db msg="too many connections"')
    d = parse_log_line('{"timestamp": "2026-11-09T10:00:01Z", "service": "api-gateway", "level": "error", "message": "HTTP 503"}')
    assert a["service"] == "auth-service" and a["level"] == "ERROR"
    assert b["service"] == "payment-service"
    assert c["service"] == "primary-db" and c["msg"] == "too many connections"
    assert d["service"] == "api-gateway"
    events, skipped = parse_log_text("garbage line\n" + "2026-11-09T10:00:01Z ERROR [x] y")
    assert len(events) == 1 and skipped == 1


def test_type_inference_via_store():
    store = EventStore()
    evs = store.add_raw([parse_log_line(
        'ts=2026-11-09T10:00:01Z level=fatal service=primary-db msg="FATAL: too many connections"')], "log")
    assert evs[0].type == "connection_pool_exhausted" and evs[0].severity == "critical"


def test_report_renders():
    main = max(run("cache_outage"), key=lambda i: i["alert_count"])
    md = render_markdown(main)
    assert "Redis Cache" in md and "## Recommended Actions" in md


def test_api_end_to_end(tmp_path):
    from app.main import app, store
    store.reset()
    client = TestClient(app)
    assert client.post("/api/simulate", json={"scenario": "db_pool_exhaustion", "mode": "instant"}).status_code == 200
    log = "2026-11-09T10:00:01Z ERROR [auth-service] Login failed\n"
    r = client.post("/api/logs/upload", files={"file": ("app.log", log, "text/plain")})
    assert r.json()["accepted"] == 1
    r = client.post("/api/events", json={"service": "api-gateway", "level": "warning", "message": "slow"})
    assert r.json()["accepted"] == 1
    incidents = client.get("/api/incidents").json()
    assert incidents
    detail = client.get(f"/api/incidents/{incidents[0]['id']}").json()
    assert detail["root_cause"]["service"] == "primary-db"
    assert client.get(f"/api/incidents/{incidents[0]['id']}/report").status_code == 200
    topo = client.get("/api/topology").json()
    assert any(n["health"].get("root_cause") for n in topo["nodes"])
    assert client.get("/api/stats").json()["noise_reduction"] > 50


@pytest.mark.parametrize("root", ["primary-db", "redis-cache", "bank-connector", "api-gateway",
                                  "fraud-service", "message-queue", "auth-service"])
def test_injected_faults_on_any_service_are_diagnosed(root):
    import random as _random
    from app.faults import faults_for, plan_fault
    from app.topology import Topology
    topo = Topology()
    for fault, _ in faults_for(topo, root):
        plan = plan_fault(topo, root, fault, _random.Random(11))
        store = EventStore(topology=topo)
        store.add_raw([{**raw, "timestamp": (BASE + timedelta(seconds=off)).isoformat()} for off, raw in plan.steps], "t")
        main = max(analyse(store.events(), topo, now=NOW), key=lambda i: i["event_count"])
        assert main["root_cause"]["service"] == root, (root, fault)
        assert all(c["verdict"] for c in main["candidates"])


def test_benchmark_reports_accuracy():
    from app.benchmark import run_benchmark
    result = run_benchmark(trials=60, seed=1)
    assert result["top1_accuracy"] >= 90
    assert result["top3_accuracy"] >= 95
    assert {c["condition"] for c in result["by_condition"]} >= {"clean", "noise", "hard"}


def test_why_not_mentions_failing_root():
    main = max(run("db_pool_exhaustion"), key=lambda i: i["alert_count"])
    auth = next(c for c in main["candidates"] if c["service"] == "auth-service")
    assert "Primary DB" in auth["verdict"]


def test_fault_api():
    from app.main import app, store
    store.reset()
    client = TestClient(app)
    catalog = client.get("/api/faults").json()
    assert {f["id"] for f in catalog["redis-cache"]} == {"oom", "crash"}
    r = client.post("/api/faults/inject", json={"service": "redis-cache", "fault": "oom", "mode": "instant"})
    assert r.status_code == 200 and "auth-service" in r.json()["affected"]
    assert client.get("/api/incidents").json()[0]["root_service"] == "redis-cache"
    assert client.post("/api/faults/inject", json={"service": "redis-cache", "fault": "deploy"}).status_code == 400


def test_reset_can_be_undone():
    from app.main import app, store
    store.reset()
    client = TestClient(app)
    client.post("/api/simulate", json={"scenario": "bad_deploy", "mode": "instant"})
    n = client.get("/api/stats").json()["events"]
    assert client.post("/api/reset").json()["cleared"] == n
    assert client.get("/api/stats").json()["events"] == 0
    assert client.post("/api/reset/undo").json()["restored"] == n
    assert client.get("/api/stats").json()["events"] == n
    assert client.post("/api/reset/undo").status_code == 409
