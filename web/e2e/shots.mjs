// Playwright check of the new pages: one screenshot per page at 1440 (and / and /follow at 390), overflow and contrast audits,
// and the rules -> follow flow. Needs `make api` and `make web` running. BASE=http://127.0.0.1:5173
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
    if ((r.right > vw + 1 || r.left < -1) && !scroller && !el.closest('svg')) overflow.push(`${el.tagName.toLowerCase()}.${String(el.className).slice(0, 30)} [${Math.round(r.left)}..${Math.round(r.right)}]`)
  }
  const lum = (c) => { const f = (v) => (v /= 255) <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]) }
  const parse = (s) => { const m = s.match(/[\d.]+/g)?.map(Number); if (!m || m.length < 3) return null; const k = s.startsWith('color(srgb') ? 255 : 1; return [m[0] * k, m[1] * k, m[2] * k, m[3] ?? 1] }
  const bg = [250, 248, 243]
  const low = [], seen = new Set()
  for (const el of document.querySelectorAll('body *')) {
    const own = [...el.childNodes].filter((n) => n.nodeType === 3 && n.textContent.trim()).map((n) => n.textContent.trim()).join(' ')
    if (!own) continue
    const cs = getComputedStyle(el)
    if (el.getBoundingClientRect().width === 0 || cs.visibility === 'hidden' || el.closest(':disabled') || el.closest('svg')) continue
    const c = parse(cs.color); if (!c) continue
    const back = el.closest('.btn-primary') ? [42, 75, 179] : bg   // the primary button's own fill
    const fg = [0, 1, 2].map((i) => c[i] * c[3] + back[i] * (1 - c[3]))
    const ratio = (Math.max(lum(fg), lum(back)) + 0.05) / (Math.min(lum(fg), lum(back)) + 0.05)
    const size = parseFloat(cs.fontSize), large = size >= 24 || (size >= 18.66 && +cs.fontWeight >= 700)
    if (ratio < (large ? 3 : 4.5)) { const k = `${own.slice(0, 30)}|${ratio.toFixed(2)}`; if (!seen.has(k)) { seen.add(k); low.push(`${ratio.toFixed(2)}:1 "${own.slice(0, 40)}" (${size}px)`) } }
  }
  return { overflow, low, sw: document.documentElement.scrollWidth, cw: vw }
}
const report = (a, w) => { check(a.sw <= a.cw && a.overflow.length === 0, `${w}: no horizontal overflow ${a.overflow.slice(0, 3).join('; ')}`); check(a.low.length === 0, `${w}: text contrast ok ${a.low.slice(0, 5).join('; ')}`) }

