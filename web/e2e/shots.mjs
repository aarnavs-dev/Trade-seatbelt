// Playwright check of the dashboard: screenshots, the tamper flow, overflow and contrast audits.
// Needs `make api` and the web server running. BASE=http://127.0.0.1:5173 (dev) or :4173 (vite preview).
import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'

const BASE = process.env.BASE ?? 'http://127.0.0.1:5173'
const OUT = new URL('./shots/', import.meta.url).pathname
mkdirSync(OUT, { recursive: true })
const ARGS = ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist']
let failures = 0
const check = (ok, msg) => { console.log(`${ok ? 'PASS' : 'FAIL'}  ${msg}`); if (!ok) failures++ }

// --- audits run inside the page
const audit = () => {
  const vw = document.documentElement.clientWidth
  const overflow = []
  const scrollers = (el) => { for (let e = el; e; e = e.parentElement) { const o = getComputedStyle(e).overflowX; if ((o === 'auto' || o === 'scroll' || o === 'hidden') && e !== document.documentElement && e !== document.body) return true } return false }
  for (const el of document.querySelectorAll('body *')) {
    const r = el.getBoundingClientRect()
    if (r.width === 0 || r.height === 0 || getComputedStyle(el).visibility === 'hidden') continue
    if ((r.right > vw + 1 || r.left < -1) && !scrollers(el)) overflow.push(`${el.tagName.toLowerCase()}.${String(el.className).slice(0, 40)} [${Math.round(r.left)}..${Math.round(r.right)}]`)
  }
  const lum = (c) => { const f = (v) => (v /= 255) <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]) }
  const parse = (s) => { const m = s.match(/[\d.]+/g)?.map(Number); if (!m || m.length < 3) return null; const k = s.startsWith('color(srgb') ? 255 : 1; return [m[0] * k, m[1] * k, m[2] * k, m[3] ?? 1] }
  const bg = [12, 14, 17] // glass panel over the near-black scene
  const low = []
  const seen = new Set()
  for (const el of document.querySelectorAll('body *')) {
    const own = [...el.childNodes].filter((n) => n.nodeType === 3 && n.textContent.trim()).map((n) => n.textContent.trim()).join(' ')
    if (!own) continue
    const cs = getComputedStyle(el)
    const r = el.getBoundingClientRect()
    if (r.width === 0 || cs.visibility === 'hidden' || cs.opacity === '0' || el.closest('[aria-hidden=true]') || el.closest('.lbl') || el.closest(':disabled')) continue // disabled controls are exempt (WCAG 1.4.3)
    const c = parse(cs.color); if (!c) continue
    const a = c[3] * (+cs.opacity || 1)
    const fg = [0, 1, 2].map((i) => c[i] * a + bg[i] * (1 - a))
    const L1 = lum(fg), L2 = lum(bg), ratio = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05)
    const size = parseFloat(cs.fontSize), large = size >= 24 || (size >= 18.66 && +cs.fontWeight >= 700)
    if (ratio < (large ? 3 : 4.5)) { const k = `${own.slice(0, 30)}|${ratio.toFixed(2)}`; if (!seen.has(k)) { seen.add(k); low.push(`${ratio.toFixed(2)}:1 "${own.slice(0, 40)}" (${size}px)`) } }
  }
  return { overflow, low, sw: document.documentElement.scrollWidth, cw: vw }
}

const browser = await chromium.launch({ args: ARGS })

