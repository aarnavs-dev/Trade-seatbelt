import { AnimatePresence, m } from 'framer-motion'
import { useEffect, useMemo, useRef, useState } from 'react'

export interface Command { id: string; label: string; keys?: string; hint?: string; run: () => void }

export function Palette({ open, onClose, commands }: { open: boolean; onClose: () => void; commands: Command[] }) {
  const [q, setQ] = useState('')
  const [i, setI] = useState(0)
  const input = useRef<HTMLInputElement>(null)
  const list = useMemo(() => {
    const t = q.trim().toLowerCase()
    return t ? commands.filter((c) => (c.label + ' ' + (c.hint ?? '')).toLowerCase().includes(t)) : commands
  }, [q, commands])
  useEffect(() => { if (open) { setQ(''); setI(0); setTimeout(() => input.current?.focus(), 0) } }, [open])
  useEffect(() => setI(0), [q])
  const run = (c?: Command) => { if (!c) return; onClose(); c.run() }
  return (
    <AnimatePresence>
      {open && (
        <m.div key="palette" className="fixed inset-0 z-50 flex items-start justify-center bg-black/45 px-4 pt-[16vh]" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.12 }} onMouseDown={onClose}>
          <m.div role="dialog" aria-modal="true" aria-label="Command palette" initial={{ y: -6, opacity: 0 }} animate={{ y: 0, opacity: 1 }} exit={{ y: -4, opacity: 0 }} transition={{ duration: 0.14, ease: 'easeOut' }}
                 className="glass w-full max-w-[520px] overflow-hidden" onMouseDown={(e) => e.stopPropagation()}
                 onKeyDown={(e) => {
                   if (e.key === 'ArrowDown') { e.preventDefault(); setI((x) => Math.min(list.length - 1, x + 1)) }
                   else if (e.key === 'ArrowUp') { e.preventDefault(); setI((x) => Math.max(0, x - 1)) }
                   else if (e.key === 'Enter') { e.preventDefault(); run(list[i]) }
                   else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); onClose() }
                 }}>
            <input ref={input} autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Type a command" aria-label="Command" role="combobox" aria-expanded="true" aria-controls="cmd-list"
                   className="w-full border-b border-line bg-transparent px-4 py-3.5 text-[15px] text-ink outline-none placeholder:text-ink-3" />
            <ul id="cmd-list" role="listbox" className="max-h-[50vh] overflow-y-auto p-1.5">
              {list.length === 0 && <li className="px-3 py-3 text-[13px] text-ink-3">No matching command</li>}
              {list.map((c, n) => (
                <li key={c.id} role="option" aria-selected={n === i} onMouseMove={() => setI(n)} onClick={() => run(c)}
                    className={`flex cursor-pointer items-center justify-between rounded-lg px-3 py-2.5 text-[14px] ${n === i ? 'bg-white/[0.07] text-ink' : 'text-ink-2'}`}>
                  <span>{c.label}{c.hint && <span className="ml-2 text-[12px] text-ink-3">{c.hint}</span>}</span>
                  {c.keys && <kbd className="kbd">{c.keys}</kbd>}
                </li>
              ))}
            </ul>
          </m.div>
        </m.div>
      )}
    </AnimatePresence>
  )
}
