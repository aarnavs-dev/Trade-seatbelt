import { useEffect, useRef, useState } from 'react'
import { Canvas, useFrame, useThree } from '@react-three/fiber'
import { PerformanceMonitor } from '@react-three/drei'
import * as THREE from 'three'
import { store, TRAVEL_MS, SWEEP_MS, type Block } from '../lib/store'
import { shortHash } from '../lib/format'
import { COLORS } from './theme'

// ---- layout constants (world units, chain frame: +x runs away from the gate, along the tunnel axis)
const MAXV = 200 // visible blocks
const SP = 2.1 // block spacing
const BW = 1.5, BH = 1.05, BD = 0.85
const X0 = 2.3 // x of the newest slot, just past the gate ring
const XF = 3.7 // x where a focused (selected) block is brought to
const START_X = -30
const RING_R = 3.0
const SLOT_VERTS = 26 // 12 box edges (24) + 1 link (2)
const MARKER_SLABS = 3
const CRACK_SEGS = 9
const ROT = 0.34 // radians the whole world is turned about Y so the axis runs into the distance
const NLABELS = 14

const cAccept = new THREE.Color(COLORS.accept), cReject = new THREE.Color(COLORS.reject), cGray = new THREE.Color(COLORS.gray)
const cDim = new THREE.Color(COLORS.grayDim), cWhite = new THREE.Color(COLORS.white)
const cBodyA = new THREE.Color(0x0a1a22), cBodyR = new THREE.Color(0x24130a), cBodyX = new THREE.Color(0x131518)

const smooth = (x: number) => (x <= 0 ? 0 : x >= 1 ? 1 : x * x * (3 - 2 * x))
const easeOut = (x: number) => 1 - Math.pow(1 - Math.min(1, Math.max(0, x)), 3)
const lane = (sym: string) => { let h = 0; for (let i = 0; i < sym.length; i++) h = (h * 31 + sym.charCodeAt(i)) % 7; return (h % 3) - 1 }

// unit-box edge template (12 edges)
const E = (() => {
  const c: [number, number, number][] = []
  for (const x of [-1, 1]) for (const y of [-1, 1]) for (const z of [-1, 1]) c.push([x * BW / 2, y * BH / 2, z * BD / 2])
  const pairs: [number, number][] = []
  for (let i = 0; i < 8; i++) for (let j = i + 1; j < 8; j++) {
    const d = (i ^ j)
    if (d === 1 || d === 2 || d === 4) pairs.push([i, j])
  }
  return { c, pairs }
})()

function glowTexture() {
  const s = 64, cv = document.createElement('canvas')
  cv.width = cv.height = s
  const g = cv.getContext('2d')!.createRadialGradient(s / 2, s / 2, 0, s / 2, s / 2, s / 2)
  g.addColorStop(0, 'rgba(255,255,255,1)'); g.addColorStop(0.35, 'rgba(255,255,255,0.28)'); g.addColorStop(1, 'rgba(255,255,255,0)')
  const ctx = cv.getContext('2d')!
  ctx.fillStyle = g; ctx.fillRect(0, 0, s, s)
  const t = new THREE.CanvasTexture(cv)
  t.colorSpace = THREE.SRGBColorSpace
  return t
}

interface Fx { accept: number; reject: number; rejectAt: { t: number; text: string }[] }

