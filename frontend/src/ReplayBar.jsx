import React, { useEffect, useRef, useState } from 'react'
import { Button } from './ui.jsx'

const PLAY_SECONDS = 8 // a full replay always takes about this long, whatever the incident length

/** Scrub or play back how an incident spread across services. value === null means "live view". */
export default function ReplayBar({ incident, value, onChange }) {
  const steps = incident.timeline.propagation
  const end = Math.max(1, ...steps.map((p) => p.offset_seconds)) + 1
  const [playing, setPlaying] = useState(false)
  const frame = useRef()

  useEffect(() => () => { onChange(null); cancelAnimationFrame(frame.current) }, []) // leave replay when the incident changes

  useEffect(() => {
    if (!playing) return
    const startT = value === null || value >= end ? 0 : value
    const started = performance.now() - (startT / end) * PLAY_SECONDS * 1000
    const tick = (now) => {
      const t = Math.min(end, ((now - started) / (PLAY_SECONDS * 1000)) * end)
      onChange(t)
      if (t < end) frame.current = requestAnimationFrame(tick)
      else setPlaying(false)
    }
    frame.current = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame.current)
  }, [playing])

  const t = value ?? end
  const failing = steps.filter((p) => p.offset_seconds <= t)
  const latest = failing[failing.length - 1]
  const scrub = (e) => { setPlaying(false); onChange(Number(e.target.value)) }

  return (
    <div className="replay" role="group" aria-label="Incident replay">
      <Button size="sm" variant="primary" onClick={() => setPlaying((p) => !p)} aria-pressed={playing}>
        {playing ? 'Pause' : value !== null && value < end ? 'Resume' : 'Replay spread'}
      </Button>
      <input
        type="range" min="0" max={end} step="0.1" value={t} onChange={scrub}
        aria-label="Incident time" aria-valuetext={`T plus ${Math.round(t)} seconds`}
        style={{ '--fill': `${(t / end) * 100}%` }}
      />
      <span className="replay-status">
        <b>T+{Math.round(t)}s</b> · {failing.length} of {steps.length} services failing
        {latest && value !== null ? <span className="muted"> · latest: {latest.label}</span> : null}
      </span>
      {value !== null && <Button size="sm" variant="quiet" onClick={() => { setPlaying(false); onChange(null) }}>Live view</Button>}
    </div>
  )
}
