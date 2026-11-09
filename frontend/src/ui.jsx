import React, { useCallback, useState } from 'react'

export function Button({ loading = false, variant = '', size = '', children, disabled, ...props }) {
  return (
    <button
      type="button"
      className={`btn ${variant} ${size}`.trim()}
      disabled={disabled || loading}
      aria-busy={loading}
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
    <svg className={className} viewBox="0 0 32 32" aria-hidden="true">
      <rect width="32" height="32" rx="8" fill="#161e2b" />
      <circle cx="16" cy="16" r="9" fill="none" stroke="#4c8dff" strokeWidth="2.5" />
      <circle cx="16" cy="16" r="3.5" fill="#4c8dff" />
    </svg>
  )
}
