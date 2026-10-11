// Playwright check of /app/follow: one finished run, a 1440 and a 390 screenshot, overflow + contrast audits.
// Needs `make api` and `make web` running. BASE=http://127.0.0.1:5173
import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'

const BASE = process.env.BASE ?? 'http://127.0.0.1:5173'
const OUT = new URL('./shots/', import.meta.url).pathname
mkdirSync(OUT, { recursive: true })
let failures = 0
const check = (ok, msg) => { console.log(`${ok ? 'PASS' : 'FAIL'}  ${msg}`); if (!ok) failures++ }

const audit = () => {
  const vw = document.documentElement.clientWidth
  const overflow = []
  for (const el of document.querySelectorAll('body *')) {
    const r = el.getBoundingClientRect()
    if (r.width === 0 || r.height === 0) continue
    let scroller = false
    for (let e = el; e; e = e.parentElement) { const o = getComputedStyle(e).overflowX; if ((o === 'auto' || o === 'scroll') && e !== document.documentElement) scroller = true }
    if ((r.right > vw + 1 || r.left < -1) && !scroller) overflow.push(`${el.tagName.toLowerCase()}.${String(el.className).slice(0, 30)} [${Math.round(r.left)}..${Math.round(r.right)}]`)
  }
  const lum = (c) => { const f = (v) => (v /= 255) <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]) }
  const parse = (s) => { const m = s.match(/[\d.]+/g)?.map(Number); if (!m || m.length < 3) return null; const k = s.startsWith('color(srgb') ? 255 : 1; return [m[0] * k, m[1] * k, m[2] * k, m[3] ?? 1] }
  const bg = [12, 14, 17]
  const low = [], seen = new Set()
  for (const el of document.querySelectorAll('body *')) {
    const own = [...el.childNodes].filter((n) => n.nodeType === 3 && n.textContent.trim()).map((n) => n.textContent.trim()).join(' ')
    if (!own) continue
    const cs = getComputedStyle(el)
    if (el.getBoundingClientRect().width === 0 || cs.visibility === 'hidden' || el.closest(':disabled')) continue
    const c = parse(cs.color); if (!c) continue
    const fg = [0, 1, 2].map((i) => c[i] * c[3] + bg[i] * (1 - c[3]))
    const ratio = (Math.max(lum(fg), lum(bg)) + 0.05) / (Math.min(lum(fg), lum(bg)) + 0.05)
    if (ratio < 4.5) { const k = `${own.slice(0, 30)}|${ratio.toFixed(2)}`; if (!seen.has(k)) { seen.add(k); low.push(`${ratio.toFixed(2)}:1 "${own.slice(0, 40)}"`) } }
  }
  return { overflow, low, sw: document.documentElement.scrollWidth, cw: vw }
}

const browser = await chromium.launch()
const errors = []
let run
{
  const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage()
  page.on('pageerror', (e) => errors.push(e.message)); page.on('console', (m) => m.type() === 'error' && !m.text().includes('422') && errors.push(m.text()))   // the 422 is the deliberate bad-limit probe
  await page.goto(BASE + '/app/follow')
  await page.waitForSelector('[data-testid=paper-label]')
  check(await page.isVisible('[data-testid=paper-label]'), '1440: PAPER EXECUTION is visible before anything runs')
  check(await page.isVisible('[data-testid=empty]'), '1440: empty state before the first run')
  await page.fill('[data-testid=limit-maxPosition]', '0'); await page.click('[data-testid=btn-start]')
  await page.waitForSelector('[data-testid=limit-error]')
  check((await page.textContent('[data-testid=limit-error]')).includes('positive'), '1440: the API\'s validation message is shown for a bad limit')
  await page.fill('[data-testid=limit-maxPosition]', '5'); await page.fill('[data-testid=limit-maxLossDollars]', '3')
  await page.click('[data-testid=btn-start]')
  await page.waitForSelector('[data-testid=row]', { timeout: 90000 })
  await page.waitForFunction(() => document.querySelector('[data-testid=btn-start]'), null, { timeout: 90000 })
  await page.waitForSelector('[data-testid=badge-data]')
  const n = await page.locator('[data-testid=row]').count()
  check(n > 0, `1440: ${n} signal rows arrived`)
  const rej = page.locator('[data-testid=row][data-status=REJECTED_BY_RULES]').first()
  if (await rej.count()) { await rej.click(); await page.waitForSelector('[data-testid=explain-detail]'); check(true, '1440: a rejected row opens its explanation') }
  await page.mouse.move(700, 880); await page.waitForTimeout(500)
  await page.screenshot({ path: OUT + 'follow-1440.png' })
  const a = await page.evaluate(audit)
  check(a.sw <= a.cw && a.overflow.length === 0, `1440: no horizontal overflow ${a.overflow.slice(0, 3).join('; ')}`)
  check(a.low.length === 0, `1440: text contrast >= 4.5:1 ${a.low.slice(0, 6).join('; ')}`)
  run = new URL(await page.evaluate(() => performance.getEntriesByType('resource').map((e) => e.name).filter((u) => u.includes('/stream')).pop())).pathname.split('/')[3]
  await page.close()
}
{
  const page = await (await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true })).newPage()
  page.on('pageerror', (e) => errors.push(e.message)); page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))
  await page.goto(BASE + '/app/follow?run=' + run)
  await page.waitForSelector('[data-testid=row]', { timeout: 30000 }); await page.waitForTimeout(1500)
  await page.screenshot({ path: OUT + 'follow-390.png', fullPage: true })
  const a = await page.evaluate(audit)
  check(a.sw <= a.cw && a.overflow.length === 0, `390: no horizontal overflow ${a.overflow.slice(0, 4).join('; ')}`)
  check(a.low.length === 0, `390: text contrast >= 4.5:1 ${a.low.slice(0, 6).join('; ')}`)
  await page.close()
}
check(errors.length === 0, `no console errors ${errors.slice(0, 2).join('; ')}`)
await browser.close()
console.log(failures ? `${failures} FAILED` : 'ALL PASSED', 'screenshots in', OUT, 'run', run)
process.exit(failures ? 1 : 0)
