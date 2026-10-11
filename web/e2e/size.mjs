// Production bundle size per route (raw and gzip) and initial load time. Run after `npm run build`, with `make api` up and
// `npx vite preview --port 4173` serving dist/. Load times are from this machine with software WebGL: indicative, not a benchmark.
import { chromium } from 'playwright'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { gzipSync } from 'node:zlib'

const DIST = new URL('../dist/assets/', import.meta.url).pathname
const kb = (n) => (n / 1024).toFixed(1) + ' kB'
const files = readdirSync(DIST).filter((f) => /\.(js|css)$/.test(f)).map((f) => ({ f, raw: statSync(DIST + f).size, gz: gzipSync(readFileSync(DIST + f)).length }))
for (const x of files.sort((a, b) => b.raw - a.raw)) console.log(`${x.f.padEnd(40)} ${kb(x.raw).padStart(10)}  gzip ${kb(x.gz).padStart(9)}`)

const BASE = process.env.BASE ?? 'http://127.0.0.1:4173'
const browser = await chromium.launch({ args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] })
for (const route of ['/', '/rules', '/follow', '/dashboard', '/audit']) {
  const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage()
  let bytes = 0, js = 0
  page.on('response', async (r) => { try { const b = (await r.body()).length; bytes += b; if (r.url().endsWith('.js')) js += b } catch { /* aborted */ } })
  const t0 = Date.now()
  await page.goto(BASE + route, { waitUntil: 'load' })
  const nav = await page.evaluate(() => { const n = performance.getEntriesByType('navigation')[0]; return { dcl: Math.round(n.domContentLoadedEventEnd), load: Math.round(n.loadEventEnd) } })
  await page.waitForSelector('h1', { timeout: 20000 }); const h1 = Date.now() - t0
  let canvas = null
  try { await page.waitForSelector('canvas', { timeout: 8000 }); canvas = Date.now() - t0 } catch { /* no 3D on this route until data exists */ }
  console.log(`${route.padEnd(11)} DOMContentLoaded ${nav.dcl} ms, load ${nav.load} ms, headline ${h1} ms, first canvas ${canvas ?? 'none'} ms, transferred ${kb(bytes)} (${kb(js)} JS)`)
  await page.close()
}
await browser.close()
