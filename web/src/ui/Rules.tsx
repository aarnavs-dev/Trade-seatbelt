import { useEffect, useState } from 'react'
import { ApiError } from '../lib/api'
import { formFrom, PRESET_NAMES, rules, sentence, type Form, type RulesState } from '../lib/rules'
import { Page } from './kit'

type Load = { kind: 'loading' } | { kind: 'error'; message: string } | { kind: 'ready'; state: RulesState }

const FIELDS: { key: keyof Form; name: string; unit: string; help: string; mode: 'numeric' | 'decimal' }[] = [
  { key: 'maxPosition', name: 'Biggest position in one stock', unit: 'shares', mode: 'numeric', help: 'Protects against a position larger than you are willing to hold.' },
  { key: 'maxLossDollars', name: 'Most you can lose in a day', unit: 'dollars', mode: 'decimal', help: 'Protects against adding new risk after a bad day. Closing a position is always allowed.' },
  { key: 'bandPct', name: 'Price band', unit: 'percent from yesterday’s close', mode: 'numeric', help: 'Protects against typos and wild prices.' },
  { key: 'cooldownLosses', name: 'Pause after losing trades', unit: 'losses in a row', mode: 'numeric', help: 'Protects against revenge trading: after this many losses, new trades wait.' },
  { key: 'cooldownMinutes', name: 'How long the pause lasts', unit: 'minutes', mode: 'numeric', help: 'Closing a position is allowed during the pause.' },
]

const when = (iso: string) => new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'medium' })

export default function Rules() {
  const [load, setLoad] = useState<Load>({ kind: 'loading' })
  const [form, setForm] = useState<Form | null>(null)
  const [preset, setPreset] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [dirty, setDirty] = useState(false)

  const fetchRules = () => {
    setLoad({ kind: 'loading' })
    rules.get().then((s) => { setLoad({ kind: 'ready', state: s }); setForm(formFrom(s.limits)) },
                     (e) => setLoad({ kind: 'error', message: e instanceof Error ? e.message : String(e) }))
  }
  useEffect(fetchRules, [])

  const edit = (patch: Partial<Form>) => { setForm((f) => ({ ...f!, ...patch })); setPreset(null); setDirty(true); setError(null) }
  const pick = (key: string) => {
    if (load.kind !== 'ready') return
    setForm(formFrom(load.state.presets[key])); setPreset(key); setDirty(true); setError(null)
  }
  const save = async () => {
    if (!form) return
    setSaving(true); setError(null)
    try { const s = await rules.save(form); setLoad({ kind: 'ready', state: s }); setForm(formFrom(s.limits)); setDirty(false) }
    catch (e) { setError(e instanceof ApiError || e instanceof Error ? e.message : String(e)) }
    finally { setSaving(false) }
  }

  const state = load.kind === 'ready' ? load.state : null
  return (
    <Page title="Rules">
      <div className="container" style={{ padding: 'var(--s8) var(--gutter) var(--s12)' }}>
        <div className="grid12">
          <div className="c-1-7 stack-lg">
            <div className="stack">
              <h1 style={{ fontSize: 'clamp(36px, 4.4vw, 56px)', lineHeight: 1.08 }}>Set your rules</h1>
              <p className="muted prose">This is the first step. Every trade you copy has to pass these rules, and anything that does not is stopped and explained. They are yours: nothing here changes the project’s own settings.</p>
            </div>

            {load.kind === 'loading' && <p className="muted" role="status" data-testid="loading">Loading your rules…</p>}
            {load.kind === 'error' && <div role="alert" data-testid="load-error"><p className="error">{load.message}</p><button className="btn" style={{ marginTop: 'var(--s2)' }} onClick={fetchRules}>Try again</button></div>}

            {form && (
              <div>
                <div className="stack" style={{ marginBottom: 'var(--s3)' }}>
                  <p className="label">Start from</p>
                  <div className="choices" role="group" aria-label="Presets">
                    {PRESET_NAMES.map((p) => <button key={p.key} className="choice" aria-pressed={preset === p.key} onClick={() => pick(p.key)} data-testid={`preset-${p.key}`}>{p.label}</button>)}
                  </div>
                </div>
                {FIELDS.map((f) => (
                  <div className="field" key={f.key}>
                    <div><label className="name" htmlFor={`f-${f.key}`}>{f.name}</label><p className="help">{f.help}</p></div>
                    <div><input id={`f-${f.key}`} className="input" inputMode={f.mode} value={form[f.key]} onChange={(e) => edit({ [f.key]: e.target.value })} data-testid={`limit-${f.key}`} /><p className="unit">{f.unit}</p></div>
                  </div>
                ))}
                <p style={{ marginTop: 'var(--s3)', fontFamily: 'var(--font-serif)', fontSize: 22, lineHeight: 1.3 }} aria-live="polite" data-testid="live-sentence">{sentence(form)}</p>
                {error && <p className="error" role="alert" data-testid="save-error" style={{ marginTop: 'var(--s2)' }}>{error}</p>}
                <div style={{ display: 'flex', gap: 'var(--s3)', alignItems: 'center', marginTop: 'var(--s4)', flexWrap: 'wrap' }}>
                  <button className="btn btn-primary" onClick={save} disabled={saving} data-testid="save">{saving ? 'Saving…' : 'Save rules'}</button>
                  {state?.saved && !dirty && <a href="/follow" data-testid="continue">Continue to Follow</a>}
                </div>
              </div>
            )}
          </div>

          <aside className="c-8-5" aria-label="Saved rules" style={{ paddingTop: 'var(--s6)' }}>
            {state && (
              <div className="stack" data-testid="saved-info">
                <p className="label">Saved rules</p>
                {state.saved && state.hash && state.saved_at ? (
                  <>
                    <p><i className="dot dot-ok" />Saved {when(state.saved_at)}</p>
                    <p className="muted">Rules hash <span className="mono" data-testid="rules-hash" style={{ color: 'var(--ink)' }}>{state.hash.slice(0, 12)}</span></p>
                    <p className="muted prose" style={{ fontSize: 13.5 }}>The hash fingerprints exactly these limits. Each follow run records the rules it ran under.</p>
                  </>
                ) : (
                  <p className="muted prose"><i className="dot dot-off" />Not saved yet. Following a leader needs saved rules, so nothing can start until you save.</p>
                )}
              </div>
            )}
          </aside>
        </div>
      </div>
    </Page>
  )
}