// =====================================================================================================
function World({ host, variant }: { host: React.RefObject<HTMLDivElement | null>; variant: 'full' | 'hero' }) {
  const { camera, size } = useThree()
  const group = useRef<THREE.Group>(null!)
  const body = useRef<THREE.InstancedMesh>(null!)
  const halo = useRef<THREE.InstancedMesh>(null!)
  const lines = useRef<THREE.LineSegments>(null!)
  const orders = useRef<THREE.InstancedMesh>(null!)
  const ringMat = useRef<THREE.MeshBasicMaterial>(null!)
  const ringHalo = useRef<THREE.MeshBasicMaterial>(null!)
  const ticks = useRef<THREE.LineSegments>(null!)
  const tex = useRef<THREE.Texture>(null!)
  if (!tex.current) tex.current = glowTexture()

  const st = useRef({
    cur: 0, dirtyUntil: 0, lastNow: 0, slice: [] as Block[], sliceStart: 0, hoverSeq: null as number | null,
    invalid: new Map<number, number>(), crack: 0, crackSeq: -1, fx: { accept: 0, reject: 0, rejectAt: [] } as Fx,
    snapped: false, lastVersion: -1, lastConn: '',
  })

  // ---- preallocated geometry buffers
  const lineData = useRef(() => {
    const verts = MAXV * SLOT_VERTS + MARKER_SLABS * 24 + CRACK_SEGS * 2
    return { pos: new Float32Array(verts * 3), col: new Float32Array(verts * 3) }
  })
  const ld = useRef<ReturnType<typeof lineData.current>>(null!)
  if (!ld.current) ld.current = lineData.current()

  // ---- DOM labels
  const labels = useRef<{ chain: HTMLDivElement[]; marker: HTMLDivElement | null; reject: HTMLDivElement[] }>({ chain: [], marker: null, reject: [] })
  useEffect(() => {
    const h = host.current
    if (!h) return
    const mk = (cls: string) => { const d = document.createElement('div'); d.className = cls; d.style.opacity = '0'; h.appendChild(d); return d }
    labels.current = {
      chain: Array.from({ length: NLABELS }, () => mk('lbl')),
      marker: mk('lbl lbl-marker'),
      reject: Array.from({ length: 4 }, () => mk('lbl lbl-reject')),
    }
    return () => { [...labels.current.chain, labels.current.marker!, ...labels.current.reject].forEach((d) => d?.remove()) }
  }, [host])

  // wheel scrubs back through the chain
  useEffect(() => {
    const el = host.current?.parentElement
    if (!el || variant === 'hero') return // the landing page must scroll
    const onWheel = (e: WheelEvent) => store.scrub(-Math.sign(e.deltaY) * Math.max(1, Math.round(Math.abs(e.deltaY) / 40)))
    el.addEventListener('wheel', onWheel, { passive: true })
    return () => el.removeEventListener('wheel', onWheel)
  }, [host, variant])

  // order particles
  const tmpM = useRef(new THREE.Matrix4()).current
  const tmpV = useRef(new THREE.Vector3()).current
  const tmpC = useRef(new THREE.Color()).current
  const axisZ = useRef(new THREE.Vector3(0, 0, 1)).current
  const tmpQ = useRef(new THREE.Quaternion()).current
  const tmpS = useRef(new THREE.Vector3()).current

  useFrame((state, dtRaw) => {
    const dt = Math.min(dtRaw, 0.05)
    const now = performance.now()
    const s = st.current
    const snap = store.getSnap()
    const blocks = store.blocks
    if (snap.version !== s.lastVersion) { s.lastVersion = snap.version; s.dirtyUntil = now + 2500 }

    // ---- which blocks are on stage
    let headIdx = blocks.length - 1
    while (headIdx >= 0 && blocks[headIdx].spawnAt + TRAVEL_MS > now) headIdx--
    const arrivedHead = headIdx >= 0 ? blocks[headIdx].ev.seq : (blocks[0]?.ev.seq ?? 1) - 1
    const focus = !snap.followLive ? (snap.viewSeq ?? snap.selected) : null
    const first = blocks[0]?.ev.seq ?? 1
    let start = Math.max(0, blocks.length - MAXV)
    if (focus !== null) start = Math.max(0, Math.min(blocks.length - MAXV, focus - first - MAXV / 2))
    const end = Math.min(blocks.length, start + MAXV)
    const slice = blocks.slice(start, end)
    if (s.sliceStart !== start || s.slice.length !== slice.length) s.dirtyUntil = Math.max(s.dirtyUntil, now + 400)
    s.slice = slice; s.sliceStart = start

    // ---- conveyor: slide the chain so the newest arrived block sits at the gate (or the focused block mid-stage)
    const target = focus !== null ? focus + (XF - X0) / SP : arrivedHead
    let diff = target - s.cur
    if (!s.snapped && blocks.length) { s.cur = target; diff = 0; s.snapped = true }
    if (Math.abs(diff) > 14) s.cur = target - Math.sign(diff) * Math.min(14, Math.abs(diff))
    const k = 1 - Math.exp(-dt * 6.5)
    const before = s.cur
    s.cur += (target - s.cur) * k
    if (Math.abs(s.cur - before) > 1e-4) s.dirtyUntil = Math.max(s.dirtyUntil, now + 120)
    group.current.position.x = X0 + s.cur * SP
    if (snap.conn !== s.lastConn) { s.lastConn = snap.conn; if (snap.conn === 'connecting') s.snapped = false }

    // ---- order particles + gate fx
    const fx = s.fx
    const gateX = 0
    let np = 0
    for (let i = blocks.length - 1; i >= 0 && i >= blocks.length - 40; i--) {
      const b = blocks[i]
      if (b.spawnAt === -Infinity) break
      const t = (now - b.spawnAt) / TRAVEL_MS
      const pt = (s.lastNow - b.spawnAt) / TRAVEL_MS
      const acc = b.ev.decision === 'ACCEPT'
      const endX = acc ? X0 : -0.55
      if (t < 0) continue
      // crossing the ring
      const crossT = acc ? (gateX - START_X) / (X0 - START_X) : 0.97
      if (pt < crossT && t >= crossT && s.lastNow) {
        if (acc) fx.accept = 1
        else { fx.reject = 1; fx.rejectAt.push({ t: now, text: b.ev.reason_code }); if (fx.rejectAt.length > 4) fx.rejectAt.shift() }
      }
      if (t > 1 + (acc ? 0.15 : 0.8) || np >= 48) continue
      const l = lane(b.ev.symbol) * 0.55
      let x: number, y: number, z = b.ev.side === 'BUY' ? 0.25 : -0.25, sc = 1
      if (t <= 1) {
        const e = easeOut(t * 0.92 + 0.08 * t)
        x = START_X + (endX - START_X) * e
        y = l * (1 - smooth(e))
        if (acc) sc = 1
        else y = l * (1 - smooth(e) * 0.4)
      } else if (acc) { x = X0; y = 0; sc = Math.max(0, 1 - (t - 1) / 0.15) } else {
        const u = (t - 1) / 0.8
        const v = 1 - Math.pow(1 - Math.min(1, u), 2)
        x = endX - 0.9 * v; y = l * 0.6 + 2.2 * v; z += 2.4 * v * (b.ev.side === 'BUY' ? 1 : -1); sc = Math.max(0, 1 - u)
      }
      tmpV.set(x, y, z); tmpS.setScalar(0.17 * sc * (variant === 'hero' ? 0.8 : 1)); tmpQ.identity()
      tmpM.compose(tmpV, tmpQ, tmpS)
      orders.current.setMatrixAt(np, tmpM)
      orders.current.setColorAt(np, acc ? cAccept : cReject)
      np++
    }
    orders.current.count = np
    orders.current.instanceMatrix.needsUpdate = true
    if (orders.current.instanceColor) orders.current.instanceColor.needsUpdate = true
    s.lastNow = now

    fx.accept *= Math.exp(-dt * 3.2); fx.reject *= Math.exp(-dt * 2.6)
    ringMat.current.color.copy(cGray).lerp(cAccept, Math.min(1, fx.accept)).lerp(cReject, Math.min(1, fx.reject))
    ringHalo.current.opacity = 0.05 + 0.5 * Math.max(fx.accept * 0.7, fx.reject)
    ringHalo.current.color.copy(fx.reject > fx.accept ? cReject : cAccept)
    ticks.current.rotation.x += dt * 0.12
    if (fx.accept > 0.02 || fx.reject > 0.02 || np) s.dirtyUntil = Math.max(s.dirtyUntil, now + 80)

    // ---- tamper / reset / verify state per frame
    const tam = snap.tamper
    const badSeq = tam ? tam.result.first_bad_seq : snap.report && !snap.report.chain.ok ? snap.report.chain.first_bad_seq : null
    const tamSeq = tam ? tam.result.tampered_seq : -1
    s.crack += ((tam ? 1 : 0) - s.crack) * (1 - Math.exp(-dt * (tam ? 4 : 8)))
    const nBad = badSeq === null ? 0 : slice.filter((b) => b.ev.seq > badSeq).length
    const wave = Math.min(70, 1400 / Math.max(1, nBad))
    const v = snap.verify
    let sweepSeq = -1, sweepEnd = Infinity, sweepFlash = 0
    if (v) {
      // the sweep plays over the blocks on screen that end at the verdict (the head, or the first bad seq);
      // the verdict itself (N events verified) comes from the server's check of the whole log
      const hi = slice[slice.length - 1]?.ev.seq ?? 1
      sweepEnd = v.badSeq ?? hi
      const lo = Math.max(slice[0]?.ev.seq ?? 1, sweepEnd - (v.badSeq !== null ? 8 : 13))
      const p = v.running || v.outcome === null ? Math.min(1, (now - v.startedAt) / SWEEP_MS) : 1
      sweepSeq = lo + (sweepEnd - lo) * smooth(p)
      sweepFlash = v.running ? 1 : Math.max(0, 1 - (now - v.startedAt - SWEEP_MS) / 900)
      if (sweepFlash > 0.01) s.dirtyUntil = Math.max(s.dirtyUntil, now + 100)
    }
    if (tam || s.crack > 0.01 || s.invalid.size) s.dirtyUntil = Math.max(s.dirtyUntil, now + 200)
    if (snap.hover !== s.hoverSeq) { s.hoverSeq = snap.hover; s.dirtyUntil = now + 150 }

    // ---- rewrite instance + line buffers while anything is animating
    if (now < s.dirtyUntil || slice.some((b) => b.spawnAt !== -Infinity && now - b.spawnAt < TRAVEL_MS + 900)) {
      const { pos, col } = ld.current
      let vi = 0
      const put = (x: number, y: number, z: number, c: THREE.Color, a: number) => {
        pos[vi * 3] = x; pos[vi * 3 + 1] = y; pos[vi * 3 + 2] = z
        col[vi * 3] = c.r * a; col[vi * 3 + 1] = c.g * a; col[vi * 3 + 2] = c.b * a
        vi++
      }
      const n = slice.length
      for (let i = 0; i < n; i++) {
        const b = slice[i], ev = b.ev, acc = ev.decision === 'ACCEPT'
        const arriveAt = b.spawnAt + TRAVEL_MS
        const born = b.spawnAt === -Infinity ? 1 : (now - arriveAt) / 700
        const shown = born >= 0
        // invalid factor eases toward its target; the wave is delayed per block after the tampered one
        const wantBad = badSeq !== null && ev.seq >= badSeq && (!tam || now >= tam.at + 650 + (ev.seq - badSeq) * wave)
        const cur = s.invalid.get(ev.seq) ?? 0
        const inv = cur + ((wantBad ? 1 : 0) - cur) * (1 - Math.exp(-dt * (wantBad ? 10 : 7)))
        if (inv < 0.005 && !wantBad) s.invalid.delete(ev.seq); else s.invalid.set(ev.seq, inv)
        const lx = -ev.seq * SP
        let sc = shown ? 0.88 + 0.12 * smooth(born) : 0
        let rotZ = 0
        if (ev.seq === tamSeq) { sc *= 1 + 0.04 * s.crack; rotZ = 0.07 * s.crack }
        // colour
        const base = acc ? cAccept : cReject
        tmpC.copy(base).lerp(cGray, inv * 0.85)
        let bright = acc ? 0.72 : 0.55
        if (shown && born < 1) bright += (1 - born) * 0.9
        const isSel = snap.selected === ev.seq, isHover = s.hoverSeq === ev.seq
        if (isSel || isHover) { tmpC.lerp(cWhite, isSel ? 0.55 : 0.35); bright = Math.max(bright, 1) }
        if (sweepSeq >= 0 && v) {
          const d = ev.seq - sweepSeq
          if (ev.seq <= sweepEnd && d <= 0.5) { // swept: a brief bright tick that decays behind the light
            const trail = Math.exp(Math.max(d, -12) * 0.35)
            tmpC.lerp(cAccept, trail * 0.6); bright += trail * 0.9 * (v.running ? 1 : sweepFlash)
          }
          if (v.badSeq !== null && ev.seq === v.badSeq && sweepSeq >= v.badSeq - 0.5) { tmpC.copy(cReject); bright += 0.9 * sweepFlash }
        }
        if (ev.seq === tamSeq) { tmpC.lerp(cReject, s.crack); bright += s.crack * 0.5 }
        // blocks newer than the focused one swing toward the camera: fade them out instead of letting them loom
        const vis = focus === null ? 1 : Math.min(1, Math.max(0, 1 - (ev.seq - focus - 1) / 2.2))
        const a = shown ? bright * vis : 0
        // box edges
        const cx = lx, cy = 0
        for (const [p, q] of E.pairs) {
          for (const idx of [p, q]) {
            const c = E.c[idx]
            const x = c[0] * sc, y = c[1] * sc, z = c[2] * sc
            put(cx + x * Math.cos(rotZ) - y * Math.sin(rotZ), cy + x * Math.sin(rotZ) + y * Math.cos(rotZ), z, tmpC, a)
          }
        }
        // link toward the previous (older) block = the next one in +x
        const linkSwept = sweepSeq >= 0 && ev.seq <= sweepSeq && ev.seq <= sweepEnd
        const linkC = tmpC.copy(cDim).lerp(cAccept, linkSwept ? 0.5 : 0.12).lerp(cReject, inv * 0.9)
        put(cx + BW / 2 * sc, 0, 0, linkC, shown ? (inv > 0.3 ? 0.9 : 1.1) * vis : 0)
        put(cx + (SP - BW / 2), 0, 0, linkC, shown ? (inv > 0.3 ? 0.9 : 1.1) * vis : 0)
        // instanced body + halo
        tmpV.set(cx, cy, 0); tmpQ.setFromAxisAngle(axisZ, rotZ); tmpS.setScalar(Math.max(sc, 1e-4))
        tmpM.compose(tmpV, tmpQ, tmpS)
        body.current.setMatrixAt(i, tmpM)
        tmpC.copy(acc ? cBodyA : cBodyR).lerp(cBodyX, inv).multiplyScalar(vis)
        body.current.setColorAt(i, tmpC)
        tmpS.setScalar(shown ? 3.6 : 1e-4)
        tmpM.compose(tmpV, tmpQ.identity(), tmpS)
        halo.current.setMatrixAt(i, tmpM)
        tmpC.copy(base).lerp(cGray, inv).multiplyScalar(shown ? (0.06 + Math.max(0, bright - 0.72) * 0.22 + (isSel || isHover ? 0.12 : 0)) * (1 - inv * 0.7) * vis : 0)
        halo.current.setColorAt(i, tmpC)
      }
      body.current.count = halo.current.count = n
      // marker slabs for the blocks that are no longer drawn
      const earlier = (slice[0]?.ev.seq ?? 1) - 1
      const mx = -((slice[0]?.ev.seq ?? 1) - 1) * SP - 0.2
      for (let m = 0; m < MARKER_SLABS; m++) {
        const show = earlier > 0
        const w = 0.14, off = m * 0.34
        for (const [p, q] of E.pairs) for (const idx of [p, q]) {
          const c = E.c[idx]
          put(mx + (c[0] / (BW / 2)) * w / 2 + off, c[1] * (show ? 1 : 0), c[2] * (show ? 1 : 0), cGray, show ? 0.55 - m * 0.12 : 0)
        }
      }
      // crack across the tampered block's front face
      const ti = slice.findIndex((b) => b.ev.seq === tamSeq)
      const cx0 = ti >= 0 ? -tamSeq * SP : 0
      const pts: [number, number][] = [[-0.62, 0.52], [-0.28, 0.2], [-0.42, -0.02], [0.02, -0.18], [-0.1, -0.34], [0.24, -0.52], [0.12, 0.08], [0.46, 0.3], [0.62, 0.12]]
      const segs: [number, number][] = [[0, 1], [1, 2], [2, 3], [3, 4], [4, 5], [3, 6], [6, 7], [7, 8], [1, 6]]
      for (let sgi = 0; sgi < CRACK_SEGS; sgi++) {
        const [a0, b0] = segs[sgi]
        const show = ti >= 0 && s.crack * CRACK_SEGS > sgi
        const f = show ? Math.min(1, s.crack * CRACK_SEGS - sgi) : 0
        const P = pts[a0], Q = pts[b0]
        const zf = BD / 2 + 0.01
        put(cx0 + P[0], P[1], show ? zf : 0, cReject, show ? 1.4 : 0)
        put(cx0 + P[0] + (Q[0] - P[0]) * f, P[1] + (Q[1] - P[1]) * f, show ? zf : 0, cReject, show ? 1.4 : 0)
      }
      const g = lines.current.geometry
      g.setDrawRange(0, vi)
      ;(g.attributes.position as THREE.BufferAttribute).needsUpdate = true
      ;(g.attributes.color as THREE.BufferAttribute).needsUpdate = true
      body.current.instanceMatrix.needsUpdate = halo.current.instanceMatrix.needsUpdate = true
      if (body.current.instanceColor) body.current.instanceColor.needsUpdate = true
      if (halo.current.instanceColor) halo.current.instanceColor.needsUpdate = true
    }

    // ---- camera: gentle parallax from the pointer, nothing that bounces
    const px = state.pointer.x, py = state.pointer.y
    const hero = variant === 'hero'
    const cam = camera as THREE.PerspectiveCamera
    const aspect = size.width / size.height
    const wide = Math.min(1.25, Math.max(0.8, 1.6 / aspect))
    // the landing headline owns the left: shift position and target together so the view direction is unchanged
    const sh = hero ? 1.75 * aspect : 0
    tmpV.set(-4.5 + px * 0.35 - 0.96 * sh, 2.7 + py * 0.2, 16 * wide - 0.28 * sh)
    cam.position.lerp(tmpV, 1 - Math.exp(-dt * 3))
    cam.lookAt(0.9 - 0.96 * sh, -0.2, -2.2 - 0.28 * sh)

    // ---- DOM labels (pool, no React renders)
    const L = labels.current
    if (!L.chain.length) return
    const wm = group.current.matrixWorld
    const cands: { i: number; d: number; x: number; y: number }[] = []
    const w = size.width, h = size.height
    for (let i = 0; i < slice.length; i++) {
      const b = slice[i]
      if (b.spawnAt + TRAVEL_MS > now || (focus !== null && b.ev.seq - focus > 2)) continue
      tmpV.set(-b.ev.seq * SP, -BH / 2 - 0.34, 0).applyMatrix4(wm)
      const d = tmpV.distanceTo(cam.position)
      tmpV.project(cam)
      if (tmpV.z > 1 || tmpV.x < -1.05 || tmpV.x > 1.05 || tmpV.y < -1.05 || tmpV.y > 1.05) continue
      cands.push({ i, d, x: (tmpV.x * 0.5 + 0.5) * w, y: (-tmpV.y * 0.5 + 0.5) * h })
    }
    cands.sort((a, b) => a.d - b.d)
    for (let li = 0; li < L.chain.length; li++) {
      const el = L.chain[li], c = cands[li]
      if (!c || variant === 'hero' && li > 7) { el.style.opacity = '0'; continue }
      const ev = slice[c.i].ev
      const bad = (s.invalid.get(ev.seq) ?? 0) > 0.5
      el.innerHTML = `<b>${ev.seq}</b> ${shortHash(ev.hash)}`
      el.dataset.bad = bad ? '1' : '0'
      el.style.opacity = String(Math.max(0, Math.min(0.95, 1.5 - c.d / 22)))
      el.style.transform = `translate3d(${c.x.toFixed(1)}px,${c.y.toFixed(1)}px,0) translateX(-50%)`
    }
    if (L.marker) {
      const earlier = (slice[0]?.ev.seq ?? 1) - 1
      if (earlier > 0 && variant === 'full') {
        tmpV.set(-earlier * SP - 0.2 + 0.4, BH / 2 + 0.5, 0).applyMatrix4(wm).project(cam)
        const onScreen = tmpV.z < 1 && Math.abs(tmpV.x) < 1.1 && Math.abs(tmpV.y) < 1.1
        L.marker.textContent = `${earlier.toLocaleString('en-US')} earlier block${earlier === 1 ? '' : 's'}`
        L.marker.style.opacity = onScreen ? '0.8' : '0'
        L.marker.style.transform = `translate3d(${((tmpV.x * 0.5 + 0.5) * w).toFixed(1)}px,${((-tmpV.y * 0.5 + 0.5) * h).toFixed(1)}px,0) translateX(-50%)`
      } else L.marker.style.opacity = '0'
    }
    for (let ri = 0; ri < L.reject.length; ri++) {
      const el = L.reject[ri], r = fx.rejectAt[fx.rejectAt.length - 1 - ri]
      const age = r ? (now - r.t) / 1700 : 2
      if (!r || age >= 1) { el.style.opacity = '0'; continue }
      tmpV.set(-0.4, 1.6 + age * 1.1, 1.6).applyMatrix4(wm).project(cam)
      el.textContent = r.text
      el.style.opacity = String(Math.max(0, 1 - age * age))
      el.style.transform = `translate3d(${((tmpV.x * 0.5 + 0.5) * w).toFixed(1)}px,${((-tmpV.y * 0.5 + 0.5) * h).toFixed(1)}px,0) translate(-50%,-50%)`
    }
  })

  // ---- static geometry for the tunnel
  const tunnel = useRef<THREE.BufferGeometry>(null!)
  useEffect(() => {
    const p: number[] = []
    const rails = 10
    for (let r = 0; r < rails; r++) {
      const a = (r / rails) * Math.PI * 2
      p.push(START_X - 14, Math.sin(a) * RING_R, Math.cos(a) * RING_R, 0, Math.sin(a) * RING_R, Math.cos(a) * RING_R)
    }
    for (let x = -3.5; x > START_X - 14; x -= 3.5) {
      const segs = 72
      for (let i = 0; i < segs; i++) {
        const a0 = (i / segs) * Math.PI * 2, a1 = ((i + 1) / segs) * Math.PI * 2
        p.push(x, Math.sin(a0) * RING_R, Math.cos(a0) * RING_R, x, Math.sin(a1) * RING_R, Math.cos(a1) * RING_R)
      }
    }
    tunnel.current.setAttribute('position', new THREE.Float32BufferAttribute(p, 3))
  }, [])
  const tickGeo = useRef<THREE.BufferGeometry>(null!)
  useEffect(() => {
    const p: number[] = []
    const n = 96
    for (let i = 0; i < n; i++) {
      const a = (i / n) * Math.PI * 2, r0 = RING_R + 0.18, r1 = RING_R + (i % 8 === 0 ? 0.52 : 0.32)
      p.push(0, Math.sin(a) * r0, Math.cos(a) * r0, 0, Math.sin(a) * r1, Math.cos(a) * r1)
    }
    tickGeo.current.setAttribute('position', new THREE.Float32BufferAttribute(p, 3))
  }, [])

  const bodyGeo = useRef(new THREE.BoxGeometry(BW, BH, BD)).current
  const haloGeo = useRef(new THREE.PlaneGeometry(1, 1)).current
  const orderGeo = useRef(new THREE.OctahedronGeometry(1, 0)).current

  return (
    <group rotation-y={ROT}>
      {/* tunnel */}
      <lineSegments frustumCulled={false}>
        <bufferGeometry ref={tunnel} />
        <lineBasicMaterial color={COLORS.gray} transparent opacity={0.34} />
      </lineSegments>
      {/* gate ring: plane normal along the tunnel axis */}
      <mesh rotation-y={Math.PI / 2}>
        <torusGeometry args={[RING_R, 0.022, 8, 160]} />
        <meshBasicMaterial ref={ringMat} color={COLORS.gray} toneMapped={false} />
      </mesh>
      <mesh rotation-y={Math.PI / 2}>
        <torusGeometry args={[RING_R - 0.28, 0.008, 6, 160]} />
        <meshBasicMaterial color={COLORS.grayDim} />
      </mesh>
      <lineSegments ref={ticks} frustumCulled={false}>
        <bufferGeometry ref={tickGeo} />
        <lineBasicMaterial color={COLORS.gray} transparent opacity={0.6} />
      </lineSegments>
      <mesh rotation-y={Math.PI / 2} position-x={-0.02}>
        <planeGeometry args={[RING_R * 2.6, RING_R * 2.6]} />
        <meshBasicMaterial ref={ringHalo} map={tex.current} transparent opacity={0.05} blending={THREE.AdditiveBlending} depthWrite={false} color={COLORS.accept} />
      </mesh>

      {/* order particles live in the tunnel frame */}
      <instancedMesh ref={orders} args={[orderGeo, undefined, 48]} frustumCulled={false}>
        <meshBasicMaterial toneMapped={false} />
      </instancedMesh>

      {/* the chain */}
      <group ref={group}>
        <lineSegments ref={lines} frustumCulled={false}>
          <bufferGeometry>
            <bufferAttribute attach="attributes-position" args={[ld.current.pos, 3]} />
            <bufferAttribute attach="attributes-color" args={[ld.current.col, 3]} />
          </bufferGeometry>
          <lineBasicMaterial vertexColors toneMapped={false} />
        </lineSegments>
        <instancedMesh
          ref={body}
          args={[bodyGeo, undefined, MAXV]}
          frustumCulled={false}
          onPointerMove={(e) => { e.stopPropagation(); const b = st.current.slice[e.instanceId ?? -1]; store.hover(b ? b.ev.seq : null); document.body.style.cursor = b ? 'pointer' : '' }}
          onPointerOut={() => { store.hover(null); document.body.style.cursor = '' }}
          onClick={(e) => { e.stopPropagation(); const b = st.current.slice[e.instanceId ?? -1]; if (b) store.select(store.getSnap().selected === b.ev.seq ? null : b.ev.seq) }}
        >
          <meshBasicMaterial transparent opacity={0.62} depthWrite={false} />
        </instancedMesh>
        <instancedMesh ref={halo} args={[haloGeo, undefined, MAXV]} frustumCulled={false} renderOrder={-1}>
          <meshBasicMaterial map={tex.current} transparent blending={THREE.AdditiveBlending} depthWrite={false} toneMapped={false} />
        </instancedMesh>
      </group>
    </group>
  )
}

