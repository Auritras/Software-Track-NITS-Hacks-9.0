import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { flushSync } from 'react-dom'
import CommandMenu, { MOD } from './CommandMenu.jsx'
import ReplayBar from './ReplayBar.jsx'
import { api, fmtTime } from './api.js'
import ServiceMap from './ServiceMap.jsx'
import IncidentDetail, { DetailSkeleton } from './IncidentDetail.jsx'
import { BrandMark, Button, CountUp, Skeleton, useBusy } from './ui.jsx'
import { AccuracyPanel, BreakPanel } from './Features.jsx'

const POLL_MS = 1500

export const sourceLabel = (s) => (s === 'api' ? 'API' : s === 'simulator' ? 'Simulated live' : s === 'alertmanager' ? 'Prometheus Alertmanager' : s === 'fault-injection' ? 'Break anything' : s.startsWith('log:') ? `Log file: ${s.slice(4)}` : s)
const REPO_URL = 'https://github.com/Auritras/Software-Track-NITS-Hacks-9.0'

function useToast() {
  const [toast, setToast] = useState(null)
  const timer = useRef()
  const show = useCallback((text, kind = 'ok', action = null) => {
    setToast({ text, kind, action, id: Date.now() })
    clearTimeout(timer.current)
    timer.current = setTimeout(() => setToast(null), action ? 7000 : 4000)
  }, [])
  const hide = useCallback(() => setToast(null), [])
  return [toast, show, hide]
}

