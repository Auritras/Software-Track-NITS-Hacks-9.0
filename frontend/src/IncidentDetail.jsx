import React, { useEffect, useState } from 'react'
import { api, fmtMoney, fmtNum, fmtTime } from './api.js'
import { Button, CountUp, Skeleton, useBusy } from './ui.jsx'

const FACTORS = [
  ['coverage', 'Share of affected services that depend on this one'],
  ['precedence', 'How early this service started failing'],
  ['depth', '1 when none of its own dependencies are failing'],
  ['causal', 'Emitted a cause-level signal such as pool exhaustion, OOM or a deploy'],
  ['intensity', 'How sustained the failure was: repeated errors outweigh a one-off warning'],
]
const LINK_LABEL = { dependency: 'Common services', same_service: 'Same service', shared_id: 'Shared trace or host', pattern: 'Activity pattern' }
const LINK_INFO = {
  dependency: 'Linked because the services are connected in the dependency graph',
  same_service: 'Repeated alerts from the same service',
  shared_id: 'Events share a trace, request or host id',
  pattern: 'Different services reported the same error signature',
}
const PRIORITY = { immediate: 'Immediate', short_term: 'Short term', long_term: 'Long term' }

function ConfidenceRing({ value }) {
  const r = 30
  const c = 2 * Math.PI * r
  return (
    <svg className="ring" viewBox="0 0 80 80" role="img" aria-label={`Confidence ${value} percent`}>
      <circle cx="40" cy="40" r={r} className="ring-bg" />
      <circle cx="40" cy="40" r={r} className="ring-fg" strokeDasharray={`${(c * value) / 100} ${c}`} transform="rotate(-90 40 40)" />
      <circle cx="40" cy="40" r={r + 5} className="ring-edge" />
      <circle cx="40" cy="40" r={r - 5} className="ring-edge" />
      <text x="40" y="46" textAnchor="middle" className="ring-value">{value}%</text>
    </svg>
  )
}

const Bar = ({ value }) => <span className="bar" role="img" aria-label={`${Math.round(value * 100)}%`}><span style={{ width: `${Math.round(value * 100)}%` }} /></span>

export function DetailSkeleton() {
  return (
    <div className="detail" aria-busy="true" aria-label="Loading incident">
      <div className="panel"><Skeleton width="30%" /><div style={{ height: 12 }} /><Skeleton width="55%" /><div style={{ height: 8 }} /><Skeleton width="80%" /></div>
      <div className="detail-grid">
        {[0, 1, 2, 3].map((i) => <div key={i} className="panel"><Skeleton width="40%" /><div style={{ height: 16 }} /><Skeleton kind="block" /></div>)}
      </div>
    </div>
  )
}

