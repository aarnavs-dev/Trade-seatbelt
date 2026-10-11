import { useEffect, useState } from 'react'

export function hasWebGL(): boolean {
  try {
    const c = document.createElement('canvas')
    return !!(c.getContext('webgl2') || c.getContext('webgl'))
  } catch {
    return false
  }
}

export function useMedia(q: string): boolean {
  const [m, setM] = useState(() => matchMedia(q).matches)
  useEffect(() => {
    const mq = matchMedia(q)
    const on = () => setM(mq.matches)
    mq.addEventListener('change', on)
    on()
    return () => mq.removeEventListener('change', on)
  }, [q])
  return m
}

export const usePhone = () => useMedia('(max-width: 767px)')
export const useReducedMotion = () => useMedia('(prefers-reduced-motion: reduce)')

/** ?view=2d forces the SVG chain (used for tests and as an escape hatch). */
export const forced2D = () => new URLSearchParams(location.search).get('view') === '2d'

export function useAsync<T>(fn: () => Promise<T>, deps: unknown[] = []) {
  const [state, setState] = useState<{ data: T | null; error: Error | null; loading: boolean }>({ data: null, error: null, loading: true })
  const [n, setN] = useState(0)
  useEffect(() => {
    let live = true
    setState((s) => ({ ...s, loading: true, error: null }))
    fn().then(
      (data) => live && setState({ data, error: null, loading: false }),
      (error) => live && setState({ data: null, error, loading: false }),
    )
    return () => { live = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, n])
  return { ...state, reload: () => setN((x) => x + 1), set: (data: T) => setState({ data, error: null, loading: false }) }
}
