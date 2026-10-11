import { Component, lazy, Suspense, useEffect, useState, type ReactNode } from 'react'
import { forced2D, hasWebGL, useReducedMotion, usePhone } from '../lib/env'
import type { ChainStore } from '../lib/store'
import type { Variant } from './Scene'
import Chain2D from './Chain2D'

// The three.js chunk loads only when a page mounts a 3D scene (and WebGL, motion and screen size allow it).
const Scene = lazy(() => import('./Scene'))

export const HEIGHTS: Record<Variant, number> = { hero: 440, medium: 420, strip: 220, mini: 120, line: 84 }

/** Whether 3D runs here: not on phones, with reduced motion, without WebGL, or with ?view=2d. `loading` until WebGL has been probed. */
export function useFlat() {
  const reduced = useReducedMotion()
  const phone = usePhone()
  const [gl, setGl] = useState<boolean | null>(null)
  useEffect(() => setGl(hasWebGL()), [])
  const why = forced2D() ? '2D view' : gl === false ? 'WebGL is unavailable, showing the 2D chain.' : reduced ? 'Reduced motion is on, showing the 2D chain.' : undefined
  return { flat: forced2D() || gl === false || reduced || phone, loading: gl === null, why }
}

class Boundary extends Component<{ children: ReactNode; fallback: (e: Error) => ReactNode }, { err: Error | null }> {
  state = { err: null as Error | null }
  static getDerivedStateFromError(err: Error) { return { err } }
  render() { return this.state.err ? this.props.fallback(this.state.err) : this.props.children }
}

/** One reusable chain scene in five sizes. `fallback` is the static 2D picture used when 3D cannot or should not run. */
export default function ChainScene({ store, variant, fallback, label }: { store: ChainStore; variant: Variant; fallback?: ReactNode; label: string }) {
  const { flat, loading, why } = useFlat()
  const picture = (note?: string) => fallback ?? <Chain2D store={store} compact={variant === 'mini' || variant === 'line'} count={variant === 'strip' ? 40 : undefined} notice={note} />
  return (
    <div className="scene-box" style={{ height: flat ? undefined : HEIGHTS[variant] }} role="img" aria-label={label} data-testid={`chainscene-${variant}`} data-mode={flat ? '2d' : '3d'}>
      {loading ? <p className="scene-msg" role="status">Loading the 3D view…</p>
        : flat ? picture(why)
        : (
          <Boundary fallback={(e) => picture(`The 3D view could not start (${e.message}). Showing the 2D chain.`)}>
            <Suspense fallback={<p className="scene-msg" role="status">Loading the 3D view…</p>}><Scene store={store} variant={variant} /></Suspense>
          </Boundary>
        )}
    </div>
  )
}
