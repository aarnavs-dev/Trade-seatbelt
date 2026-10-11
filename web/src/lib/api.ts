import type { Bench, CfResult, DemoStatus, Invariants, ReconResult, Report, Source, TamperResult } from './types'

const BASE = '/api'

export class ApiError extends Error {
  constructor(public status: number, public body: { error?: string; message?: string; row?: number } | null, msg: string) {
    super(msg)
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(BASE + path, init)
  } catch {
    throw new ApiError(0, null, 'API not reachable on 127.0.0.1:8000. Start it with `make api`.')
  }
  const text = await res.text()
  let body: unknown = null
  try {
    body = text ? JSON.parse(text) : null
  } catch {
    /* proxy error pages are not JSON */
  }
  if (!res.ok) {
    const b = body as { error?: string; message?: string } | null
    const proxyDown = res.status >= 500 && !b
    throw new ApiError(res.status, b, proxyDown ? 'API not reachable on 127.0.0.1:8000. Start it with `make api`.' : (b?.message ?? `HTTP ${res.status}`))
  }
  return body as T
}

export const api = {
  report: (source: Source) => call<Report>(`/report?source=${source}`),
  tamper: (source: Source) => call<TamperResult>(`/tamper?source=${source}`, { method: 'POST' }),
  demoRun: (delayMs = 130) => call<DemoStatus>(`/demo/run?delay_ms=${delayMs}`, { method: 'POST' }),
  demoStatus: () => call<DemoStatus>('/demo/status'),
  recon: () => call<ReconResult>('/recon'),
  reconUpload: (csv: Blob) => call<ReconResult>('/recon', { method: 'POST', body: csv, headers: { 'content-type': 'text/csv' } }),
  counterfactual: () => call<CfResult>('/counterfactual'),
  counterfactualUpload: (csv: Blob) => call<CfResult>('/counterfactual', { method: 'POST', body: csv, headers: { 'content-type': 'text/csv' } }),
  bench: () => call<Bench>('/bench'),
  invariants: () => call<Invariants>('/invariants'),
  streamUrl: (source: Source, tail = 400) => `${BASE}/events/stream?source=${source}&tail=${tail}`,
}

export const MAX_UPLOAD = 1_000_000
