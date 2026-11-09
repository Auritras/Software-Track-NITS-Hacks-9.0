# J.A.R.V.I.S.
**Justified Analysis, Reasoning & Visualization of Incident Systems**
NITS Hacks 9.0, Software Track, Problem Statement 1

Monitoring tools send a separate alert for every symptom. When a payment platform's database runs out of connections, on-call engineers see login failures, slow APIs and failed transactions as unrelated alerts.

J.A.R.V.I.S. collects events from several sources and works out which ones are connected. It then identifies the most likely root cause, with evidence you can check, maps the impact, shows how the failure spread, and suggests fixes in a report people can read.

> Demo scenario: **49 alerts from 7 services → 1 incident → "Primary DB: connection pool exhausted" at 82% confidence**, with an unrelated analytics slowdown correctly kept as a separate incident.

## Features (mapped to the problem statement)

| Requirement | Implementation |
|---|---|
| Multiple data sources | REST API (`POST /api/events`), log file upload (JSON lines, plain text, logfmt), simulated live payment platform |
| Event correlation | Uses **timestamps** (a sliding time window), **common services** (same service or connected in the dependency graph, plus shared `trace_id`/`request_id`/`host`) and **activity patterns** (the same error signature on different services once ids and numbers are stripped). Union-find merges the linked events, and the reason for every link is recorded and shown |
| Root cause detection | Explainable scoring of every candidate service: **coverage**, **precedence**, **depth** and **causal signal** |
| Impact analysis | Affected services, at-risk services (dependents not yet alerting), business capabilities, failed logins, failed transactions, revenue at risk, users affected |
| Incident timeline | Ordered events plus the propagation path from the root to each symptom, animated on the service map |
| Smart recommendations | Rule base keyed on root-cause component type and failure type, plus mitigations for the observed symptoms, ordered by priority (immediate / short / long term) with runnable commands |
| Interactive dashboard | Live service map, incident list, root-cause panel, impact panel, timeline, recommendations, event stream, downloadable Markdown report |

## How root cause ranking works
Every service in an incident gets a score made of four factors:

- **Coverage (35%)**: the share of affected services that depend on this one. If everything failing depends on X, X explains the incident.
- **Precedence (25%)**: how early this service started failing relative to the others.
- **Depth (20%)**: 1 if none of this service's own dependencies are failing, meaning it's the deepest failing component.
- **Causal signal (20%)**: whether it emitted a cause-type event (connection pool exhausted, OOM, crash, deploy, config change, provider outage) rather than just a symptom (timeouts, 5xx, latency).

Confidence combines the top score with its margin over the runner-up. The dashboard shows every factor for every candidate, so the conclusion can always be checked.

## Run locally
```bash
# backend (Python 3.11+)
cd backend
pip install -r requirements-dev.txt
uvicorn app.main:app --reload --port 8000

# frontend (Node 18+), in a second terminal
cd frontend
npm install
npm run dev        # http://localhost:5173 (proxies /api to :8000)
```
Production mode: run `npm run build` in `frontend/`, then FastAPI serves the dashboard at http://localhost:8000.

Tests: `cd backend && pytest`. They check root-cause accuracy across 4 scenarios × 4 random seeds, noise separation, log parsing and the API.

## Demo flow (under 2 minutes)
1. Choose **Primary DB connection pool exhaustion**, then click **▶ Simulate live**.
2. Watch the raw alerts stream in from auth, payments, the gateway and the apps, all appearing to be separate problems.
3. J.A.R.V.I.S. merges them into one SEV1. Primary DB is marked **ROOT CAUSE** on the map, with the failure path animated.
4. Walk through the evidence, the candidate scoring, the impact (₹ at risk, failed logins) and the recommendations.
5. Point out the unrelated warehouse slowdown, which stays a separate SEV3.
6. **Upload logs** → `samples/payment-platform-outage.log` (mixed formats), which J.A.R.V.I.S. diagnoses as a Redis OOM caused by a config change.
7. Download the incident report.

## API
| Method | Path | Purpose |
|---|---|---|
| POST | `/api/events` | Push one event, a list, or `{"events": [...]}` |
| POST | `/api/logs/upload` | Upload a log file (multipart `file`) |
| POST | `/api/simulate` | `{"scenario": "db_pool_exhaustion", "mode": "live" or "instant", "speed": 1}` |
| GET | `/api/incidents` and `/api/incidents/{id}` | Incident list and full analysis |
| GET | `/api/incidents/{id}/report` | Markdown incident report |
| GET | `/api/topology` | Service graph with live health |
| GET | `/api/events`, `/api/stats` | Event stream and statistics |
| POST | `/api/reset` | Clear all data |

Interactive docs are at `/docs`.

Example:
```bash
curl -X POST localhost:8000/api/events -H "Content-Type: application/json" \
  -d '{"service":"payment-service","level":"error","message":"Transaction TXN-1 failed: db unavailable","amount":2500}'
```

## Deploy (free)
Deploy to Render as a single Docker web service: `render.yaml` and `Dockerfile` are included. Connect the GitHub repo on Render and it builds the dashboard and API together. The free plan sleeps when idle, so open the URL once before judging.

## Tech stack
Python, FastAPI, React 18, Vite, and an SVG service map. No paid services. Reports are generated from templates, so they work offline and are deterministic.

## Roadmap
- Learn the service topology automatically from traces (OpenTelemetry) instead of configuring it
- Infer "silent" root causes, i.e. unmonitored shared dependencies
- Optional LLM-written incident narratives
- Integrations: Prometheus Alertmanager webhook, Slack and PagerDuty notifications
