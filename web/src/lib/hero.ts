import { ChainStore } from './store'
import { canonicalBody, sha256Hex } from './hash'
import type { LogEvent } from './types'

/** The landing hero's sample signals. They are an illustration, not live data; the blocks carry real SHA-256 hashes so the chain is a real chain. */
const SCRIPT: { side: 'BUY' | 'SELL'; symbol: string; qty: number; price: number; stop?: { code: string; text: string } }[] = [
  { side: 'BUY', symbol: 'AAPL', qty: 20, price: 33631 },
  { side: 'BUY', symbol: 'AAPL', qty: 90, price: 33644, stop: { code: 'MAX_POSITION', text: 'Stopped: this would take you above your limit of 100 shares in AAPL' } },
  { side: 'SELL', symbol: 'AAPL', qty: 20, price: 33712 },
  { side: 'BUY', symbol: 'MSFT', qty: 15, price: 52269 },
  { side: 'BUY', symbol: 'TSLA', qty: 10, price: 41280, stop: { code: 'PRICE_BAND', text: 'Stopped: the price is more than 5% from yesterday’s close' } },
  { side: 'SELL', symbol: 'MSFT', qty: 15, price: 52190 },
  { side: 'BUY', symbol: 'NVDA', qty: 30, price: 12044, stop: { code: 'MAX_DAILY_LOSS', text: 'Stopped: this would bring today’s loss to $62.40, past your limit of $50.00' } },
  { side: 'BUY', symbol: 'SPY', qty: 10, price: 55747 },
  { side: 'SELL', symbol: 'SPY', qty: 10, price: 55690 },
  { side: 'BUY', symbol: 'AAPL', qty: 10, price: 33580, stop: { code: 'LOSS_STREAK_COOLDOWN', text: 'Stopped: 3 losing trades in a row, so new trades are paused for 5 minutes' } },
]
export const GAPS = [3200, 3800, 3500, 3300, 3700]      // a new signal every 3.2 to 3.8 s
export const HERO_TRAVEL_MS = 7500
const GENESIS = '0'.repeat(64)

async function next(seq: number, prev: string, i: number): Promise<LogEvent> {
  const sc = SCRIPT[i % SCRIPT.length]
  const body: LogEvent = {
    seq, ts: Date.now() * 1e6, client_order_id: `sample-${seq}`, symbol: sc.symbol, side: sc.side, qty: sc.qty, price: sc.price,
    decision: sc.stop ? 'REJECT' : 'ACCEPT', reason_code: sc.stop ? sc.stop.code : 'OK', reason_text: sc.stop ? sc.stop.text : 'within all limits', prev_hash: prev, hash: '',
  }
  return { ...body, hash: await sha256Hex(prev + canonicalBody(body)) }
}

/** n sample blocks, as history. */
export async function sampleChain(store: ChainStore, n: number) {
  let prev = GENESIS
  for (let i = 0; i < n; i++) { const ev = await next(i + 1, prev, i + 3); store.push(ev, { animate: false }); prev = ev.hash }
  return { seq: n, prev }
}

/** Play the hero: a few blocks of history, then a new sample signal every 3 to 4 seconds. `onSignal` fires when a stopped one reaches the rules ring. */
export function startHero(store: ChainStore, onStop: (text: string) => void): () => void {
  let dead = false
  const timers: ReturnType<typeof setTimeout>[] = []
  ;(async () => {
    let { seq, prev } = await sampleChain(store, 4)
    let i = 0
    const spawn = async () => {
      if (dead) return
      const ev = await next(++seq, prev, i)
      prev = ev.hash
      store.push(ev, { animate: true })
      if (ev.decision === 'REJECT') timers.push(setTimeout(() => !dead && onStop(ev.reason_text), HERO_TRAVEL_MS * 0.97))
      timers.push(setTimeout(spawn, GAPS[i++ % GAPS.length]))
    }
    await spawn()
  })()
  return () => { dead = true; timers.forEach(clearTimeout) }
}
