/** Wording about Solana that the landing page and the follow page both show, so they cannot drift apart. */
export const SOLANA_STEPS = [
  { title: 'Publish', text: 'The leader’s strategy posts each trade as a signed memo transaction on Solana devnet.' },
  { title: 'Verify', text: 'Anyone can read it. Your copier accepts only signals signed by the leader’s key, and every signal links to Solana Explorer.' },
  { title: 'Decide', text: 'Nothing happens because a signal is on-chain. Your own rules decide.' },
] as const

export const SOLANA_DOES_NOT_TITLE = 'What Solana does not do here'
export const SOLANA_DOES_NOT = 'It does not run your trades. It does not judge whether the leader is any good. Devnet is a test network, and fills are paper.'

/** Follow page only: what is and is not public. Today only the leader's signal is published; nothing of yours is. */
export const SOLANA_WHAT_IS_PUBLIC = 'What goes on chain is the leader’s signal only (symbol, side, size, reference price). Your orders, positions, limits and decisions stay on this machine; the chain holds no funds and executes nothing.'
export const LOCAL_FEED_WARNING = 'Not from Solana. These signals were generated on this machine by replaying the leader’s strategy. There is no transaction to look up, and nothing here was published or signed.'
