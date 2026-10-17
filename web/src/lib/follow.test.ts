import { describe, expect, it } from 'vitest'
import { byReason, DEFAULT_OPTIONS, dollarsToCents, FILTERS, nyTime, rowBadge, shortKey, SOURCE_LABEL, STATUS, toRequest, type Row } from './follow'
import { LOCAL_FEED_WARNING, SOLANA_DOES_NOT, SOLANA_STEPS, SOLANA_WHAT_IS_PUBLIC } from './copy'
import { errorText } from './api'
import { formFrom, sentence, toLimits } from './rules'

describe('follow helpers', () => {
  it('turns dollars into integer cents without floats', () => {
    expect(dollarsToCents('50')).toBe(5000)
    expect(dollarsToCents('12.5')).toBe(1250)
    expect(dollarsToCents('0.07')).toBe(7)
    expect(dollarsToCents('1.005')).toBeNull()
    expect(dollarsToCents('-3')).toBeNull()
  })
  it('sends only the options that were set, as integers', () => {
    expect(toRequest(DEFAULT_OPTIONS)).toEqual({ feed: 'auto', leader: 'momentum', pace: 'normal', size: { mode: 'same' }, session: {} })
    const r = toRequest({ ...DEFAULT_OPTIONS, copyMode: 'percent', copyValue: '50', maxTrades: '5', maxTotalDollars: '2500', maxTradeDollars: '12.5' })
    expect(r.size).toEqual({ mode: 'percent', value: 50 })
    expect(r.session).toEqual({ max_trades: 5, max_total_amount: 250000, max_trade_amount: 1250 })
    expect(toRequest({ ...DEFAULT_OPTIONS, maxVolume: '1.5' }).session).toEqual({ max_volume: '1.5' })   // not a whole number: sent as typed, the API explains
  })
  it('has one dot and label per status and a filter for each', () => {
    expect(Object.keys(STATUS).sort()).toEqual(['CLIPPED', 'COPIED', 'REJECTED_BY_RULES', 'STOPPED_BY_COPIER'])
    expect(FILTERS.slice(1).map((f) => f.key).sort()).toEqual(Object.keys(STATUS).sort())
    expect(Object.values(STATUS).every((s) => s.dot.startsWith('dot-'))).toBe(true)
  })
  it('shows New York time across the daylight-saving change, and short keys', () => {
    expect(nyTime(Date.UTC(2026, 8, 30, 14, 35))).toBe('10:35')
    expect(nyTime(Date.UTC(2026, 10, 2, 15, 35))).toBe('10:35')
    expect(shortKey('CfUDMw61yyZKofYuptmTNLcaxFZTaytKV3HAhkh7Ls7w')).toBe('CfUD…Ls7w')
  })
  it('prefers the API message and falls back sensibly', () => {
    expect(errorText(422, { message: 'max_daily_loss must be a positive integer, got 0' })).toContain('positive')
    expect(errorText(422, { detail: [{ msg: 'bad feed' }] })).toBe('bad feed')
    expect(errorText(502, null)).toContain('make api')
  })
})

describe('rules form', () => {
  const l = { max_position_per_symbol: 100, max_daily_loss: 5000, price_band_pct: 5, loss_streak_cooldown: { losses: 3, cooldown_seconds: 300 } }
  it('round-trips between the API limits and the editable text', () => {
    const f = formFrom(l)
    expect(f).toEqual({ maxPosition: '100', maxLossDollars: '50', bandPct: '5', cooldownLosses: '3', cooldownMinutes: '5' })
    expect(toLimits(f)).toEqual(l)
    expect(toLimits({ ...f, maxPosition: '1.5', maxLossDollars: '0.001' })).toMatchObject({ max_position_per_symbol: '1.5', max_daily_loss: '0.001' })
  })
  it('writes the live sentence from the position limit', () => {
    expect(sentence(formFrom(l))).toBe('With these rules, an order that would take you above 100 shares of one stock is blocked.')
    expect(sentence({ ...formFrom(l), maxPosition: '1000' })).toContain('1,000 shares')
    expect(sentence({ ...formFrom(l), maxPosition: 'x' })).toContain('whole number')
  })
})