export default function IncidentDetail({ incident, replayT = null }) {
  const [showAll, setShowAll] = useState(false)
  const [report, setReport] = useState(null)
  const [busy, run] = useBusy()

  useEffect(() => {
    if (!report) return
    const onKey = (e) => { if (e.key === 'Escape') setReport(null) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [report])

  if (!incident) {
    return (
      <div className="panel empty-state">
        <h3>No incidents yet</h3>
        <p>Run a scenario, upload a log file or push an alert to the API. Related alerts are grouped into incidents and analysed here.</p>
      </div>
    )
  }

  const rc = incident.root_cause
  const m = incident.impact.metrics
  const events = showAll ? incident.timeline.events : incident.timeline.events.filter((e) => e.milestone || e.severity === 'critical')

  const viewReport = () => run('view', async () => setReport(await api.report(incident.id)))
  const download = () => run('download', async () => {
    const text = await api.report(incident.id)
    const url = URL.createObjectURL(new Blob([text], { type: 'text/markdown' }))
    const a = Object.assign(document.createElement('a'), { href: url, download: `${incident.id}.md` })
    a.click()
    URL.revokeObjectURL(url)
  })

  return (
    <div className="detail fade-in" key={incident.id}>
      <section className="panel detail-head" aria-labelledby="incident-title">
        <div>
          <div className="detail-meta">
            <span className={`sev ${incident.severity}`}>{incident.severity}</span>
            <span className={`status ${incident.status}`}>{incident.status === 'active' ? 'Active' : 'Resolved'}</span>
            <span className="mono muted">{incident.id}</span>
            <span className="muted small">Started {fmtTime(incident.started_at)} · {Math.round(incident.duration_seconds)}s</span>
          </div>
          <h2 id="incident-title">{incident.title}</h2>
          <div className="collapse-line" key={`collapse-${incident.id}`} aria-label={`${incident.alert_count} alerts grouped into 1 incident`}>
            <span className="c-from"><CountUp value={incident.alert_count} duration={900} /> alerts</span>
            <span className="c-arrow" aria-hidden="true">→</span>
            <span className="c-to">1 incident</span>
          </div>
          <p className="summary">{incident.summary}</p>
          <div className="correlation" aria-label="Event correlation">
            <span className="corr-label">Event correlation</span>
            <span className="chip">{incident.event_count} events in {Math.round(incident.correlation.span_seconds)}s</span>
            {Object.entries(incident.correlation.links).filter(([, n]) => n > 0).map(([reason, n]) => (
              <span key={reason} className="chip" title={LINK_INFO[reason]}>{LINK_LABEL[reason]} · {n}</span>
            ))}
          </div>
        </div>
        <div className="row">
          <Button variant="quiet" loading={busy === 'view'} onClick={viewReport}>View report</Button>
          <Button loading={busy === 'download'} onClick={download}>Download report</Button>
        </div>
      </section>

      <div className="detail-grid">
        <section className="panel rc-panel">
          <div className="burst-wrap" key={`${incident.id}:${rc.service}`} aria-hidden="true">
            <div className="burst"><span>Root cause found!</span></div>
          </div>
          <div className="panel-head"><h3 className="panel-title">Root cause detection</h3></div>
          <div className="rc-head">
            <ConfidenceRing value={rc.confidence} />
            <div>
              <div className="rc-service">{rc.label}</div>
              <div className="rc-cause">{rc.cause_label.charAt(0).toUpperCase() + rc.cause_label.slice(1)}</div>
              <div className="muted small">Confidence {rc.confidence}% · {rc.service}</div>
            </div>
          </div>
          <ul className="evidence">{rc.evidence.map((e, i) => <li key={i}>{e}</li>)}</ul>
          <h4 className="sub">Candidate ranking <span className="muted small">· why each service was ruled in or out</span></h4>
          <div className="table-scroll">
            <table className="cand">
              <thead>
                <tr>
                  <th scope="col">Service</th>
                  {FACTORS.map(([f, info]) => <th key={f} scope="col"><abbr title={info}>{f}</abbr></th>)}
                  <th scope="col" className="num">Score</th>
                </tr>
              </thead>
              <tbody>
                {incident.candidates.map((c, i) => (
                  <tr key={c.service} className={i === 0 ? 'lead' : ''}>
                    <td className="cand-name"><div>{c.label}</div><div className="verdict">{c.verdict}</div></td>
                    {FACTORS.map(([f]) => <td key={f}><Bar value={c.factors[f]} /></td>)}
                    <td className="num">{c.score.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="panel">
          <div className="panel-head"><h3 className="panel-title">Impact analysis</h3></div>
          <div className="tiles">
            <div className="tile"><div className="tile-v">{fmtNum(m.users_affected)}</div><div className="tile-l">Users affected</div></div>
            <div className="tile"><div className="tile-v">{fmtNum(m.failed_logins)}</div><div className="tile-l">Failed logins</div></div>
            <div className="tile"><div className="tile-v">{fmtNum(m.failed_transactions)}</div><div className="tile-l">Failed transactions</div></div>
            <div className="tile"><div className="tile-v">{fmtMoney(m.revenue_at_risk)}</div><div className="tile-l">Revenue at risk</div></div>
          </div>
          <h4 className="sub">Affected services and applications</h4>
          <ul className="svc-list">
            {incident.impact.affected_services.map((s) => (
              <li key={s.service}>
                <span className={`pip ${s.worst_severity}`} aria-label={s.worst_severity} />
                <span className="svc-name">{s.label}</span>
                <span className="muted small svc-cap">{s.capability}</span>
                <span className="muted small">{s.events} {s.events === 1 ? 'event' : 'events'}</span>
              </li>
            ))}
          </ul>
          {incident.impact.at_risk_services.length > 0 && (
            <>
              <h4 className="sub">At risk <span className="muted small">· depend on the root cause, not alerting yet</span></h4>
              <div className="chips">{incident.impact.at_risk_services.map((s) => <span key={s.service} className="chip warn">{s.label}</span>)}</div>
            </>
          )}
        </section>

        <section className="panel">
          <div className="panel-head">
            <h3 className="panel-title">Incident timeline</h3>
            <label className="toggle"><input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} /> Show all {incident.timeline.events.length} events</label>
          </div>
          <div className="propagation" aria-label="Failure propagation">
            {incident.timeline.propagation.map((p, i) => (
              <React.Fragment key={p.service}>
                {i > 0 && <span className="prop-arrow" aria-hidden="true">→</span>}
                <span className={`prop-step ${p.is_root ? 'root' : ''}`}><span className="muted">T+{Math.round(p.offset_seconds)}s</span>{p.label}</span>
              </React.Fragment>
            ))}
          </div>
          <ol className="timeline">
            {events.map((e) => (
              <li key={e.id} className={`${e.severity} ${e.is_root ? 'is-root' : ''} ${replayT !== null && e.offset_seconds > replayT ? 'future' : ''}`}>
                <span className="t">T+{Math.round(e.offset_seconds)}s</span>
                <span className="dot" aria-hidden="true" />
                <div>
                  <div className="tl-head"><b>{e.service}</b><span className={`badge ${e.severity}`}>{e.severity}</span><span className="muted small">{e.type_label}</span></div>
                  <div className="tl-msg">{e.message}</div>
                </div>
              </li>
            ))}
          </ol>
        </section>

        <section className="panel">
          <div className="panel-head"><h3 className="panel-title">Smart recommendations</h3></div>
          <ol className="recs">
            {incident.recommendations.map((r, i) => (
              <li key={i}>
                <span className={`prio ${r.priority}`}>{PRIORITY[r.priority]}</span>
                <div>
                  <div className="rec-title">{r.title}</div>
                  <div className="rec-detail">{r.detail}</div>
                  {r.command && <code className="cmd">{r.command}</code>}
                  <div className="rec-target">Target: {r.target}</div>
                </div>
              </li>
            ))}
          </ol>
        </section>
      </div>

      {report && (
        <div className="modal" onClick={() => setReport(null)}>
          <div className="modal-body panel" role="dialog" aria-modal="true" aria-labelledby="report-title" onClick={(e) => e.stopPropagation()}>
            <div className="panel-head">
              <h3 className="panel-title" id="report-title">Incident report · {incident.id}</h3>
              <Button variant="quiet" size="sm" onClick={() => setReport(null)} autoFocus>Close</Button>
            </div>
            <pre className="report">{report}</pre>
          </div>
        </div>
      )}
    </div>
  )
}
