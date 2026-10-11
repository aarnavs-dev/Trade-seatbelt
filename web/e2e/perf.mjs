// Initial load of the production build (vite preview). Cold cache each run; 5 runs; median reported.
import { chromium } from 'playwright'
const BASE = process.env.BASE ?? 'http://127.0.0.1:4173'
const browser = await chromium.launch({ args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] })
const runs = []
for (let i = 0; i < 5; i++) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  const page = await ctx.newPage()
  const t0 = Date.now()
  await page.goto(BASE, { waitUntil: 'load' })
  await page.waitForSelector('[data-testid=dashboard]')
  const shell = Date.now() - t0
  await page.waitForSelector('canvas')
  const canvas = Date.now() - t0
  const nav = await page.evaluate(() => {
    const n = performance.getEntriesByType('navigation')[0]
    const fcp = performance.getEntriesByType('paint').find((p) => p.name === 'first-contentful-paint')?.startTime
    const res = performance.getEntriesByType('resource').map((r) => ({ n: r.name.split('/').pop(), t: r.transferSize, d: r.decodedBodySize }))
    return { dcl: n.domContentLoadedEventEnd, load: n.loadEventEnd, fcp, res }
  })
  runs.push({ shell, canvas, ...nav })
  await ctx.close()
}
const med = (k) => runs.map((r) => r[k]).sort((a, b) => a - b)[2]
console.log(JSON.stringify({ runs: runs.length, median_ms: { fcp: Math.round(med('fcp')), domContentLoaded: Math.round(med('dcl')), load: Math.round(med('load')), dashboard_shell_visible: med('shell'), webgl_canvas_mounted: med('canvas') } }))
const r = runs[2]
const js = r.res.filter((x) => x.n.endsWith('.js')), css = r.res.filter((x) => x.n.endsWith('.css')), fonts = r.res.filter((x) => x.n.endsWith('.woff2'))
const sum = (a, k) => a.reduce((s, x) => s + x[k], 0)
console.log(JSON.stringify({ js_files: js.map((x) => `${x.n} ${x.d}`), js_decoded_bytes: sum(js, 'd'), css_decoded_bytes: sum(css, 'd'), fonts: fonts.map((x) => `${x.n} ${x.d}`) }))
await browser.close()
