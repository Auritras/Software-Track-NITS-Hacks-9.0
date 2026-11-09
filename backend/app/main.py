"""J.A.R.V.I.S. API server."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import simulator
from .engine import ACTIVE_WINDOW_S
from .parsing import parse_log_text
from .report import render_markdown
from .store import EventStore

app = FastAPI(title="J.A.R.V.I.S.", version="1.0.0",
              description="Justified Analysis, Reasoning & Visualization of Incident Systems")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

store = EventStore()
runner = simulator.SimulationRunner(store)


class SimulateRequest(BaseModel):
    scenario: str = "db_pool_exhaustion"
    mode: str = "live"  # "live" streams in real time, "instant" injects back-dated events
    speed: float = 1.0


class SettingsRequest(BaseModel):
    window_seconds: float


# --- ingestion ----------------------------------------------------------------
@app.post("/api/events")
def ingest_events(payload: dict[str, Any] | list[dict[str, Any]]):
    """Source 1: push events/alerts over HTTP (single object, list, or {"events": [...]})."""
    if isinstance(payload, dict):
        raws = payload.get("events") if isinstance(payload.get("events"), list) else [payload]
    else:
        raws = payload
    added = store.add_raw(raws, source="api")
    return {"accepted": len(added), "ids": [e.id for e in added]}


@app.post("/api/logs/upload")
async def upload_logs(file: UploadFile = File(...)):
    """Source 2: upload a log file (JSON lines, plain text or logfmt)."""
    content = (await file.read()).decode("utf-8", errors="replace")
    raws, skipped = parse_log_text(content)
    if not raws:
        raise HTTPException(400, f"No parseable log lines found ({skipped} lines skipped).")
    added = store.add_raw(raws, source=f"log:{file.filename}")
    return {"accepted": len(added), "skipped": skipped, "filename": file.filename}


@app.get("/api/scenarios")
def scenarios():
    return [{"id": k, "name": v["name"], "description": v["description"]} for k, v in simulator.SCENARIOS.items()]


@app.post("/api/simulate")
def simulate(req: SimulateRequest):
    """Source 3: simulated live data from the payment platform."""
    if req.scenario not in simulator.SCENARIOS:
        raise HTTPException(404, f"Unknown scenario '{req.scenario}'")
    if req.mode == "instant":
        steps = simulator.build(req.scenario)
        base = datetime.now(timezone.utc) - timedelta(seconds=steps[-1][0] + 1)
        added = store.add_raw(simulator.materialize(req.scenario, base), source="simulator")
        return {"mode": "instant", "accepted": len(added)}
    return {"mode": "live", **runner.start(req.scenario, speed=max(0.25, min(req.speed, 10.0)))}


@app.post("/api/simulate/stop")
def simulate_stop():
    runner.stop()
    return runner.status


@app.post("/api/reset")
def reset():
    runner.stop()
    store.reset()
    return {"ok": True}


@app.post("/api/settings")
def settings(req: SettingsRequest):
    store.window_s = max(10.0, min(req.window_seconds, 3600.0))
    return {"window_seconds": store.window_s}


# --- analysis -----------------------------------------------------------------
def _summary(inc: dict) -> dict:
    keys = ("id", "title", "severity", "status", "started_at", "last_event_at", "duration_seconds",
            "alert_count", "event_count", "service_count", "summary")
    return {**{k: inc[k] for k in keys},
            "root_service": inc["root_cause"]["service"],
            "root_label": inc["root_cause"]["label"],
            "confidence": inc["root_cause"]["confidence"],
            "services": [s["service"] for s in inc["impact"]["affected_services"]]}


@app.get("/api/incidents")
def list_incidents():
    return [_summary(i) for i in store.incidents()]


@app.get("/api/incidents/{incident_id}")
def get_incident(incident_id: str):
    inc = store.incident(incident_id)
    if not inc:
        raise HTTPException(404, "Incident not found")
    return inc


@app.get("/api/incidents/{incident_id}/report", response_class=PlainTextResponse)
def get_report(incident_id: str):
    inc = store.incident(incident_id)
    if not inc:
        raise HTTPException(404, "Incident not found")
    return PlainTextResponse(render_markdown(inc), media_type="text/markdown",
                             headers={"Content-Disposition": f'inline; filename="{incident_id}.md"'})


@app.get("/api/events")
def list_events(limit: int = 100, service: str | None = None, anomalies_only: bool = False):
    events = store.events()
    if service:
        events = [e for e in events if e.service == service]
    if anomalies_only:
        events = [e for e in events if e.is_anomaly]
    incident_of = {eid: inc["id"] for inc in store.incidents() for eid in inc["event_ids"]}
    return [{**e.to_dict(), "incident_id": incident_of.get(e.id)} for e in reversed(events[-limit:])]


@app.get("/api/topology")
def topology():
    data = store.topology.to_dict()
    health: dict[str, dict] = {}
    for inc in store.incidents():
        if inc["status"] != "active":
            continue
        for s in inc["impact"]["affected_services"]:
            entry = health.setdefault(s["service"], {"status": "degraded", "incidents": []})
            entry["incidents"].append(inc["id"])
            if s["worst_severity"] in ("error", "critical"):
                entry["status"] = "failing"
        root = inc["root_cause"]["service"]
        health.setdefault(root, {"status": "failing", "incidents": [inc["id"]]})["root_cause"] = True
        for s in inc["impact"]["at_risk_services"]:
            health.setdefault(s["service"], {"status": "at_risk", "incidents": [inc["id"]]})
    for node in data["nodes"]:
        node["health"] = health.get(node["id"], {"status": "healthy", "incidents": []})
    return data


@app.get("/api/stats")
def stats():
    events = store.events()
    incidents = store.incidents()
    alerts = sum(1 for e in events if e.is_anomaly)
    return {
        "events": len(events),
        "alerts": alerts,
        "incidents": len(incidents),
        "active_incidents": sum(1 for i in incidents if i["status"] == "active"),
        "noise_reduction": round(100 * (1 - len(incidents) / alerts), 1) if alerts else 0,
        "sources": store.source_counts,
        "simulation": runner.status,
        "window_seconds": store.window_s,
        "active_window_seconds": ACTIVE_WINDOW_S,
    }


@app.get("/api/health")
def health():
    return {"ok": True}


# --- frontend -----------------------------------------------------------------
DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        candidate = (DIST / path).resolve()
        if path and candidate.is_relative_to(DIST) and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(DIST / "index.html")
