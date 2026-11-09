const BASE = import.meta.env.VITE_API_URL || ''

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, options)
  if (!res.ok) {
    let detail = res.statusText
    try { detail = (await res.json()).detail || detail } catch { /* not json */ }
    throw new Error(detail)
  }
  const type = res.headers.get('content-type') || ''
  return type.includes('application/json') ? res.json() : res.text()
}

const post = (path, body) => request(path, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body ?? {}),
})

export const api = {
  stats: () => request('/api/stats'),
  incidents: () => request('/api/incidents'),
  incident: (id) => request(`/api/incidents/${id}`),
  report: (id) => request(`/api/incidents/${id}/report`),
  reportUrl: (id) => `${BASE}/api/incidents/${id}/report`,
  topology: () => request('/api/topology'),
  events: (limit = 80, anomaliesOnly = false) => request(`/api/events?limit=${limit}&anomalies_only=${anomaliesOnly}`),
  scenarios: () => request('/api/scenarios'),
  simulate: (scenario, mode, speed) => post('/api/simulate', { scenario, mode, speed }),
  stop: () => post('/api/simulate/stop'),
  reset: () => post('/api/reset'),
  undoReset: () => post('/api/reset/undo'),
  sendEvent: (event) => post('/api/events', event),
  faults: () => request('/api/faults'),
  injectFault: (service, fault, mode, speed) => post('/api/faults/inject', { service, fault, mode, speed }),
  benchmark: () => request('/api/benchmark'),
  rerunBenchmark: () => post('/api/benchmark/run'),
  upload: (file) => {
    const form = new FormData()
    form.append('file', file)
    return request('/api/logs/upload', { method: 'POST', body: form })
  },
}

export const fmtTime = (iso) => new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
export const fmtNum = (n) => Number(n || 0).toLocaleString('en-IN')
export const fmtMoney = (n) => `₹${Number(n || 0).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`
