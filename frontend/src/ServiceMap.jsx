import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'

const W = 1000
const NODE_W = 144
const LABEL_X = 28
const LABEL_MAX_W = NODE_W - LABEL_X - 10 // room for the label inside the box
const LABEL_SIZE = 13
const NODE_H = 48
const TIER_GAP = 88
const TOP = 32

const KIND_LABEL = {
  frontend: 'Client app', gateway: 'Gateway', service: 'Service', database: 'Database',
  cache: 'Cache', external: 'External', queue: 'Queue', unknown: 'Unmapped',
}

/** SVG label that measures its real rendered width and shrinks (down to 10px), then truncates, to fit. */
function FitLabel({ text, x, y, max }) {
  const ref = useRef()
  // `measure: true` renders the full text at full size so its real width can be read.
  const [fit, setFit] = useState({ text, size: LABEL_SIZE, measure: true })
  const [fontsReady, setFontsReady] = useState(0)
  useLayoutEffect(() => {
    let alive = true
    document.fonts?.ready.then(() => alive && setFontsReady((n) => n + 1))
    return () => { alive = false }
  }, [])
  useLayoutEffect(() => { setFit({ text, size: LABEL_SIZE, measure: true }) }, [text, max, fontsReady])
  useLayoutEffect(() => {
    if (!fit.measure) return
    const el = ref.current
    const width = el?.getComputedTextLength ? el.getComputedTextLength() : 0
    if (!width || width <= max) { setFit({ text, size: LABEL_SIZE, measure: false }); return }
    const size = Math.max(10, (LABEL_SIZE * max) / width)
    const scaled = (width * size) / LABEL_SIZE
    const shown = scaled <= max ? text : `${text.slice(0, Math.max(3, Math.floor((text.length * max) / scaled) - 1))}…`
    setFit({ text: shown, size, measure: false })
  }, [fit.measure, text, max])
  return <text ref={ref} className="node-label" x={x} y={y} style={{ fontSize: fit.size }}>{fit.text}</text>
}

function layout(nodes) {
  const tiers = {}
  nodes.forEach((n) => { (tiers[n.tier] ??= []).push(n) })
  const pos = {}
  Object.entries(tiers).forEach(([tier, list]) => {
    list.forEach((n, i) => {
      pos[n.id] = { x: ((i + 1) * W) / (list.length + 1), y: TOP + NODE_H / 2 + Number(tier) * TIER_GAP }
    })
  })
  const maxTier = Math.max(0, ...nodes.map((n) => n.tier))
  return { pos, height: TOP + NODE_H + maxTier * TIER_GAP + 16 }
}

function curve(a, b) {
  const midY = (a.y + b.y) / 2
  return `M${a.x},${a.y} C${a.x},${midY} ${b.x},${midY} ${b.x},${b.y}`
}

