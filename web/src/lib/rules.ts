import { call, json } from './api'
import { dollarsToCents } from './follow'

export interface Limits { max_position_per_symbol: number; max_daily_loss: number; price_band_pct: number; loss_streak_cooldown: { losses: number; cooldown_seconds: number } }
export interface RulesState { saved: boolean; limits: Limits; hash: string | null; saved_at: string | null; presets: Record<string, Limits>; defaults: Limits }

/** What the person edits: dollars, percent, counts and minutes, all as text. */
export interface Form { maxPosition: string; maxLossDollars: string; bandPct: string; cooldownLosses: string; cooldownMinutes: string }

export const PRESET_NAMES = [
  { key: 'cautious', label: 'Cautious' }, { key: 'balanced', label: 'Balanced' }, { key: 'active', label: 'Active' },
] as const

export function formFrom(l: Limits): Form {
  return { maxPosition: String(l.max_position_per_symbol), maxLossDollars: (l.max_daily_loss / 100).toFixed(2).replace(/\.00$/, ''), bandPct: String(l.price_band_pct),
           cooldownLosses: String(l.loss_streak_cooldown.losses), cooldownMinutes: String(l.loss_streak_cooldown.cooldown_seconds / 60) }
}

const whole = (s: string): number | string => (/^\s*-?\d+\s*$/.test(s) ? Number(s) : s)

/** Form text -> the API's body. Anything that is not a whole number is sent as typed so the API's message explains it. */
export function toLimits(f: Form): Record<string, unknown> {
  const mins = whole(f.cooldownMinutes)
  return {
    max_position_per_symbol: whole(f.maxPosition),
    max_daily_loss: dollarsToCents(f.maxLossDollars) ?? f.maxLossDollars,
    price_band_pct: whole(f.bandPct),
    loss_streak_cooldown: { losses: whole(f.cooldownLosses), cooldown_seconds: typeof mins === 'number' ? mins * 60 : mins },
  }
}

/** The one live sentence under the form. */
export function sentence(f: Form): string {
  const n = /^\s*[1-9]\d*\s*$/.test(f.maxPosition) ? Number(f.maxPosition).toLocaleString('en-US') : null
  return n ? `With these rules, an order that would take you above ${n} shares of one stock is blocked.` : 'Enter a whole number of shares to see what this rule does.'
}

export const rules = {
  get: () => call<RulesState>('/rules'),
  save: (f: Form) => call<RulesState>('/rules', json('PUT', toLimits(f))),
}
