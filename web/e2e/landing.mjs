// Landing page check: screenshots (desktop 1440, phone 390), copy audit, overflow/contrast, API-down behaviour.
import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'
const BASE = process.env.BASE ?? 'http://127.0.0.1:5173'
const OUT = new URL('./shots/', import.meta.url).pathname
mkdirSync(OUT, { recursive: true })
const ARGS = ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist']
let failures = 0
const check = (ok, msg) => { console.log(`${ok ? 'PASS' : 'FAIL'}  ${msg}`); if (!ok) failures++ }
const audit = () => {
  const vw = document.documentElement.clientWidth
  const overflow = []
  for (const el of document.querySelectorAll('body *')) {
    const r = el.getBoundingClientRect()
    if (r.width === 0 || r.height === 0) continue
    let scroller = false
    for (let e = el; e; e = e.parentElement) { const o = getComputedStyle(e).overflowX; if ((o === 'auto' || o === 'scroll') && e !== document.documentElement && e !== document.body) { scroller = true; break } }
    if ((r.right > vw + 1 || r.left < -1) && !scroller && !el.closest('[data-testid=scene]') && !el.closest('.overflow-hidden')) overflow.push(`${el.tagName.toLowerCase()}.${String(el.className).slice(0, 40)} [${Math.round(r.left)}..${Math.round(r.right)}]`)
  }
  const lum = (c) => { const f = (v) => (v /= 255) <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]) }
  const parse = (s) => { const m = s.match(/[\d.]+/g)?.map(Number); if (!m || m.length < 3) return null; const k = s.startsWith('color(srgb') ? 255 : 1; return [m[0] * k, m[1] * k, m[2] * k, m[3] ?? 1] }
  const bg = [7, 8, 10]
  const low = [], seen = new Set()
  for (const el of document.querySelectorAll('body *')) {
    const own = [...el.childNodes].filter((n) => n.nodeType === 3 && n.textContent.trim()).map((n) => n.textContent.trim()).join(' ')
    if (!own) continue
    const cs = getComputedStyle(el), r = el.getBoundingClientRect()
    if (r.width === 0 || cs.visibility === 'hidden' || +cs.opacity === 0 || el.closest('.lbl') || el.closest(':disabled')) continue
    let op = 1; for (let e = el; e; e = e.parentElement) op *= +getComputedStyle(e).opacity
    if (op < 0.99) continue // mid-reveal animation
    const c = parse(cs.color); if (!c) continue
    const a = c[3]
    const fg = [0, 1, 2].map((i) => c[i] * a + bg[i] * (1 - a))
    const L1 = lum(fg), L2 = lum(bg), ratio = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05)
    const size = parseFloat(cs.fontSize), large = size >= 24 || (size >= 18.66 && +cs.fontWeight >= 700)
    if (ratio < (large ? 3 : 4.5)) { const k = `${own.slice(0, 30)}|${ratio.toFixed(2)}`; if (!seen.has(k)) { seen.add(k); low.push(`${ratio.toFixed(2)}:1 "${own.slice(0, 40)}" (${size}px)`) } }
  }
  return { overflow, low, sw: document.documentElement.scrollWidth, cw: vw }
}
const scrollAll = async (page) => { const h = await page.evaluate(() => document.documentElement.scrollHeight); for (let y = 0; y < h; y += 500) { await page.evaluate((y) => window.scrollTo(0, y), y); await page.waitForTimeout(120) } await page.evaluate(() => window.scrollTo(0, 0)); await page.waitForTimeout(500) }

