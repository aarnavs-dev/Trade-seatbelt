import { expect, it } from 'vitest'
import { money } from './format'

it('formats minor units like core/money.py', () => {
  expect(money(9150)).toBe('$91.50')
  expect(money(-101990)).toBe('-$1,019.90')
  expect(money(5)).toBe('$0.05')
  expect(money(5400, true)).toBe('+$54.00')
})