const browser = await chromium.launch()
const errors = []
const watch = (page) => { page.on('pageerror', (e) => errors.push(e.message)); page.on('console', (m) => m.type() === 'error' && !/status of (4\d\d)/.test(m.text()) && errors.push(m.text())) }
let run
{
  const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage(); watch(page)
  // landing
  await page.goto(BASE + '/'); await page.waitForSelector('[data-testid=cta-rules]')
  check((await page.textContent('h1')) === 'Follow any trader, but never beyond your own limits.', '/: the headline is exact')
  check((await page.textContent('[data-testid=net-indicator]')).includes('Devnet · Paper trading'), '/: the persistent "Devnet · Paper trading" indicator is in the top bar')
  await page.waitForFunction(() => document.querySelectorAll('.sig').length > 0, null, { timeout: 8000 }); check(true, '/: dots travel along the flow')
  await page.waitForFunction(() => /^Stopped:/.test(document.querySelector('[data-testid=flow-caption]')?.textContent ?? ''), null, { timeout: 25000 }); check(true, '/: a dot stops at "Your rules" and the caption shows a stop reason')
  await page.screenshot({ path: OUT + 'landing-1440.png', fullPage: true }); report(await page.evaluate(audit), '1440 /')
  // rules
  await page.goto(BASE + '/rules'); await page.waitForSelector('[data-testid=live-sentence]')
  await page.click('[data-testid=preset-cautious]')
  check((await page.inputValue('[data-testid=limit-maxPosition]')) === '25', '/rules: the Cautious preset fills the fields')
  await page.fill('[data-testid=limit-maxPosition]', '0'); await page.click('[data-testid=save]'); await page.waitForSelector('[data-testid=save-error]')
  check((await page.textContent('[data-testid=save-error]')).includes('positive'), '/rules: the API\'s message is shown for an invalid value')
  await page.fill('[data-testid=limit-maxPosition]', '5')
  check((await page.textContent('[data-testid=live-sentence]')).includes('above 5 shares of one stock is blocked'), '/rules: the live sentence follows the field')
  await page.click('[data-testid=save]'); await page.waitForSelector('[data-testid=rules-hash]')
  check(/^[0-9a-f]{12}$/.test(await page.textContent('[data-testid=rules-hash]')), '/rules: the short rules hash is shown after saving')
  await page.screenshot({ path: OUT + 'rules-1440.png', fullPage: true }); report(await page.evaluate(audit), '1440 /rules')
  // follow
  await page.goto(BASE + '/follow'); await page.waitForSelector('[data-testid=setup]')
  check(!(await page.isVisible('[data-testid=rules-banner]')), '/follow: no banner once rules are saved')
  await page.click('[data-testid=feed-local]'); await page.click('[data-testid=pace-fast]')
  await page.fill('[data-testid=session-maxTrades]', '4')
  await page.click('[data-testid=btn-start]')
  await page.waitForSelector('[data-testid=row]', { timeout: 90000 })
  await page.waitForFunction(() => /finished/.test(document.querySelector('[data-testid=strip]')?.textContent ?? ''), null, { timeout: 120000 })
  check(await page.isVisible('[data-testid=badge-data]') && await page.isVisible('[data-testid=badge-feed]'), '/follow: the source badges are shown')
  check((await page.textContent('[data-testid=badge-feed]')) === 'local feed', '/follow: the feed badge is the manifest text')
  const rej = page.locator('[data-testid=row][data-status=REJECTED_BY_RULES]').first()
  if (await rej.count()) { await rej.click(); await page.waitForSelector('[data-testid=explain-detail]'); check(true, '/follow: a row opens its explanation in the drawer') }
  await page.mouse.move(100, 880); await page.waitForTimeout(400)
  await page.screenshot({ path: OUT + 'follow-1440.png', fullPage: true }); report(await page.evaluate(audit), '1440 /follow')
  run = new URL(await page.evaluate(() => performance.getEntriesByType('resource').map((e) => e.name).filter((u) => u.includes('/stream')).pop())).pathname.split('/')[3]
  for (const p of ['/dashboard', '/audit']) { await page.goto(BASE + p); await page.waitForSelector('h1'); await page.screenshot({ path: OUT + `${p.slice(1)}-1440.png` }); report(await page.evaluate(audit), `1440 ${p}`) }
  await page.goto(BASE + '/app/follow'); await page.waitForURL('**/follow'); check(true, 'old route /app/follow redirects to /follow')
  await page.goto(BASE + '/app'); await page.waitForURL('**/dashboard'); check(true, 'old route /app redirects to /dashboard')
  await page.close()
}
{
  const page = await (await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true })).newPage(); watch(page)
  await page.goto(BASE + '/'); await page.waitForSelector('[data-testid=cta-rules]'); await page.waitForTimeout(1500)
  await page.screenshot({ path: OUT + 'landing-390.png', fullPage: true }); report(await page.evaluate(audit), '390 /')
  await page.goto(BASE + '/follow?run=' + run); await page.waitForSelector('[data-testid=row]', { timeout: 30000 }); await page.waitForTimeout(1500)
  await page.screenshot({ path: OUT + 'follow-390.png', fullPage: true }); report(await page.evaluate(audit), '390 /follow')
  await page.close()
}
check(errors.length === 0, `no console errors ${errors.slice(0, 2).join('; ')}`)
await browser.close()
console.log(failures ? `${failures} FAILED` : 'ALL PASSED', 'screenshots in', OUT)
process.exit(failures ? 1 : 0)
