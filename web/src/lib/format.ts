/** Money is integer minor units (cents); same format as core/money.py. */
export function money(minor: number, sign = false): string {
  const neg = minor < 0
  const n = Math.abs(minor)
  const body = `$${Math.floor(n / 100).toLocaleString('en-US')}.${String(n % 100).padStart(2, '0')}`
  return neg ? `-${body}` : sign && minor > 0 ? `+${body}` : body
}

export const shortHash = (h: string, n = 7) => h.slice(0, n)

export function iso(ns: number): string {
  const ms = Math.floor(ns / 1e6)
  return new Date(ms).toISOString().replace('T', ' ')
}

export const int = (n: number) => n.toLocaleString('en-US')

export function signed(n: number): string {
  return n > 0 ? `+${int(n)}` : int(n)
}