export default function ServiceMap({ topology, incident, onSelectService, selectedService, replayT = null }) {
  const { pos, height } = useMemo(() => layout(topology?.nodes || []), [topology])

  const propagation = new Set((incident?.timeline?.propagation_edges || []).map((e) => `${e.from}>${e.to}`))
  const incidentServices = new Set((incident?.impact?.affected_services || []).map((s) => s.service))
  const rootService = incident?.root_cause?.service
  // Replay: only services that had started failing by time replayT are shown as failing.
  const replaying = replayT !== null && incident
  const startedAt = Object.fromEntries((incident?.timeline?.propagation || []).map((p) => [p.service, p.offset_seconds]))
  const hasStarted = (svc) => startedAt[svc] !== undefined && startedAt[svc] <= replayT
  // The ROOT CAUSE verdict is only revealed once there is evidence: 3+ correlated services.
  const EVIDENCE = 3
  const failingCount = replaying
    ? Object.values(startedAt).filter((t) => t <= replayT).length
    : (incident?.impact?.affected_services?.length || 0)
  const revealed = Boolean(incident) && failingCount >= EVIDENCE
  const [spotlight, setSpotlight] = useState(false)
  const wasRevealed = useRef(false)
  const lastIncident = useRef(null)
  useEffect(() => {
    if (incident?.id !== lastIncident.current) { lastIncident.current = incident?.id; wasRevealed.current = revealed; return }
    if (revealed && !wasRevealed.current) {
      setSpotlight(true)
      const t = setTimeout(() => setSpotlight(false), 1600)
      wasRevealed.current = true
      return () => clearTimeout(t)
    }
    wasRevealed.current = revealed
  }, [revealed, incident?.id])

  return (
    <div className="map-scroll">
      <svg className={`service-map ${spotlight ? 'spotlight' : ''}`} viewBox={`0 0 ${W} ${height}`} role="img" aria-label="Service dependency map">
        <defs>
          <pattern id="halftone" width="6" height="6" patternUnits="userSpaceOnUse">
            <rect width="6" height="6" fill="var(--pink-soft)" />
            <circle cx="3" cy="3" r="1.6" fill="var(--pink)" />
          </pattern>
          <marker id="arrow-hot" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M0,0 L10,5 L0,10 z" fill="var(--danger)" />
          </marker>
        </defs>

        {topology.edges.map((e) => {
          const a = pos[e.source]
          const b = pos[e.target]
          if (!a || !b) return null
          const from = { x: a.x, y: a.y + NODE_H / 2 }
          const to = { x: b.x, y: b.y - NODE_H / 2 }
          const hot = propagation.has(`${e.target}>${e.source}`) && (!replaying || hasStarted(e.source))
          // Hot edges are drawn callee -> caller so the arrow shows the direction the failure travelled.
          if (!hot) return <path key={`${e.source}-${e.target}`} className="edge" d={curve(from, to)} />
          const d = curve(to, from)
          return (
            <g key={`${e.source}-${e.target}`}>
              <path className="edge hot" d={d} markerEnd="url(#arrow-hot)" />
              {/* particles travel from the failing callee to its caller: the direction the failure spread */}
              {[0, 0.6, 1.2].map((delay) => (
                <circle key={delay} className="particle" r="3.5">
                  <animateMotion dur="1.8s" begin={`${delay}s`} repeatCount="indefinite" path={d} />
                </circle>
              ))}
            </g>
          )
        })}

        {topology.nodes.map((n) => {
          const p = pos[n.id]
          const status = replaying
            ? (hasStarted(n.id) ? 'failing' : 'healthy')
            : (n.health?.status || 'healthy')
          const isRoot = (incident ? rootService === n.id && revealed : Boolean(n.health?.root_cause)) && (!replaying || hasStarted(n.id))
          const dim = incident && !incidentServices.has(n.id) && !isRoot
          const select = () => onSelectService?.(selectedService === n.id ? null : n.id)
          return (
            <g
              key={n.id}
              className={`node ${status} ${isRoot ? 'root' : ''} ${dim ? 'dim' : ''} ${selectedService === n.id ? 'selected' : ''}`}
              transform={`translate(${p.x - NODE_W / 2}, ${p.y - NODE_H / 2})`}
              onClick={select}
              onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); select() } }}
              tabIndex={0}
              role="button"
              aria-pressed={selectedService === n.id}
              aria-label={`${n.label}, ${status}${isRoot ? ', root cause' : ''}. Filter event stream`}
            >
              {/* keyed by status: remounts (and replays the jolt) the moment a service starts failing */}
              <g className="jolt" key={status}>
                <rect className="node-shadow" x={3} y={3} width={NODE_W} height={NODE_H} rx={10} />
                <rect className="node-box" width={NODE_W} height={NODE_H} rx={10} />
                <circle className="node-dot" cx={16} cy={NODE_H / 2} r={4} />
                <FitLabel text={n.label} x={LABEL_X} y={21} max={LABEL_MAX_W} />
                <text className="node-kind" x={LABEL_X} y={37}>{KIND_LABEL[n.kind] || n.kind}</text>
              </g>
              {isRoot && (
                <g transform={`translate(${NODE_W / 2 - 40}, -12)`} className="root-stamp">
                  <rect className="root-tag" width={80} height={18} rx={9} />
                  <text className="root-tag-text" x={40} y={13.5} textAnchor="middle">ROOT CAUSE</text>
                </g>
              )}
              <title>{`${n.label} (${n.id}): ${status}${n.capability ? `. ${n.capability}` : ''}`}</title>
            </g>
          )
        })}
      </svg>
    </div>
  )
}
