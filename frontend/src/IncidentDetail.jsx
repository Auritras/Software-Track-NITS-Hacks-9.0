import React, { useState } from 'react'
import { api, fmtMoney, fmtNum, fmtTime } from './api.js'

const FACTOR_INFO = {
  coverage: 'Share of affected services that depend on this one',
  precedence: 'How early this service started failing',
  depth: '1 if none of its own dependencies are failing',
  causal: 'Emitted a cause-level signal (pool exhausted, OOM, deploy…)',
}
const PRIORITY = { immediate: 'Immediate', short_term: 'Short term', long_term: 'Long term' }

function ConfidenceRing({ value }) {
  const r = 34
  const c = 2 * Math.PI * r
  return (
    <svg className="ring" viewBox="0 0 84 84" aria-label={`Confidence ${value}%`}>
      <circle cx="42" cy="42" r={r} className="ring-bg" />
      <circle cx="42" cy="42" r={r} className="ring-fg" strokeDasharray={`${(c * value) / 100} ${c}`} transform="rotate(-90 42 42)" />
      <text x="42" y="44" textAnchor="middle" className="ring-value">{value}%</text>
      <text x="42" y="58" textAnchor="middle" className="ring-label">confidence</text>
    </svg>
  )
}

function Bar({ value }) {
  return <span className="bar"><span style={{ width: `${Math.round(value * 100)}%` }} /></span>
}

