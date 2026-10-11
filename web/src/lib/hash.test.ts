import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { recomputeHash } from './hash'
import type { LogEvent } from './types'

const lines = readFileSync(new URL('../../../fixtures/recon/gate_log.jsonl', import.meta.url), 'utf8').trim().split('\n')

describe('recomputeHash', () => {
  it('reproduces the hash the gate wrote for every event of a real log', async () => {
    for (const l of lines) {
      const ev = JSON.parse(l) as LogEvent
      expect(await recomputeHash(ev)).toBe(ev.hash)
    }
  })
  it('does not match once a field is edited', async () => {
    const ev = JSON.parse(lines[5]) as LogEvent
    expect(await recomputeHash({ ...ev, qty: ev.qty + 1 })).not.toBe(ev.hash)
  })
})
