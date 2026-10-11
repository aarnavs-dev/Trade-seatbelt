import { Component, type ReactNode } from 'react'

export class SceneBoundary extends Component<{ children: ReactNode; fallback: (e: Error) => ReactNode }, { err: Error | null }> {
  state = { err: null as Error | null }
  static getDerivedStateFromError(err: Error) { return { err } }
  render() { return this.state.err ? this.props.fallback(this.state.err) : this.props.children }
}

export function SimulatedLabel() {
  return (
    <span className="num inline-flex items-center gap-2 rounded-full border border-line px-2.5 py-1 text-[11px] tracking-wide text-ink-2" data-testid="sim-label">
      <i className="inline-block h-1.5 w-1.5 rounded-full bg-ink-3" />SIMULATED, no real orders
    </span>
  )
}

export function Mark() {
  return (
    <svg width="22" height="22" viewBox="0 0 22 22" aria-hidden>
      <circle cx="11" cy="11" r="8.5" fill="none" stroke="var(--color-accept)" strokeWidth="1.4" />
      <circle cx="11" cy="11" r="4.2" fill="none" stroke="var(--color-ink-3)" strokeWidth="1" />
    </svg>
  )
}

export function StageLoading({ text }: { text: string }) {
  return <div className="absolute inset-0 grid place-items-center text-[13px] text-ink-3" role="status"><span className="flex items-center gap-3"><i className="live-dot inline-block h-2 w-2 rounded-full bg-accept" />{text}</span></div>
}