describe('signal source', () => {
  const base: Row = { n: 1, signal_id: 'local:12', leader: 'local', symbol: 'AAPL', side: 'BUY', leader_qty: 10, qty: 10, price: 19000, leader_ts: 0,
    status: 'COPIED', reason_code: 'OK', reason_text: '', tx: null, slot: null, explorer_url: null, source: 'local', hash: null }
  const KEY = 'CfUDMw61yyZKofYuptmTNLcaxFZTaytKV3HAhkh7Ls7w'
  const SIG = '5Kd3v9mQp7ZtYw2uXc8bNfLhJrE4sAaVgTyUoPiRkWnMzC1xDqHbGeSjFlOtIv6yBnMcXz3LkQpWrEtYuIoPaSdFgHjKlZxCvBnM'

  it('says where a row came from, and links a transaction only for a row that has one', () => {
    const local = rowBadge(base, 'Momentum')
    expect(local).toMatchObject({ leader: 'Momentum', signal: '#12', source: 'Local feed', tone: 'local', tx: null, note: 'not on-chain' })
    const chain = rowBadge({ ...base, signal_id: `${KEY}:7`, leader: KEY, source: 'chain', tx: SIG, slot: 99, explorer_url: `https://explorer.solana.com/tx/${SIG}?cluster=devnet` }, 'Momentum')
    expect(chain).toMatchObject({ signal: '#7', source: 'Solana devnet', tone: 'chain', note: 'on Solana devnet' })
    expect(chain.tx).toEqual({ short: shortKey(SIG), url: `https://explorer.solana.com/tx/${SIG}?cluster=devnet` })
    const cached = rowBadge({ ...base, source: 'chain_cache', tx: SIG, explorer_url: 'u' }, 'Momentum')
    expect(cached).toMatchObject({ source: 'Solana devnet (saved copy)', tone: 'cache', note: 'saved copy of an earlier devnet read' })
  })
  it('never shows chain styling for a row without a transaction, whatever it claims', () => {
    for (const source of ['chain', 'chain_cache'] as const) {
      const b = rowBadge({ ...base, source, tx: null }, 'Momentum')
      expect(b).toMatchObject({ source: 'Local feed', tone: 'local', tx: null })
    }
    expect(SOURCE_LABEL.local).not.toMatch(/solana/i)
  })
  it('names the two kinds of non-copied signal by where they stopped', () => {
    expect(STATUS.STOPPED_BY_COPIER.label).toBe('Skipped before the gate')
    expect(STATUS.REJECTED_BY_RULES.label).toBe('Blocked by your rules')
    expect(FILTERS.map((f) => f.label)).toEqual(['All', 'Copied', 'Skipped', 'Blocked', 'Clipped'])
    expect(byReason({ SELL_WITHOUT_POSITION: 4, DUPLICATE_SIGNAL: 1 })).toBe('SELL_WITHOUT_POSITION 4, DUPLICATE_SIGNAL 1')
    expect(byReason({})).toBe('')
    expect(byReason(undefined)).toBe('')
  })
  it('keeps the shared Solana wording honest: what it does, what it does not, what is public', () => {
    expect(SOLANA_STEPS.map((s) => s.title)).toEqual(['Publish', 'Verify', 'Decide'])
    expect(SOLANA_DOES_NOT).toMatch(/does not run your trades/)
    expect(SOLANA_WHAT_IS_PUBLIC).toMatch(/signal only/); expect(SOLANA_WHAT_IS_PUBLIC).toMatch(/no funds and executes nothing/)
    expect(SOLANA_WHAT_IS_PUBLIC).not.toMatch(/hash/i)          // today's chain holds signals, not hashes: do not claim otherwise
    expect(LOCAL_FEED_WARNING).toMatch(/^Not from Solana/)
  })
})
