import React, { useCallback, useEffect, useRef, useState } from 'react'
import { api, fmtTime } from './api.js'
import ServiceMap from './ServiceMap.jsx'
import IncidentDetail, { DetailSkeleton } from './IncidentDetail.jsx'
import { BrandMark, Button, Skeleton, useBusy } from './ui.jsx'

const POLL_MS = 1500

export const sourceLabel = (s) => (s === 'api' ? 'API' : s === 'simulator' ? 'Simulated live' : s.startsWith('log:') ? `Log file: ${s.slice(4)}` : s)
const REPO_URL = 'https://github.com/Auritras/Software-Track-NITS-Hacks-9.0'

function useToast() {
  const [toast, setToast] = useState(null)
  const timer = useRef()
  const show = useCallback((text, kind = 'ok') => {
    setToast({ text, kind })
    clearTimeout(timer.current)
    timer.current = setTimeout(() => setToast(null), 4000)
  }, [])
  return [toast, show]
}

function Stat({ label, value, tone = '', loading }) {
  return (
    <div className={`stat ${tone}`}>
      {loading ? <Skeleton kind="num" /> : <div className="stat-v">{value}</div>}
      <div className="stat-l">{label}</div>
    </div>
  )
}

export default function App() {
  const [loaded, setLoaded] = useState(false)
  const [stats, setStats] = useState(null)
  const [incidents, setIncidents] = useState([])
  const [topology, setTopology] = useState(null)
  const [events, setEvents] = useState([])
  const [scenarios, setScenarios] = useState([])
  const [scenario, setScenario] = useState('db_pool_exhaustion')
  const [speed, setSpeed] = useState(1)
  const [selectedId, setSelectedId] = useState(null)
  const [detail, setDetail] = useState(null)
  const [serviceFilter, setServiceFilter] = useState(null)
  const [anomaliesOnly, setAnomaliesOnly] = useState(false)
  const [offline, setOffline] = useState(false)
  const [toast, showToast] = useToast()
  const [busy, run] = useBusy()
  const fileRef = useRef()

  const refresh = useCallback(async () => {
    try {
      const [s, inc, topo, ev] = await Promise.all([
        api.stats(), api.incidents(), api.topology(), api.events(120, anomaliesOnly),
      ])
      setStats(s); setIncidents(inc); setTopology(topo); setEvents(ev); setOffline(false); setLoaded(true)
      setSelectedId((cur) => (cur && inc.some((i) => i.id === cur) ? cur : inc[0]?.id ?? null))
    } catch {
      setOffline(true)
    }
  }, [anomaliesOnly])

  useEffect(() => { api.scenarios().then(setScenarios).catch(() => {}) }, [])
  useEffect(() => {
    refresh()
    const t = setInterval(refresh, POLL_MS)
    return () => clearInterval(t)
  }, [refresh])
  useEffect(() => {
    if (!selectedId) { setDetail(null); return }
    let alive = true
    api.incident(selectedId).then((d) => alive && setDetail(d)).catch(() => {})
    return () => { alive = false }
  }, [selectedId, stats?.events, stats?.incidents, stats?.active_incidents])

  const act = (key, fn, ok) => run(key, async () => {
    try {
      const result = await fn()
      if (ok) showToast(ok(result))
      await refresh()
    } catch (e) {
      showToast(e.message || 'Request failed', 'err')
    }
  })

  const simulateLive = () => act('live', () => api.simulate(scenario, 'live', speed),
    (r) => `Streaming scenario live for about ${Math.round(r.duration_seconds)} seconds`)
  const inject = () => act('instant', () => api.simulate(scenario, 'instant', speed),
    (r) => `Injected ${r.accepted} events`)
  const reset = () => act('reset', async () => { await api.reset(); setSelectedId(null); setDetail(null) }, () => 'All data cleared')
  const pushAlert = () => act('push', () => api.sendEvent({
    service: 'api-gateway', level: 'warning', message: 'p95 latency 1.9s on /api/v1/accounts',
  }), () => 'Alert received via POST /api/events')
  const upload = (e) => {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    act('upload', () => api.upload(file),
      (r) => `Ingested ${r.accepted} lines from ${r.filename}${r.skipped ? `, ${r.skipped} skipped` : ''}`)
  }

  const sim = stats?.simulation
  const streaming = Boolean(sim?.running)
  const shownEvents = serviceFilter ? events.filter((e) => e.service === serviceFilter) : events
  const detailLoading = selectedId && (!detail || detail.id !== selectedId)
  const description = scenarios.find((s) => s.id === scenario)?.description

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <BrandMark />
          <div>
            <h1>J.A.R.V.I.S.</h1>
            <div className="tagline">Justified Analysis, Reasoning &amp; Visualization of Incident Systems</div>
          </div>
        </div>
        <div className="controls">
          <label className="sr-only" htmlFor="scenario">Scenario</label>
          <select id="scenario" value={scenario} onChange={(e) => setScenario(e.target.value)} title={description}>
            {scenarios.length === 0 && <option value="db_pool_exhaustion">Loading scenarios…</option>}
            {scenarios.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
          <label className="sr-only" htmlFor="speed">Playback speed</label>
          <select id="speed" value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>
            {[1, 2, 4].map((v) => <option key={v} value={v}>{v}× speed</option>)}
          </select>
          <Button variant="primary" onClick={simulateLive} loading={busy === 'live' || streaming}>
            {streaming ? `Streaming ${sim.emitted} of ${sim.total}` : 'Simulate live'}
          </Button>
          <Button onClick={inject} loading={busy === 'instant'}>Inject instantly</Button>
          <span className="divider" aria-hidden="true" />
          <Button onClick={() => fileRef.current?.click()} loading={busy === 'upload'}>Upload logs</Button>
          <input ref={fileRef} type="file" accept=".log,.txt,.jsonl,.json" hidden onChange={upload} />
          <Button variant="quiet" onClick={pushAlert} loading={busy === 'push'} title="Sends a sample alert to POST /api/events">Push API alert</Button>
          <Button variant="quiet danger" onClick={reset} loading={busy === 'reset'}>Reset</Button>
        </div>
      </header>

      {offline && (
        <div className="banner" role="alert">
          Can't reach the backend. Start it with <code>uvicorn app.main:app --app-dir backend</code> and this page will reconnect automatically.
        </div>
      )}

      <div className="stats">
        <Stat label="Events ingested" value={stats?.events ?? 0} loading={!loaded} />
        <Stat label="Raw alerts" value={stats?.alerts ?? 0} loading={!loaded} />
        <Stat label="Correlated incidents" value={stats?.incidents ?? 0} tone="accent" loading={!loaded} />
        <Stat label="Alert noise reduction" value={`${stats?.noise_reduction ?? 0}%`} tone="good" loading={!loaded} />
        <Stat label="Active incidents" value={stats?.active_incidents ?? 0} loading={!loaded} />
        <div className="stat wide">
          <div className="sources">
            {!loaded && <Skeleton width="60%" />}
            {loaded && Object.entries(stats?.sources || {}).map(([k, v]) => <span key={k} className="chip">{sourceLabel(k)} · {v}</span>)}
            {loaded && !Object.keys(stats?.sources || {}).length && <span className="muted small">No data received yet</span>}
          </div>
          <div className="stat-l">Data sources: APIs, uploaded log files, simulated live data</div>
        </div>
      </div>

      <div className="main-grid">
        <section className="panel" aria-labelledby="map-title">
          <div className="panel-head">
            <h2 className="panel-title" id="map-title">Service connections</h2>
            <div className="legend">
              <span><i className="lg healthy" />Healthy</span><span><i className="lg at_risk" />At risk</span>
              <span><i className="lg degraded" />Degraded</span><span><i className="lg failing" />Failing</span>
              <span><i className="lg hot" />Failure path</span>
            </div>
          </div>
          {topology
            ? <ServiceMap topology={topology} incident={detail} selectedService={serviceFilter} onSelectService={setServiceFilter} />
            : <Skeleton kind="map" />}
        </section>

        <section className="panel incidents-panel" aria-labelledby="incidents-title">
          <div className="panel-head">
            <h2 className="panel-title" id="incidents-title">Related incidents</h2>
            <span className="muted small">{incidents.length} total</span>
          </div>
          {!loaded && <div className="incident-list">{[0, 1, 2].map((i) => <Skeleton key={i} kind="block" />)}</div>}
          {loaded && incidents.length === 0 && <div className="empty">No incidents. All services are reporting normally.</div>}
          <div className="incident-list">
            {incidents.map((i) => (
              <button type="button" key={i.id} className={`incident-card ${i.id === selectedId ? 'active' : ''}`} onClick={() => setSelectedId(i.id)} aria-pressed={i.id === selectedId}>
                <div className="row between">
                  <span className={`sev ${i.severity}`}>{i.severity}</span>
                  <span className={`status ${i.status}`}>{i.status === 'active' ? 'Active' : 'Resolved'}</span>
                </div>
                <div className="ic-title">{i.title}</div>
                <div className="ic-meta">{i.alert_count} alerts grouped · {i.service_count} services · {i.confidence}% confidence</div>
                <div className="ic-meta muted">{i.id} · {fmtTime(i.started_at)}</div>
              </button>
            ))}
          </div>
        </section>
      </div>

      {detailLoading ? <DetailSkeleton /> : (loaded ? <IncidentDetail incident={detail} /> : <DetailSkeleton />)}

      <section className="panel" aria-labelledby="stream-title">
        <div className="panel-head">
          <div className="row">
            <h2 className="panel-title" id="stream-title">Collected events</h2>
            {serviceFilter && (
              <span className="chip">{serviceFilter}<button type="button" className="x" onClick={() => setServiceFilter(null)} aria-label="Clear service filter">×</button></span>
            )}
          </div>
          <label className="toggle"><input type="checkbox" checked={anomaliesOnly} onChange={(e) => setAnomaliesOnly(e.target.checked)} /> Anomalies only</label>
        </div>
        <div className="stream">
          <table>
            <thead><tr><th scope="col">Time</th><th scope="col">Source</th><th scope="col">Service</th><th scope="col">Severity</th><th scope="col">Type</th><th scope="col">Message</th><th scope="col">Incident</th></tr></thead>
            <tbody>
              {shownEvents.slice(0, 80).map((e) => (
                <tr key={e.id} className={e.severity}>
                  <td>{fmtTime(e.timestamp)}</td>
                  <td className="small">{sourceLabel(e.source)}</td>
                  <td>{e.service}</td>
                  <td><span className={`badge ${e.severity}`}>{e.severity}</span></td>
                  <td className="small">{e.type_label}</td>
                  <td className="msg">{e.message}</td>
                  <td>{e.incident_id ? <button type="button" className="link mono" onClick={() => setSelectedId(e.incident_id)}>{e.incident_id}</button> : <span className="muted">-</span>}</td>
                </tr>
              ))}
              {loaded && shownEvents.length === 0 && <tr><td colSpan={7} className="empty">No events yet. Start a simulation or upload a log file.</td></tr>}
            </tbody>
          </table>
        </div>
      </section>

      <footer className="foot">
        <span>J.A.R.V.I.S. · NITS Hacks 9.0 Software Track · Problem Statement 1 · Intelligent Systems, AI, Distributed Systems</span>
        <nav aria-label="Project links">
          <a href={REPO_URL} target="_blank" rel="noreferrer">GitHub repository</a>
          <a href="/docs" target="_blank" rel="noreferrer">API documentation</a>
          <a href="/api/samples/payment-platform-outage.log" download>Sample log file</a>
        </nav>
      </footer>

      <div aria-live="polite" className="sr-only">{toast?.text}</div>
      {toast && <div className={`toast ${toast.kind}`} role="status">{toast.text}</div>}
    </div>
  )
}
