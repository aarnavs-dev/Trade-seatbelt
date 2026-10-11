import { describe, expect, it } from 'vitest'
import { DEFAULT_LIMITS, dollarsToCents, errorText, FILTERS, nyTime, STATUS, toApiLimits } from './follow'

describe('follow helpers', () => {
  it('turns dollars into integer cents without floats', () => {
    expect(dollarsToCents('50')).toBe(5000)
    expect(dollarsToCents('12.5')).toBe(1250)
    expect(dollarsToCents('0.07')).toBe(7)
    expect(dollarsToCents('1.005')).toBeNull()
    expect(dollarsToCents('-3')).toBeNull()
    expect(dollarsToCents('abc')).toBeNull()
  })
  it('sends whole numbers as numbers and anything else as typed, so the API explains the problem', () => {
    expect(toApiLimits(DEFAULT_LIMITS)).toEqual({ max_position_per_symbol: 100, max_daily_loss: 5000, price_band_pct: 5, loss_streak_cooldown: { losses: 3 } })
    expect(toApiLimits({ ...DEFAULT_LIMITS, maxPosition: '1.5', cooldownLosses: '0' })).toMatchObject({ max_position_per_symbol: '1.5', loss_streak_cooldown: { losses: 0 } })
  })
  it('has one chip per status and a filter for each', () => {
    expect(Object.keys(STATUS).sort()).toEqual(['CLIPPED', 'COPIED', 'REJECTED_BY_RULES', 'STOPPED_BY_COPIER'])
    expect(FILTERS.slice(1).map((f) => f.key).sort()).toEqual(Object.keys(STATUS).sort())
  })
  it('shows New York time, including across the daylight-saving change', () => {
    expect(nyTime(Date.UTC(2026, 8, 30, 14, 35))).toBe('10:35')
    expect(nyTime(Date.UTC(2026, 10, 2, 15, 35))).toBe('10:35')
    expect(nyTime(null)).toBe('--:--')
  })
  it('prefers the API message and falls back sensibly', () => {
    expect(errorText(422, { message: 'max_daily_loss must be a positive integer, got 0' })).toContain('positive')
    expect(errorText(422, { detail: [{ msg: 'bad feed' }] })).toBe('bad feed')
    expect(errorText(502, null)).toContain('make api')
  })
})
