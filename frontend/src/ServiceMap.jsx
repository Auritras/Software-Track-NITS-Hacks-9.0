import React, { useMemo } from 'react'

const W = 1000
const NODE_W = 132
const NODE_H = 46
const TIER_GAP = 86
const TOP = 34

const KIND_ICON = {
  frontend: '◧', gateway: '⇄', service: '◆', database: '⛁', cache: '⚡', external: '⇲', queue: '≣', unknown: '?',
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
  return { pos, height: TOP + NODE_H + maxTier * TIER_GAP + 30 }
}

function curve(a, b) {
  const midY = (a.y + b.y) / 2
  return `M${a.x},${a.y} C${a.x},${midY} ${b.x},${midY} ${b.x},${b.y}`
}

export default function ServiceMap({ topology, incident, onSelectService, selectedService }) {
  const { pos, height } = useMemo(() => layout(topology?.nodes || []), [topology])
  if (!topology) return <div className="empty">Loading topology…</div>

  const propagation = new Set((incident?.timeline?.propagation_edges || []).map((e) => `${e.from}>${e.to}`))
  const incidentServices = new Set((incident?.impact?.affected_services || []).map((s) => s.service))
  const rootService = incident?.root_cause?.service

  return (
    <svg className="service-map" viewBox={`0 0 ${W} ${height}`} role="img" aria-label="Service dependency map">
      <defs>
        <filter id="glow" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="6" result="blur" />
          <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
        </filter>
        <marker id="arrow-hot" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
          <path d="M0,0 L10,5 L0,10 z" fill="var(--red)" />
        </marker>
      </defs>

      {topology.edges.map((e) => {
        const a = pos[e.source]
        const b = pos[e.target]
        if (!a || !b) return null
        const from = { x: a.x, y: a.y + NODE_H / 2 }
        const to = { x: b.x, y: b.y - NODE_H / 2 }
        const hot = propagation.has(`${e.target}>${e.source}`)
        return hot ? (
          <path key={`${e.source}-${e.target}`} className="edge hot" d={curve(to, from)} markerEnd="url(#arrow-hot)" />
        ) : (
          <path key={`${e.source}-${e.target}`} className="edge" d={curve(from, to)} />
        )
      })}

      {topology.nodes.map((n) => {
        const p = pos[n.id]
        const status = n.health?.status || 'healthy'
        const inIncident = incidentServices.has(n.id)
        const isRoot = rootService ? rootService === n.id : n.health?.root_cause
        const dim = incident && !inIncident && !isRoot
        return (
          <g
            key={n.id}
            className={`node ${status} ${isRoot ? 'root' : ''} ${dim ? 'dim' : ''} ${selectedService === n.id ? 'selected' : ''}`}
            transform={`translate(${p.x - NODE_W / 2}, ${p.y - NODE_H / 2})`}
            onClick={() => onSelectService?.(selectedService === n.id ? null : n.id)}
          >
            {isRoot && <rect className="root-halo" x={-6} y={-6} width={NODE_W + 12} height={NODE_H + 12} rx={14} filter="url(#glow)" />}
            <rect className="node-box" width={NODE_W} height={NODE_H} rx={10} />
            <circle className="node-dot" cx={14} cy={NODE_H / 2} r={4.5} />
            <text className="node-label" x={26} y={20}>{n.label}</text>
            <text className="node-kind" x={26} y={35}>{KIND_ICON[n.kind] || '•'} {n.kind}</text>
            {isRoot && (
              <g transform={`translate(${NODE_W / 2 - 40}, -17)`}>
                <rect className="root-tag" width={80} height={16} rx={8} />
                <text className="root-tag-text" x={40} y={11.5} textAnchor="middle">ROOT CAUSE</text>
              </g>
            )}
            <title>{`${n.label} (${n.id}) - ${status}${n.capability ? `\n${n.capability}` : ''}`}</title>
          </g>
        )
      })}
    </svg>
  )
}
