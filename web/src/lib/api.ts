const BASE = '/api'

export class ApiError extends Error {
  constructor(public status: number, public body: { error?: string; message?: string } | null, msg: string) {
    super(msg)
  }
}

/** The API's own message when it sends one; a calm sentence otherwise. */
export function errorText(status: number, body: unknown): string {
  const b = body as { message?: string; detail?: unknown } | null
  if (b?.message) return b.message
  if (Array.isArray(b?.detail)) return String((b!.detail as { msg?: string }[])[0]?.msg ?? 'That request was not valid.')
  if (status === 0 || (status >= 500 && !b)) return 'The API is not reachable on 127.0.0.1:8000. Start it with `make api`.'
  return `The API answered HTTP ${status}.`
}

export async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(BASE + path, init)
  } catch {
    throw new ApiError(0, null, errorText(0, null))
  }
  const text = await res.text()
  let body: unknown = null
  try { body = text ? JSON.parse(text) : null } catch { /* proxy error pages are not JSON */ }
  if (!res.ok) throw new ApiError(res.status, body as never, errorText(res.status, body))
  return body as T
}

import type { DemoStatus, Report, Source, TamperResult } from './types'

export const api = {
  report: (source: Source) => call<Report>(`/report?source=${source}`),
  tamper: (source: Source) => call<TamperResult>(`/tamper?source=${source}`, { method: 'POST' }),
  demoRun: (delayMs = 130) => call<DemoStatus>(`/demo/run?delay_ms=${delayMs}`, { method: 'POST' }),
  demoStatus: () => call<DemoStatus>('/demo/status'),
  streamUrl: (source: Source, tail = 400) => `${BASE}/events/stream?source=${source}&tail=${tail}`,
}

export const json = (method: string, body: unknown): RequestInit => ({ method, headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) })
export const API_BASE = BASE