export default function IncidentDetail({ incident }) {
  const [showAll, setShowAll] = useState(false)
  const [report, setReport] = useState(null)
  if (!incident) {
    return (
      <div className="panel detail empty-state">
        <div className="empty-icon">◎</div>
        <h3>No incident selected</h3>
        <p>Run a scenario, upload a log file or push alerts to the API. J.A.R.V.I.S. will correlate them into incidents here.</p>
      </div>
    )
  }
  const rc = incident.root_cause
  const m = incident.impact.metrics
  const events = showAll ? incident.timeline.events : incident.timeline.events.filter((e) => e.milestone || e.severity === 'critical')

  const openReport = async () => setReport(await api.report(incident.id))
  const download = async () => {
    const text = await api.report(incident.id)
    const url = URL.createObjectURL(new Blob([text], { type: 'text/markdown' }))
    const a = Object.assign(document.createElement('a'), { href: url, download: `${incident.id}.md` })
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="detail">
      <div className="panel detail-head">
        <div className="detail-title">
          <div className="row gap">
            <span className={`sev ${incident.severity}`}>{incident.severity}</span>
            <span className={`status ${incident.status}`}>{incident.status}</span>
            <span className="mono muted">{incident.id}</span>
            <span className="mono muted">started {fmtTime(incident.started_at)} · {Math.round(incident.duration_seconds)}s</span>
          </div>
          <h2>{incident.title}</h2>
          <p className="summary">{incident.summary}</p>
        </div>
        <div className="row gap">
          <button className="btn ghost" onClick={openReport}>View report</button>
          <button className="btn" onClick={download}>Download report</button>
        </div>
      </div>

      <div className="detail-grid">
        <section className="panel">
          <h3 className="panel-title">Root cause analysis</h3>
          <div className="rc-head">
            <ConfidenceRing value={rc.confidence} />
            <div>
              <div className="rc-service">{rc.label}</div>
              <div className="rc-cause">{rc.cause_label}</div>
              <div className="mono muted small">{rc.service} · {rc.kind}</div>
            </div>
          </div>
          <ul className="evidence">
            {rc.evidence.map((e, i) => <li key={i}>{e}</li>)}
          </ul>
          <h4 className="sub">Candidate ranking <span className="muted small">(explainable scoring)</span></h4>
          <table className="cand">
            <thead>
              <tr><th>Service</th>{Object.keys(FACTOR_INFO).map((f) => <th key={f} title={FACTOR_INFO[f]}>{f}</th>)}<th>score</th></tr>
            </thead>
            <tbody>
              {incident.candidates.map((c, i) => (
                <tr key={c.service} className={i === 0 ? 'top' : ''}>
                  <td>{c.label}</td>
                  {Object.keys(FACTOR_INFO).map((f) => <td key={f}><Bar value={c.factors[f]} /></td>)}
                  <td className="mono">{c.score.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        <section className="panel">
          <h3 className="panel-title">Impact analysis</h3>
          <div className="tiles">
            <div className="tile"><div className="tile-v">{fmtNum(m.users_affected)}</div><div className="tile-l">users affected</div></div>
            <div className="tile"><div className="tile-v">{fmtNum(m.failed_logins)}</div><div className="tile-l">failed logins</div></div>
            <div className="tile"><div className="tile-v">{fmtNum(m.failed_transactions)}</div><div className="tile-l">failed transactions</div></div>
            <div className="tile"><div className="tile-v">{fmtMoney(m.revenue_at_risk)}</div><div className="tile-l">revenue at risk</div></div>
          </div>
          <h4 className="sub">Affected services</h4>
          <ul className="svc-list">
            {incident.impact.affected_services.map((s) => (
              <li key={s.service}>
                <span className={`pip ${s.worst_severity}`} />
                <span className="svc-name">{s.label}</span>
                <span className="muted small">{s.capability}</span>
                <span className="mono small right">{s.events} ev</span>
              </li>
            ))}
          </ul>
          {incident.impact.at_risk_services.length > 0 && (
            <>
              <h4 className="sub">At risk <span className="muted small">(depend on root cause, not alerting yet)</span></h4>
              <div className="chips">
                {incident.impact.at_risk_services.map((s) => <span key={s.service} className="chip warn">{s.label}</span>)}
              </div>
            </>
          )}
        </section>

        <section className="panel">
          <div className="row between">
            <h3 className="panel-title">Incident timeline</h3>
            <label className="toggle"><input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} /> all {incident.timeline.events.length} events</label>
          </div>
          <div className="propagation">
            {incident.timeline.propagation.map((p, i) => (
              <React.Fragment key={p.service}>
                {i > 0 && <span className="prop-arrow">→</span>}
                <span className={`prop-step ${p.is_root ? 'root' : ''}`}>
                  <span className="mono small">T+{Math.round(p.offset_seconds)}s</span> {p.label}
                </span>
              </React.Fragment>
            ))}
          </div>
          <ol className="timeline">
            {events.map((e) => (
              <li key={e.id} className={`${e.severity} ${e.is_root ? 'is-root' : ''}`}>
                <span className="t mono">T+{Math.round(e.offset_seconds)}s</span>
                <span className="dot" />
                <div>
                  <div className="tl-head"><b>{e.service}</b> <span className={`badge ${e.severity}`}>{e.severity}</span> <span className="muted small">{e.type_label}</span></div>
                  <div className="tl-msg">{e.message}</div>
                </div>
              </li>
            ))}
          </ol>
        </section>

        <section className="panel">
          <h3 className="panel-title">Smart recommendations</h3>
          <ol className="recs">
            {incident.recommendations.map((r, i) => (
              <li key={i}>
                <span className={`prio ${r.priority}`}>{PRIORITY[r.priority]}</span>
                <div>
                  <div className="rec-title">{r.title}</div>
                  <div className="rec-detail">{r.detail}</div>
                  {r.command && <code className="cmd">{r.command}</code>}
                  <div className="muted small">target: {r.target}</div>
                </div>
              </li>
            ))}
          </ol>
        </section>
      </div>

      {report && (
        <div className="modal" onClick={() => setReport(null)}>
          <div className="modal-body panel" onClick={(e) => e.stopPropagation()}>
            <div className="row between">
              <h3 className="panel-title">Incident report · {incident.id}</h3>
              <button className="btn ghost" onClick={() => setReport(null)}>Close</button>
            </div>
            <pre className="report">{report}</pre>
          </div>
        </div>
      )}
    </div>
  )
}
