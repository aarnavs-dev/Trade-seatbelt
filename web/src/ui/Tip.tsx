import type { ReactNode } from 'react'

/** Hover/focus tooltip that always shows the keyboard shortcut. */
export function Tip({ label, keys, children, side = 'bottom' }: { label: string; keys?: string[]; children: ReactNode; side?: 'bottom' | 'top' }) {
  return (
    <span className="group/tip relative inline-flex">
      {children}
      <span
        role="tooltip"
        className={`pointer-events-none absolute left-1/2 z-50 flex -translate-x-1/2 items-center gap-2 whitespace-nowrap rounded-lg border border-line bg-[#0d0f12] px-2.5 py-1.5 text-xs text-ink-2 opacity-0 transition-opacity duration-150 group-hover/tip:opacity-100 group-hover/tip:delay-300 group-has-[:focus-visible]/tip:opacity-100 max-md:hidden ${side === 'bottom' ? 'top-full mt-2' : 'bottom-full mb-2'}`}
      >
        {label}
        {keys?.map((k) => <kbd key={k} className="kbd">{k}</kbd>)}
      </span>
    </span>
  )
}
