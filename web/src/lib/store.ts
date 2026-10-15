import { useSyncExternalStore } from 'react'
import { api, ApiError } from './api'
import type { DemoStatus, LogEvent, Report, Source, TamperResult } from './types'

/** Default visual timing of a live event: it flies in from `spawnAt`, lands on the chain at `spawnAt + travelMs`. */
const TRAVEL_MS = 760
const SPAWN_GAP_MS = 120
const MAX_BACKLOG_MS = 1400
const KEEP = 1200

export interface Block {
  ev: LogEvent
  /** performance.now() when the order enters the tunnel; -Infinity for history that is simply there */
  spawnAt: number
  /** read from Solana devnet (the follow strip marks these) */
  onchain?: boolean
}

export type Conn = 'connecting' | 'live' | 'reconnecting' | 'error'

export interface Snap {
  source: Source
  conn: Conn
  connError: string | null
  total: number
  firstSeq: number
  version: number
  selected: number | null
  viewSeq: number | null
  hover: number | null
  followLive: boolean
  report: Report | null
  reportError: string | null
  tamper: { at: number; result: TamperResult } | null
  resetAt: number
  verify: { startedAt: number; running: boolean; outcome: 'ok' | 'broken' | null; message: string | null; events: number; badSeq: number | null } | null
  demo: DemoStatus | null
  busy: string | null
  notice: string | null
}

export const SWEEP_MS = 2200
const reduced = () => typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches

export interface StoreOptions { travelMs?: number; spawnGapMs?: number }

/** One chain of blocks plus its view state. The audit page's log stream, the follow strip and the landing hero each own one. */
export class ChainStore {
  readonly travelMs: number
  private spawnGapMs: number
  constructor(opts: StoreOptions = {}) { this.travelMs = opts.travelMs ?? TRAVEL_MS; this.spawnGapMs = opts.spawnGapMs ?? SPAWN_GAP_MS }
  /** mutable window of the chain, ascending contiguous seq. The 3D scene reads this every frame. */
  blocks: Block[] = []
  snap: Snap = {
    source: 'demo', conn: 'connecting', connError: null, total: 0, firstSeq: 1, version: 0, selected: null, viewSeq: null, hover: null,
    followLive: true, report: null, reportError: null, tamper: null, resetAt: 0, verify: null, demo: null, busy: null, notice: null,
  }
  private subs = new Set<() => void>()
  private es: EventSource | null = null
  private lastSpawn = 0
  private helloTotal = 0
  private reportTimer: number | undefined
  private notifyQueued = false
  private seqIndex = new Map<number, Block>()
  private reportOn = true

  // ------------------------------------------------------------ subscription
  subscribe = (fn: () => void) => {
    this.subs.add(fn)
    return () => this.subs.delete(fn)
  }
  getSnap = () => this.snap
  private set(p: Partial<Snap>) {
    this.snap = { ...this.snap, ...p }
    if (!this.notifyQueued) {
      this.notifyQueued = true
      queueMicrotask(() => {
        this.notifyQueued = false
        this.subs.forEach((f) => f())
      })
    }
  }
  byseq(seq: number) {
    return this.seqIndex.get(seq)?.ev
  }

  // ------------------------------------------------------------ stream
  connect(source: Source = this.snap.source) {
    this.es?.close()
    this.blocks = []
    this.seqIndex.clear()
    this.lastSpawn = 0
    this.helloTotal = 0
    this.reportOn = true
    this.set({ source, conn: 'connecting', connError: null, total: 0, firstSeq: 1, version: this.snap.version + 1, selected: null, viewSeq: null, hover: null, followLive: true })
    const es = new EventSource(api.streamUrl(source))
    this.es = es
    es.addEventListener('hello', (m) => {
      this.helloTotal = (JSON.parse((m as MessageEvent).data) as { total: number }).total
      this.set({ conn: 'live', connError: null })
    })
    es.addEventListener('block', (m) => this.push(JSON.parse((m as MessageEvent).data) as LogEvent))
    es.addEventListener('reset', () => {
      es.close()
      setTimeout(() => this.es === es && this.connect(), 250)
    })
    es.onerror = () => {
      if (this.es !== es) return
      this.set({ conn: es.readyState === EventSource.CLOSED ? 'error' : 'reconnecting', connError: 'Lost the event stream. Is `make api` running?' })
    }
    es.onopen = () => this.set({ conn: 'live', connError: null })
    this.refreshReport(0)
  }

  /** Add one block (also used by the SSE path). `animate` false means it is history and simply appears. */
  push(ev: LogEvent, opts: { animate?: boolean; onchain?: boolean } = {}) {
    const last = this.blocks[this.blocks.length - 1]
    if (last && ev.seq <= last.ev.seq) return // resumed stream repeating itself
    const now = performance.now()
    let spawnAt = -Infinity
    if (opts.animate !== false && ev.seq > this.helloTotal) {
      // a live event: animate it, but never let a burst queue up more than ~1.4 s of animation
      const at = Math.max(now, this.lastSpawn + this.spawnGapMs)
      spawnAt = at - now > MAX_BACKLOG_MS || reduced() ? -Infinity : at
      if (spawnAt !== -Infinity) this.lastSpawn = at
    }
    const b: Block = { ev, spawnAt, onchain: opts.onchain }
    this.blocks.push(b)
    this.seqIndex.set(ev.seq, b)
    if (this.blocks.length > KEEP) {
      for (const old of this.blocks.splice(0, 200)) this.seqIndex.delete(old.ev.seq)
    }
    this.set({
      total: ev.seq,
      firstSeq: this.blocks[0].ev.seq,
      version: this.snap.version + 1,
      // a new demo run is a new chain: forget any tamper display
      ...(!last && ev.seq <= 2 ? { tamper: null, verify: null } : {}), // the first order of a log is seq 1 (v1) or seq 2 (after the rules record)
    })
    if (this.reportOn) this.refreshReport(350)
  }

