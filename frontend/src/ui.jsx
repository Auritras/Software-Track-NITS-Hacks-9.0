import React, { useCallback, useEffect, useRef, useState } from 'react'

const reducedMotion = () => typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches

/** Number that counts from its previous value to the new one (ease-out, ~700 ms). */
export function CountUp({ value, decimals = 0, suffix = '', duration = 700 }) {
  const target = Number(value) || 0
  const [shown, setShown] = useState(target)
  const from = useRef(0)
  const frame = useRef()
  useEffect(() => {
    if (reducedMotion()) { setShown(target); from.current = target; return }
    const start = performance.now()
    const begin = from.current
    const tick = (now) => {
      const p = Math.min(1, (now - start) / duration)
      const eased = 1 - (1 - p) ** 3
      setShown(begin + (target - begin) * eased)
      if (p < 1) frame.current = requestAnimationFrame(tick)
      else from.current = target
    }
    frame.current = requestAnimationFrame(tick)
    // Background tabs pause animation frames: make sure the final value always lands.
    const settle = setTimeout(() => { setShown(target); from.current = target }, duration + 80)
    return () => { cancelAnimationFrame(frame.current); clearTimeout(settle); from.current = target }
  }, [target, duration])
  const text = shown.toLocaleString('en-IN', { minimumFractionDigits: decimals, maximumFractionDigits: decimals })
  return <span className="countup">{text}{suffix}</span>
}

export function Button({ loading = false, variant = '', size = '', children, disabled, ...props }) {
  return (
    <button
      type="button"
      className={`btn ${variant} ${size}`.trim()}
      disabled={disabled || loading}
      aria-busy={loading}
      aria-label={typeof children === 'string' ? children : undefined}
      {...props}
    >
      {loading && <span className="spinner" aria-hidden="true" />}
      <span>{children}</span>
    </button>
  )
}

/** Tracks which async action is in flight so its button can show a loading state. */
export function useBusy() {
  const [busy, setBusy] = useState(null)
  const run = useCallback(async (key, fn) => {
    setBusy(key)
    try { return await fn() } finally { setBusy(null) }
  }, [])
  return [busy, run]
}

export function Skeleton({ kind = 'line', width }) {
  return <span className={`skel ${kind}`} style={width ? { width } : undefined} aria-hidden="true" />
}

export function BrandMark({ className = 'brand-mark' }) {
  return (
    <svg className={className} viewBox="0 0 44 44" aria-hidden="true">
      <rect x="4" y="4" width="38" height="38" rx="10" fill="#111" />
      <rect x="1" y="1" width="38" height="38" rx="10" fill="#fbde86" stroke="#111" strokeWidth="2" />
      <circle cx="20" cy="20" r="11" fill="#86d2fb" stroke="#111" strokeWidth="2" />
      <circle cx="20" cy="20" r="4.5" fill="#ff82b7" stroke="#111" strokeWidth="2" />
    </svg>
  )
}
