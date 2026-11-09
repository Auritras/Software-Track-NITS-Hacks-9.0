import React, { useEffect, useState } from 'react'
import { api } from './api.js'
import { Button, CountUp, Skeleton, useBusy } from './ui.jsx'

/** "Break anything": inject a failure into any service and watch J.A.R.V.I.S. diagnose it. */
export function BreakPanel({ topology, service, onServiceChange, streaming, onBreak }) {
  const [catalog, setCatalog] = useState(null)
  const [fault, setFault] = useState('')
  const [busy, run] = useBusy()
  const target = service || 'redis-cache'

  useEffect(() => { api.faults().then(setCatalog).catch(() => {}) }, [topology?.nodes?.length])
  const options = catalog?.[target] || []
  useEffect(() => { setFault(options[0]?.id || '') }, [target, catalog])

  const label = topology?.nodes.find((n) => n.id === target)?.label || target
  const inject = () => run('inject', () => onBreak(target, fault))

  return (
    <section className="panel break-panel" aria-labelledby="break-title">
      <div className="panel-head">
        <h2 className="panel-title" id="break-title">Break anything</h2>
      </div>
      <p className="panel-copy">Choose any service, or click one on the map, and a failure to inject. Nothing is scripted: the failure spreads through the real dependency graph and J.A.R.V.I.S. has to find it.</p>
      <div className="break-form">
        <label className="field">
          <span className="field-label">Service</span>
          <select value={target} onChange={(e) => onServiceChange(e.target.value)}>
            {(topology?.nodes || []).map((n) => <option key={n.id} value={n.id}>{n.label}</option>)}
          </select>
        </label>
        <label className="field">
          <span className="field-label">Failure</span>
          <select value={fault} onChange={(e) => setFault(e.target.value)} disabled={!options.length}>
            {options.map((f) => <option key={f.id} value={f.id}>{f.label}</option>)}
          </select>
        </label>
        <Button variant="primary" onClick={inject} loading={busy === 'inject' || streaming} disabled={!fault}>
          {streaming ? 'Simulation running…' : `Break ${label}`}
        </Button>
      </div>
    </section>
  )
}

const pct = (v) => `${Number(v).toFixed(1)}%`

/** Flickers like a slot machine while the benchmark re-runs. */
function SlotNumber() {
  const [v, setV] = useState(90)
  useEffect(() => {
    const t = setInterval(() => setV(80 + Math.random() * 19.9), 70)
    return () => clearInterval(t)
  }, [])
  return <span className="countup slot">{v.toFixed(1)}%</span>
}

/** Measured root-cause accuracy over random injected faults. */
export function AccuracyPanel() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [busy, run] = useBusy()

  useEffect(() => { api.benchmark().then(setData).catch((e) => setError(e.message)) }, [])
  const rerun = () => run('rerun', async () => {
    try { setData(await api.rerunBenchmark()); setError(null) } catch (e) { setError(e.message) }
  })

  return (
    <section className="panel" aria-labelledby="accuracy-title">
      <div className="panel-head">
        <h2 className="panel-title" id="accuracy-title">Measured accuracy</h2>
        <Button size="sm" onClick={rerun} loading={busy === 'rerun'}>Re-run with new random faults</Button>
      </div>
      {error && <div className="empty">Benchmark unavailable: {error}</div>}
      {!data && !error && <div className="acc-grid"><Skeleton kind="block" /><Skeleton kind="block" /><Skeleton kind="block" /></div>}
      {data && (
        <div className="acc-layout fade-in">
          <div className="acc-headline">
            <div className="acc-big">
              <div className="acc-v hero">{busy === 'rerun' ? <SlotNumber /> : <CountUp value={data.top1_accuracy} decimals={1} suffix="%" />}</div>
              <div className="acc-l">Root cause correct (top 1)</div>
            </div>
            <div className="acc-pair">
              <div className="acc-big">
                <div className="acc-v secondary"><CountUp value={data.top3_accuracy} decimals={1} suffix="%" /></div>
                <div className="acc-l">Root cause in top 3</div>
              </div>
              {data.throughput?.events_per_second && (
                <div className="acc-big" title={`${data.throughput.events.toLocaleString('en-IN')} events analysed end to end in ${data.throughput.analysis_ms} ms on this server`}>
                  <div className="acc-v secondary"><CountUp value={data.throughput.events_per_second} /></div>
                  <div className="acc-l">Events analysed per second</div>
                </div>
              )}
            </div>
            {data.throughput?.events_per_second && (
              <p className="panel-copy">
                Speed: {data.throughput.events.toLocaleString('en-IN')} events from {data.throughput.outages} back-to-back outages were correlated, ranked and
                explained in {data.throughput.analysis_ms} ms, measured live on this server.
              </p>
            )}
            <p className="panel-copy">
              {data.trials} random failures across {data.root_candidates} services, completed in {data.duration_ms} ms.
              Confidence averages {data.avg_confidence_correct}% when correct and {data.avg_confidence_wrong}% when wrong,
              so a low score is a real signal to double-check.
            </p>
          </div>
          <div>
            <h3 className="sub">By test condition</h3>
            <table className="acc-table">
              <thead><tr><th scope="col">Condition</th><th scope="col">Trials</th><th scope="col">Top 1</th><th scope="col" className="num">Top 3</th></tr></thead>
              <tbody>
                {data.by_condition.map((c) => (
                  <tr key={c.condition}>
                    <td>{c.label}</td>
                    <td className="muted">{c.trials}</td>
                    <td><span className="acc-bar"><span style={{ width: `${c.top1}%` }} /></span>{pct(c.top1)}</td>
                    <td className="num muted">{pct(c.top3)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div>
            <h3 className="sub">How it is measured</h3>
            <ul className="acc-notes">
              <li>Each trial breaks a random service that has dependents, using a random failure type.</li>
              <li>Harder conditions add unrelated noise, hide the cause signal, limit propagation to about 60% of dependents, or skew one host's clock by 3 s.</li>
              <li>Unrelated noise kept out of the incident: {data.noise_separation ?? 0}%. Noise on connected services is often grouped in, but it rarely changes the diagnosis.</li>
              <li>Synthetic failures on this platform's topology; real-world accuracy depends on log quality.</li>
            </ul>
          </div>
        </div>
      )}
    </section>
  )
}