// =====================================================================================================
export default function Scene({ variant = 'full' }: { variant?: 'full' | 'hero' }) {
  const host = useRef<HTMLDivElement>(null)
  const [hidden, setHidden] = useState(document.hidden)
  const [dpr, setDpr] = useState(Math.min(2, window.devicePixelRatio || 1))
  useEffect(() => {
    const on = () => setHidden(document.hidden)
    document.addEventListener('visibilitychange', on)
    return () => document.removeEventListener('visibilitychange', on)
  }, [])
  return (
    <div className={`absolute inset-0 ${variant === 'hero' ? 'pointer-events-none' : ''}`} data-testid="scene">
      <Canvas
        dpr={dpr}
        frameloop={hidden ? 'never' : 'always'}
        camera={{ fov: 36, near: 0.1, far: 140, position: [0.4, 2, 10] }}
        gl={{ antialias: true, powerPreference: 'high-performance', alpha: false }}
        onCreated={({ gl, scene }) => {
          gl.setClearColor(COLORS.bg)
          scene.fog = new THREE.FogExp2(COLORS.bg, variant === 'hero' ? 0.028 : 0.034)
          scene.background = new THREE.Color(COLORS.bg)
        }}
      >
        <PerformanceMonitor onDecline={() => setDpr((d) => Math.max(1, d - 0.5))} flipflops={2} />
        <World host={host} variant={variant} />
      </Canvas>
      <div ref={host} className="pointer-events-none absolute inset-0 overflow-hidden" />
    </div>
  )
}