// ---------------------------------------------------------------- desktop 1440
{
  const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage()
  const errors = []
  page.on('pageerror', (e) => errors.push(e.message))
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))
  await page.goto(BASE)
  await page.waitForSelector('canvas', { timeout: 20000 })
  check(await page.isVisible('[data-testid=sim-label]'), 'desktop: permanent "SIMULATED, no real orders" label is visible')
  await page.click('[data-testid=btn-demo]'); await page.mouse.move(720, 840)
  await page.waitForTimeout(1500); await page.screenshot({ path: OUT + 'desktop-1-live.png' })
  await page.waitForFunction(() => document.querySelector('[data-testid=status]')?.textContent?.includes('seq 25'), null, { timeout: 15000 })
  await page.waitForTimeout(1500)
  check((await page.textContent('[data-testid=net-pnl]')) === '$91.50', 'desktop: live P&L equals the audited figure ($91.50)')
  await page.screenshot({ path: OUT + 'desktop-2-demo.png' })

  // hover a block in the 3D scene: sweep the cursor over the chain until the panel opens
  let hit = false
  for (let x = 760; x < 1300 && !hit; x += 16) { await page.mouse.move(x, 440 - (x - 760) * 0.06); hit = await page.isVisible('[data-testid=block-panel]') }
  check(hit, 'desktop: hovering a 3D block opens its panel')
  if (hit) { await page.mouse.down(); await page.mouse.up(); await page.mouse.move(720, 840); await page.waitForTimeout(400)
    check(await page.isVisible('[data-testid=block-panel]'), 'desktop: clicking pins the block panel')
    await page.screenshot({ path: OUT + 'desktop-3-block.png' }); await page.keyboard.press('Escape') }

  await page.keyboard.press('t'); await page.waitForSelector('[data-testid=verifier-message]')
  const msg = await page.textContent('[data-testid=verifier-message]')
  check(/^chain broken at seq \d+: /.test(msg), `desktop: tamper shows the verifier's message naming the bad seq ("${msg}")`)
  await page.mouse.move(720, 840); await page.waitForTimeout(600); await page.screenshot({ path: OUT + 'desktop-4-tamper-a.png' })
  await page.waitForTimeout(2400); await page.screenshot({ path: OUT + 'desktop-4-tamper-b.png' })
  check((await page.textContent('[data-testid=hash-check]')).includes('MISMATCH'), 'desktop: tampered block fails the in-browser SHA-256 recomputation')
  const a1 = await page.evaluate(audit)
  await page.keyboard.press('v'); await page.waitForTimeout(1300); await page.screenshot({ path: OUT + 'desktop-5-verify.png' })
  await page.waitForTimeout(1800)
  await page.keyboard.press('r'); await page.waitForTimeout(1200)
  check(!(await page.isVisible('text=Tamper detected')), 'desktop: Reset restores the chain')
  await page.keyboard.press('v'); await page.waitForTimeout(3600)
  check((await page.textContent('[data-testid=verifier-message]')).startsWith('chain intact'), 'desktop: Verify on an untouched chain reports it intact')

  await page.keyboard.press('1'); await page.waitForSelector('[data-testid=drawer-recon]'); await page.waitForSelector('[data-testid=finding]'); await page.waitForTimeout(500)
  await page.screenshot({ path: OUT + 'desktop-6-recon.png' })
  await page.click('[data-testid=finding] button:has-text("seq")'); await page.waitForTimeout(2000)
  check((await page.textContent('[data-testid=status]')).includes('reconciliation fixture'), 'desktop: a reconciliation evidence line focuses its block (scene switches to the cited log)')
  await page.screenshot({ path: OUT + 'desktop-7-recon-focus.png' })
  await page.click('text=back to demo log'); await page.waitForTimeout(800)
  await page.keyboard.press('2'); await page.waitForSelector('[data-testid=cf-block]'); await page.waitForTimeout(400); await page.screenshot({ path: OUT + 'desktop-8-cf.png' })
  await page.keyboard.press('3'); await page.waitForSelector('[data-testid=bench-table]'); await page.waitForTimeout(400); await page.screenshot({ path: OUT + 'desktop-9-bench.png' })
  await page.keyboard.press('Escape')
  await page.keyboard.press('Control+k'); await page.waitForSelector('[role=dialog][aria-label="Command palette"]'); await page.keyboard.type('tam'); await page.waitForTimeout(300)
  await page.screenshot({ path: OUT + 'desktop-10-palette.png' })
  await page.keyboard.press('Enter'); await page.waitForSelector('[data-testid=verifier-message]')
  check(true, 'desktop: command palette runs Tamper')
  await page.keyboard.press('r')
  const a2 = await page.evaluate(audit)
  const A = { overflow: [...a1.overflow, ...a2.overflow], low: [...new Set([...a1.low, ...a2.low])], sw: a1.sw, cw: a1.cw }
  check(A.sw <= A.cw && A.overflow.length === 0, `desktop: no horizontal overflow ${A.overflow.slice(0, 3).join('; ')}`)
  check(A.low.length === 0, `desktop: text contrast >= 4.5:1 ${A.low.slice(0, 6).join('; ')}`)
  check(errors.length === 0, `desktop: no console errors ${errors.slice(0, 2).join('; ')}`)
  await page.close()
}

// ---------------------------------------------------------------- reduced motion -> 2D chain
{
  const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: 'reduce' })).newPage()
  await page.goto(BASE); await page.waitForSelector('[role=list]', { timeout: 15000 }); await page.waitForTimeout(1200)
  check((await page.locator('canvas').count()) === 0, 'reduced motion: 3D canvas is not mounted, the SVG chain is shown')
  await page.screenshot({ path: OUT + 'reduced-motion-2d.png' })
  await page.close()
}

// ---------------------------------------------------------------- phone 390
{
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true })
  const page = await ctx.newPage()
  const errors = []
  page.on('pageerror', (e) => errors.push(e.message))
  await page.goto(BASE); await page.waitForSelector('[data-testid=dashboard-phone]'); await page.waitForTimeout(1200)
  check((await page.locator('canvas').count()) === 0, 'phone: no 3D canvas, the 2D chain and a panel stack are used')
  check(await page.isVisible('[data-testid=sim-label]'), 'phone: permanent "SIMULATED, no real orders" label is visible')
  await page.tap('[data-testid=btn-demo]'); await page.waitForFunction(() => document.querySelector('[data-testid=status]')?.textContent?.includes('seq 25'), null, { timeout: 15000 })
  await page.waitForTimeout(1500); await page.screenshot({ path: OUT + 'phone-1-demo.png' })
  await page.tap('[data-testid=btn-tamper]'); await page.waitForSelector('[data-testid=verifier-message]'); await page.waitForTimeout(1500)
  await page.screenshot({ path: OUT + 'phone-2-tamper.png' })
  const b = await page.evaluate(audit)
  await page.evaluate(() => document.querySelector('[data-testid=dashboard-phone]').scrollTo(0, 600)); await page.waitForTimeout(300)
  await page.screenshot({ path: OUT + 'phone-3-panels.png' })
  await page.tap('[data-testid=btn-reset]')
  await page.tap('text=Reconciliation'); await page.waitForSelector('[data-testid=drawer-recon]'); await page.waitForTimeout(600)
  await page.screenshot({ path: OUT + 'phone-4-recon.png' })
  const c = await page.evaluate(audit)
  check(b.sw <= b.cw && c.sw <= c.cw && b.overflow.length + c.overflow.length === 0, `phone: no horizontal overflow ${[...b.overflow, ...c.overflow].slice(0, 4).join('; ')}`)
  check(b.low.length + c.low.length === 0, `phone: text contrast >= 4.5:1 ${[...b.low, ...c.low].slice(0, 6).join('; ')}`)
  check(errors.length === 0, `phone: no page errors ${errors.slice(0, 2).join('; ')}`)
  await page.close()
}
await browser.close()
console.log(failures ? `\n${failures} check(s) failed` : '\nall checks passed')
process.exit(failures ? 1 : 0)
