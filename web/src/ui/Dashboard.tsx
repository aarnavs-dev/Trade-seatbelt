import { useEffect } from 'react'
import { store, useSnap } from '../lib/store'
import ChainScene from '../scene/ChainScene'
import { Page } from './kit'

export default function Dashboard() {
  const s = useSnap()
  useEffect(() => { store.connect('demo'); return () => { store.clear() } }, [])
  return (
    <Page title="Dashboard">
      <ChainScene store={store} variant="mini" label="The latest session's decisions as blocks: accepted in blue, rejected in orange" />
      <div className="container" style={{ padding: 'var(--s8) var(--gutter) var(--s12)' }}>
        <h1 style={{ fontSize: 'clamp(36px, 4.4vw, 56px)' }}>Dashboard</h1>
        <p className="muted prose" style={{ marginTop: 'var(--s2)' }}>This page is built in the next step. Above: {s.total ? `the last ${Math.min(s.total, 30)} decisions of the most recent demo log.` : 'no log yet. Run the demo from the Audit page.'}</p>
      </div>
    </Page>
  )
}