function Stat({ label, value, tone = '', loading, decimals = 0, suffix = '' }) {
  return (
    <div className={`stat ${tone}`}>
      {loading ? <Skeleton kind="num" /> : <div className="stat-v"><CountUp value={value} decimals={decimals} suffix={suffix} /></div>}
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
  const [toast, showToast, hideToast] = useToast()
  const [cmdOpen, setCmdOpen] = useState(false)
  const [replayT, setReplayT] = useState(null)
  const [faultCatalog, setFaultCatalog] = useState({})
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
  useEffect(() => { api.faults().then(setFaultCatalog).catch(() => {}) }, [])
  useEffect(() => {
    const onKey = (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); setCmdOpen((o) => !o) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // Smooth crossfade between incidents where the browser supports view transitions.
  const selectIncident = useCallback((id) => {
    if (document.startViewTransition && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      document.startViewTransition(() => flushSync(() => setSelectedId(id)))
    } else {
      setSelectedId(id)
    }
  }, [])
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
  const undoReset = () => act('undo', async () => { hideToast(); return api.undoReset() }, (r) => `Restored ${r.restored} events`)
  const reset = () => run('reset', async () => {
    try {
      const r = await api.reset()
      setSelectedId(null); setDetail(null)
      showToast(r.cleared ? `Cleared ${r.cleared} events` : 'Nothing to clear', 'ok', r.cleared ? { label: 'Undo', onClick: undoReset } : null)
      await refresh()
    } catch (e) { showToast(e.message, 'err') }
  })
  const breakService = (service, fault) => act('break', () => api.injectFault(service, fault, 'live', 1),
    (r) => `${topology?.nodes.find((n) => n.id === service)?.label || service} broken. Failure spreading to ${r.affected.length - 1} dependent services.`)
  const pushAlert = () => act('push', () => api.sendEvent({
    service: 'api-gateway', level: 'warning', message: 'p95 latency 1.9s on /api/v1/accounts',
  }), () => 'Alert received via POST /api/events')
  // Judge mode: one click (or ?demo=live) replays the headline incident from a clean slate.
  const replayDemo = () => act('replay', async () => {
    await api.reset()
    setSelectedId(null); setDetail(null)
    return api.simulate('db_pool_exhaustion', 'live', 2)
  }, () => 'Replaying the payment-platform incident live. Watch the map.')
  const [introOpen, setIntroOpen] = useState(() => {
    try { return localStorage.getItem('jarvis-intro-dismissed') !== '1' } catch { return true }
  })
  const dismissIntro = () => {
    setIntroOpen(false)
    try { localStorage.setItem('jarvis-intro-dismissed', '1') } catch { /* storage unavailable */ }
  }
  const demoStarted = useRef(false)
  useEffect(() => {
    if (demoStarted.current) return
    const params = new URLSearchParams(window.location.search)
    if (params.get('demo') === 'live') {
      demoStarted.current = true
      params.delete('demo')
      const qs = params.toString()
      window.history.replaceState(null, '', window.location.pathname + (qs ? `?${qs}` : ''))
      replayDemo()
    }
  }, [])

  const upload = (e) => {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    act('upload', () => api.upload(file),
      (r) => `Ingested ${r.accepted} lines from ${r.filename}${r.skipped ? `, ${r.skipped} skipped` : ''}`)
  }

  const commands = useMemo(() => {
    const list = []
    scenarios.forEach((s) => {
      list.push({ id: `live-${s.id}`, group: 'Scenario', label: `Simulate live: ${s.name}`, run: () => act('live', () => api.simulate(s.id, 'live', speed), () => `Streaming ${s.name}`) })
    })
    const nodes = topology?.nodes || []
    nodes.forEach((n) => {
      (faultCatalog[n.id] || []).forEach((f) => {
        list.push({ id: `break-${n.id}-${f.id}`, group: 'Break anything', label: `Break ${n.label}: ${f.label}`, run: () => breakService(n.id, f.id) })
      })
    })
    incidents.forEach((i) => {
      list.push({ id: `open-${i.id}`, group: 'Incident', label: `Open ${i.id}: ${i.title}`, hint: i.severity, run: () => selectIncident(i.id) })
    })
    list.push(
      { id: 'upload', group: 'Data', label: 'Upload a log file', run: () => fileRef.current?.click() },
      { id: 'push', group: 'Data', label: 'Push a sample alert via the API', run: pushAlert },
      { id: 'reset', group: 'Data', label: 'Reset all data (can be undone)', run: reset },
    )
    return list
  }, [scenarios, topology, faultCatalog, incidents, speed])

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
            <div className="theme-tags"><span className="chip tag">Intelligent Systems</span><span className="chip tag">AI</span><span className="chip tag">Distributed Systems</span></div>
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
          <Button onClick={() => setCmdOpen(true)} title="Open the command menu">
            Commands <span className="kbd-hint"><kbd>{MOD}</kbd> <kbd>K</kbd></span>
          </Button>
        </div>
      </header>

      {offline && (
        <div className="banner" role="alert">
          Can't reach the backend. Start it with <code>uvicorn app.main:app --app-dir backend</code> and this page will reconnect automatically.
        </div>
      )}

      {introOpen && (
        <section className="panel intro" aria-labelledby="intro-title">
          <div className="intro-copy">
            <h2 id="intro-title">Dozens of alerts. One real problem.</h2>
            <p className="panel-copy">Login failures, slow APIs and failed payments look like separate alerts. J.A.R.V.I.S. links them, finds the root cause, explains why, and tells you what to fix first.</p>
            <ol className="intro-steps">
              <li><b>Watch</b> an outage stream in live</li>
              <li><b>See</b> 49 alerts become one incident with a root cause</li>
              <li><b>Break</b> any service yourself and watch it get diagnosed</li>
            </ol>
          </div>
          <div className="intro-actions">
            <Button variant="primary" onClick={replayDemo} loading={busy === 'replay'}>Replay the incident live</Button>
            <Button onClick={() => document.getElementById('break-title')?.scrollIntoView({ behavior: 'smooth', block: 'center' })}>Break a service</Button>
            <Button variant="quiet" onClick={dismissIntro}>Dismiss</Button>
          </div>
        </section>
      )}

      <div className="stats">
        <Stat label="Events ingested" value={stats?.events ?? 0} loading={!loaded} />
        <Stat label="Raw alerts" value={stats?.alerts ?? 0} loading={!loaded} />
        <Stat label="Correlated incidents" value={stats?.incidents ?? 0} tone="accent" loading={!loaded} />
        <Stat label="Alert noise reduction" value={stats?.noise_reduction ?? 0} decimals={1} suffix="%" tone="good" loading={!loaded} />
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
            ? <ServiceMap topology={topology} incident={detail} selectedService={serviceFilter} onSelectService={setServiceFilter} replayT={replayT} />
            : <Skeleton kind="map" />}
          {detail && <ReplayBar key={detail.id} incident={detail} value={replayT} onChange={setReplayT} />}
        </section>

        <div className="side-col">
        <BreakPanel
          topology={topology}
          service={serviceFilter}
          onServiceChange={setServiceFilter}
          streaming={streaming}
          onInjected={(text) => { showToast(text); refresh() }}
          onError={(text) => showToast(text, 'err')}
        />
        <section className="panel incidents-panel" aria-labelledby="incidents-title">
          <div className="panel-head">
            <h2 className="panel-title" id="incidents-title">Related incidents</h2>
            <span className="muted small">{incidents.length} total</span>
          </div>
          {!loaded && <div className="incident-list">{[0, 1, 2].map((i) => <Skeleton key={i} kind="block" />)}</div>}
          {loaded && incidents.length === 0 && <div className="empty">No incidents. All services are reporting normally.</div>}
          <div className="incident-list">
            {incidents.map((i) => (
              <button type="button" key={i.id} className={`incident-card ${i.id === selectedId ? 'active' : ''}`} onClick={() => selectIncident(i.id)} aria-pressed={i.id === selectedId}>
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
      </div>

      {detailLoading ? <DetailSkeleton /> : (loaded ? <IncidentDetail incident={detail} replayT={replayT} /> : <DetailSkeleton />)}

      <AccuracyPanel />

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
                  <td>{e.incident_id ? <button type="button" className="link mono" onClick={() => selectIncident(e.incident_id)}>{e.incident_id}</button> : <span className="muted">-</span>}</td>
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
      {toast && (
        <div className={`toast ${toast.kind}`} role="status" key={toast.id}>
          <span>{toast.text}</span>
          {toast.action && <Button size="sm" onClick={toast.action.onClick} loading={busy === 'undo'}>{toast.action.label}</Button>}
        </div>
      )}
      <CommandMenu open={cmdOpen} onClose={() => setCmdOpen(false)} commands={commands} />
    </div>
  )
}