  // ------------------------------------------------------------ report
  /** Throttled, not debounced: a steady stream of events must still refresh the figures about every `delay` ms. */
  refreshReport(delay = 0) {
    if (this.reportTimer !== undefined && delay > 0) return
    window.clearTimeout(this.reportTimer)
    this.reportTimer = window.setTimeout(async () => {
      this.reportTimer = undefined
      const source = this.snap.source
      try {
        const report = await api.report(source)
        if (source === this.snap.source) this.set({ report, reportError: null })
      } catch (e) {
        this.set({ reportError: (e as Error).message })
      }
    }, delay)
  }

  // ------------------------------------------------------------ selection
  select(seq: number | null) {
    this.set({ selected: seq, viewSeq: null, followLive: seq === null ? this.snap.followLive : false })
  }
  hover(seq: number | null) {
    if (seq !== this.snap.hover) this.set({ hover: seq })
  }
  follow() {
    this.set({ followLive: true, selected: null, viewSeq: null })
  }
  /** Mouse wheel: move the stage back (+) or forward through the chain without selecting anything. */
  scrub(delta: number) {
    const s = this.snap
    const base = s.followLive ? s.total : (s.viewSeq ?? s.selected ?? s.total)
    const viewSeq = Math.max(s.firstSeq, Math.min(s.total, base + delta))
    this.set(viewSeq >= s.total && s.selected === null ? { followLive: true, viewSeq: null } : { followLive: false, viewSeq })
  }
  /** Used by reconciliation evidence lines: the cited seq lives in the bundled recon log. */
  focusSeq(source: Source, seq: number) {
    if (this.snap.source !== source) {
      this.connect(source)
      const t = window.setInterval(() => {
        if (this.seqIndex.has(seq)) {
          window.clearInterval(t)
          this.select(seq)
        }
      }, 60)
      window.setTimeout(() => window.clearInterval(t), 4000)
    } else this.select(seq)
  }

  // ------------------------------------------------------------ actions
  private async guard(name: string, fn: () => Promise<void>) {
    if (this.snap.busy) return
    this.set({ busy: name, notice: null })
    try {
      await fn()
    } catch (e) {
      this.set({ notice: e instanceof ApiError ? e.message : String(e) })
    } finally {
      this.set({ busy: null })
    }
  }

  runDemo() {
    return this.guard('demo', async () => {
      this.set({ tamper: null, verify: null, report: null })
      let st = await api.demoRun()
      this.connect('demo')
      this.set({ demo: st })
      while (st.state === 'running') {
        await new Promise((r) => setTimeout(r, 250))
        st = await api.demoStatus()
        this.set({ demo: st })
      }
      if (st.state === 'failed') throw new Error(`Demo failed: ${st.error}`)
      this.refreshReport(200)
    })
  }

  tamper() {
    return this.guard('tamper', async () => {
      if (this.snap.tamper) return
      const result = await api.tamper(this.snap.source)
      const phone = matchMedia('(max-width: 767px)').matches // on a phone the 2D chain scrolls to the block; no sheet pops over the verdict
      this.set({ tamper: { at: performance.now(), result }, selected: phone ? null : result.tampered_seq, followLive: phone, verify: null })
    })
  }

  reset() {
    this.set({ tamper: null, verify: null, resetAt: performance.now(), notice: null })
  }

  /** Verify = the server's verdict (report.chain, or the stored verdict for the tampered copy) played as a sweep. */
  verify() {
    return this.guard('verify', async () => {
      let badSeq: number | null = null
      let message: string
      const events = this.snap.total
      if (this.snap.tamper) {
        badSeq = this.snap.tamper.result.first_bad_seq
        message = this.snap.tamper.result.message
      } else {
        const report = await api.report(this.snap.source)
        this.set({ report })
        badSeq = report.chain.first_bad_seq
        message = report.chain.ok ? `chain intact: ${report.chain.events_verified} events verified` : `chain broken at seq ${badSeq}: ${report.chain.reason}`
      }
      // bring the verdict position on stage: the first bad block, or the head of the chain
      if (badSeq !== null) this.select(badSeq)
      else if (!this.snap.followLive || this.snap.selected !== null) this.follow()
      const dur = reduced() ? 0 : SWEEP_MS
      this.set({ verify: { startedAt: performance.now(), running: dur > 0, outcome: dur > 0 ? null : badSeq === null ? 'ok' : 'broken', message: dur > 0 ? null : message, events, badSeq } })
      if (dur > 0) {
        await new Promise((r) => setTimeout(r, dur))
        const v = this.snap.verify
        if (v) this.set({ verify: { ...v, running: false, outcome: badSeq === null ? 'ok' : 'broken', message } })
      }
    })
  }

  /** Forget every block (a new run is a new chain). */
  clear() {
    this.blocks = []
    this.seqIndex.clear()
    this.lastSpawn = 0
    this.set({ total: 0, firstSeq: 1, version: this.snap.version + 1, selected: null, viewSeq: null, hover: null, followLive: true, conn: 'live', tamper: null, verify: null })
  }

  clearNotice() {
    this.set({ notice: null })
  }
}

/** The audit page's store: the gate log stream (demo or recon source). */
export const store = new ChainStore()
export const useSnap = (st: ChainStore = store) => useSyncExternalStore(st.subscribe, st.getSnap)

/** First seq of the chain that the current display treats as invalid (tamper demo, or a really broken log). */
export function badFrom(s: Snap): number | null {
  if (s.tamper) return s.tamper.result.first_bad_seq
  return s.report && !s.report.chain.ok ? s.report.chain.first_bad_seq : null
}
