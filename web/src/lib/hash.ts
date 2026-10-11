import type { LogEvent } from './types'

/** Canonical JSON as core/canon.py writes it: sorted keys, no whitespace (events are flat strings and integers). */
export function canonicalBody(ev: LogEvent): string {
  const { hash: _h, prev_hash: _p, ...body } = ev
  const keys = Object.keys(body).sort() as (keyof typeof body)[]
  return '{' + keys.map((k) => `${JSON.stringify(k)}:${JSON.stringify(body[k])}`).join(',') + '}'
}

export async function sha256Hex(text: string): Promise<string> {
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text))
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('')
}

/** hash = SHA-256(prev_hash || canonical JSON of the rest), recomputed locally. */
export const recomputeHash = (ev: LogEvent) => sha256Hex(ev.prev_hash + canonicalBody(ev))