const browser = await chromium.launch({ args: ARGS })
for (const [name, ctxOpts] of [['desktop', { viewport: { width: 1440, height: 900 } }], ['phone', { viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true }]]) {
  const ctx = await browser.newContext(ctxOpts)
  const page = await ctx.newPage()
  const errors = []
  page.on('pageerror', (e) => errors.push(e.message))
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))
  await page.goto(BASE + '/')
  await page.waitForSelector('[data-testid=hero]')
  if (name === 'desktop') await page.waitForSelector('canvas', { timeout: 20000 })
  await page.waitForTimeout(6500)
  await page.screenshot({ path: OUT + `landing-${name}-1-hero.png` })
  await scrollAll(page)
  await page.waitForSelector('[data-testid=invariant]')
  await page.screenshot({ path: OUT + `landing-${name}-full.png`, fullPage: true, scale: 'css' })
  const text = await page.evaluate(() => document.body.innerText)
  check(/Rules before the trade\./.test(text) && /Receipts after\./.test(text), `${name}: hero headline present`)
  check(await page.isVisible('[data-testid=hero] >> text=SIMULATED, no real orders'), `${name}: SIMULATED label in the hero`)
  for (const w of ['power-loss', '1,000,000-event', 'Tail truncation', 'No real orders']) check(text.toLowerCase().includes(w.toLowerCase()), `${name}: footer states "${w}"`)
  const claims = text.replace('does not claim to make or save anyone money', '').match(/\b(save[sd]?|saving|profit\w*|guarantee\w*|beat the market)\b/gi)
  check(!claims, `${name}: no money-saving / profit claims in the copy ${claims ?? ''}`)
  const nums = await page.evaluate(() => (document.querySelector('#proof .grid > div:first-child')?.innerText ?? '').match(/\d[\d,]*/g) ?? [])
  const bench = await (await fetch(`http://127.0.0.1:8000/bench`)).json()
  const allowed = new Set(); const walk = (o) => { if (typeof o === 'string') (o.match(/\d+/g) ?? []).forEach((t) => allowed.add(t)); else if (typeof o === 'number') { allowed.add(Math.round(o).toLocaleString('en-US')); allowed.add(String(Math.round(o))) } else if (o && typeof o === 'object') Object.values(o).forEach(walk) }; walk(bench)
  const unknown = nums.filter((t) => !allowed.has(t.replace(/,$/, '')))
  check(nums.length >= 6 && unknown.length === 0, `${name}: every number in the Benchmarks column appears in bench/results via /bench (${nums.length} checked, unknown: ${unknown.join(', ') || 'none'})`)
  const inv = await page.locator('[data-testid=invariant]').count()
  check(inv === 12, `${name}: 12 invariants listed (${inv})`)
  const a = await page.evaluate(audit)
  check(a.sw <= a.cw && a.overflow.length === 0, `${name}: no horizontal overflow ${a.overflow.slice(0, 3).join('; ')}`)
  check(a.low.length === 0, `${name}: text contrast ${a.low.slice(0, 5).join('; ')}`)
  await page.click('[data-testid=cta-hero]'); await page.waitForSelector('[data-testid=dashboard], [data-testid=dashboard-phone]')
  check(page.url().endsWith('/app'), `${name}: CTA opens the dashboard`)
  check(errors.length === 0, `${name}: no console errors ${errors.slice(0, 2).join('; ')}`)
  await ctx.close()
}
// API down: the landing page still renders, proof shows no invented numbers
{
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  const page = await ctx.newPage()
  await page.route('**/api/**', (r) => r.abort())
  await page.goto(BASE + '/'); await page.waitForSelector('[data-testid=hero]'); await page.waitForTimeout(1500)
  await page.evaluate(() => document.querySelector('#proof').scrollIntoView()); await page.waitForTimeout(1200)
  await page.screenshot({ path: OUT + 'landing-api-down.png' })
  const t = await page.evaluate(() => document.querySelector('#proof').innerText)
  check(/not running/.test(t) && !/\d{2,3},\d{3}/.test(t), 'api down: Proof section says so and shows no benchmark number')
  await ctx.close()
}
await browser.close()
console.log(failures ? `\n${failures} check(s) failed` : '\nall checks passed')
process.exit(failures ? 1 : 0)
