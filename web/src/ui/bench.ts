import type { BenchRun } from '../lib/types'

export const fsyncLabel = (f: string) => (f === 'none' ? 'fsync none (NON-DURABLE)' : `fsync ${f}`)

/** The sync call, only if the result file recorded it (older files predate the field; we do not guess). */
export function fsyncCall(run: BenchRun, policy: string): string | null {
  if (policy === 'none') return 'no sync call'
  const c = run.environment.fsync_call
  return c ? String(c) : null
}

export const policyText = (run: BenchRun, policy: string) => {
  const c = fsyncCall(run, policy)
  return c ? `${fsyncLabel(policy)} (${c})` : fsyncLabel(policy)
}
