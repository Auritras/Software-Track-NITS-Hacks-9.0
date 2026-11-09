import React, { useCallback, useEffect, useRef, useState } from 'react'
import { api, fmtTime } from './api.js'
import ServiceMap from './ServiceMap.jsx'
import IncidentDetail from './IncidentDetail.jsx'

const POLL_MS = 1500

function useToast() {
  const [toast, setToast] = useState(null)
  const timer = useRef()
  const show = useCallback((text, kind = 'ok') => {
    setToast({ text, kind })
    clearTimeout(timer.current)
    timer.current = setTimeout(() => setToast(null), 3500)
  }, [])
  return [toast, show]
}

export default function App() {
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
  const fileRef = useRef()

  const refresh = useCallback(async () => {
    try {
      const [s, inc, topo, ev] = await Promise.all([
        api.stats(), api.incidents(), api.topology(), api.events(120, anomaliesOnly),
      ])
      setStats(s); setIncidents(inc); setTopology(topo); setEvents(ev); setOffline(false)
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

  const run = async (mode) => {
    try {
      const r = await api.simulate(scenario, mode, speed)
      showToast(mode === 'live' ? `Streaming "${scenario}" live (~${Math.round(r.duration_seconds)}s)` : `Injected ${r.accepted} events`)
      refresh()
    } catch (e) { showToast(e.message, 'err') }
  }
  const reset = async () => { await api.reset(); setSelectedId(null); setDetail(null); showToast('All data cleared'); refresh() }
  const upload = async (e) => {
    const file = e.target.files?.[0]
    if (!file) return
    try {
      const r = await api.upload(file)
      showToast(`Ingested ${r.accepted} log lines from ${r.filename}${r.skipped ? ` (${r.skipped} skipped)` : ''}`)
      refresh()
    } catch (err) { showToast(err.message, 'err') }
    e.target.value = ''
  }
  const sendTestAlert = async () => {
    await api.sendEvent({ service: 'api-gateway', level: 'warning', message: 'p95 latency 1.9s on /api/v1/accounts (pushed via API)' })
    showToast('Alert pushed via POST /api/events')
    refresh()
  }

  const sim = stats?.simulation
  const shownEvents = serviceFilter ? events.filter((e) => e.service === serviceFilter) : events

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <div className="logo"><span /></div>
          <div>
            <h1>J.A.R.V.I.S.</h1>
            <div className="tagline">Justified Analysis, Reasoning &amp; Visualization of Incident Systems</div>
          </div>
        </div>
        <div className="controls">
          <select value={scenario} onChange={(e) => setScenario(e.target.value)} title={scenarios.find((s) => s.id === scenario)?.description}>
            {scenarios.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
          <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))} title="Playback speed">
            {[1, 2, 4].map((v) => <option key={v} value={v}>{v}x</option>)}
          </select>
          <button className="btn primary" onClick={() => run('live')} disabled={sim?.running}>
            {sim?.running ? `Streaming ${sim.emitted}/${sim.total}` : '▶ Simulate live'}
          </button>
          <button className="btn" onClick={() => run('instant')}>Inject instantly</button>
          <button className="btn" onClick={() => fileRef.current?.click()}>Upload logs</button>
          <input ref={fileRef} type="file" accept=".log,.txt,.jsonl,.json" hidden onChange={upload} />
          <button className="btn ghost" onClick={sendTestAlert} title="POST a sample alert to /api/events">Push API alert</button>
          <button className="btn ghost danger" onClick={reset}>Reset</button>
        </div>
      </header>

      {offline && <div className="banner">Backend unreachable - start it with <code>uvicorn app.main:app</code></div>}

      <div className="stats">
        <div className="stat"><div className="stat-v">{stats?.events ?? 0}</div><div className="stat-l">events ingested</div></div>
        <div className="stat"><div className="stat-v">{stats?.alerts ?? 0}</div><div className="stat-l">raw alerts</div></div>
        <div className="stat accent"><div className="stat-v">{stats?.incidents ?? 0}</div><div className="stat-l">correlated incidents</div></div>
        <div className="stat good"><div className="stat-v">{stats?.noise_reduction ?? 0}%</div><div className="stat-l">alert noise reduction</div></div>
        <div className="stat"><div className="stat-v">{stats?.active_incidents ?? 0}</div><div className="stat-l">active now</div></div>
        <div className="stat sources">
          {Object.entries(stats?.sources || {}).map(([k, v]) => <span key={k} className="chip">{k} · {v}</span>)}
          {!Object.keys(stats?.sources || {}).length && <span className="muted small">no data sources yet</span>}
          <div className="stat-l">data sources</div>
        </div>
      </div>

      <div className="main-grid">
        <section className="panel map-panel">
          <div className="row between">
            <h3 className="panel-title">Service dependency map</h3>
            <div className="legend">
              <span><i className="lg healthy" />healthy</span><span><i className="lg at_risk" />at risk</span>
              <span><i className="lg degraded" />degraded</span><span><i className="lg failing" />failing</span>
              <span><i className="lg hot" />failure propagation</span>
            </div>
          </div>
          <ServiceMap topology={topology} incident={detail} selectedService={serviceFilter} onSelectService={setServiceFilter} />
        </section>

        <section className="panel incidents-panel">
          <h3 className="panel-title">Incidents <span className="muted small">{incidents.length}</span></h3>
          {incidents.length === 0 && <div className="empty">No incidents. Everything is nominal.</div>}
          <div className="incident-list">
            {incidents.map((i) => (
              <button key={i.id} className={`incident-card ${i.id === selectedId ? 'active' : ''}`} onClick={() => setSelectedId(i.id)}>
                <div className="row between">
                  <span className={`sev ${i.severity}`}>{i.severity}</span>
                  <span className={`status ${i.status}`}>{i.status}</span>
                </div>
                <div className="ic-title">{i.title}</div>
                <div className="ic-meta mono">
                  {i.alert_count} alerts → 1 incident · {i.service_count} services · {i.confidence}% conf.
                </div>
                <div className="ic-meta muted">{i.id} · {fmtTime(i.started_at)}</div>
              </button>
            ))}
          </div>
        </section>
      </div>

      <IncidentDetail incident={detail} />

      <section className="panel">
        <div className="row between">
          <h3 className="panel-title">
            Live event stream {serviceFilter && <span className="chip">{serviceFilter} <button className="x" onClick={() => setServiceFilter(null)}>×</button></span>}
          </h3>
          <label className="toggle"><input type="checkbox" checked={anomaliesOnly} onChange={(e) => setAnomaliesOnly(e.target.checked)} /> anomalies only</label>
        </div>
        <div className="stream">
          <table>
            <thead><tr><th>Time</th><th>Source</th><th>Service</th><th>Severity</th><th>Type</th><th>Message</th><th>Incident</th></tr></thead>
            <tbody>
              {shownEvents.slice(0, 80).map((e) => (
                <tr key={e.id} className={e.severity}>
                  <td className="mono">{fmtTime(e.timestamp)}</td>
                  <td className="muted small">{e.source}</td>
                  <td>{e.service}</td>
                  <td><span className={`badge ${e.severity}`}>{e.severity}</span></td>
                  <td className="muted small">{e.type_label}</td>
                  <td className="msg">{e.message}</td>
                  <td>{e.incident_id ? <button className="link mono" onClick={() => setSelectedId(e.incident_id)}>{e.incident_id}</button> : <span className="muted">-</span>}</td>
                </tr>
              ))}
              {shownEvents.length === 0 && <tr><td colSpan={7} className="empty">Waiting for events…</td></tr>}
            </tbody>
          </table>
        </div>
      </section>

      <footer className="foot muted small">
        Sources: simulated live platform · log upload (JSON lines, text, logfmt) · REST <code>POST /api/events</code> · correlation window {stats?.window_seconds ?? 120}s
      </footer>

      {toast && <div className={`toast ${toast.kind}`}>{toast.text}</div>}
    </div>
  )
}
