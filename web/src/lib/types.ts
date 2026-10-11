export type Decision = 'ACCEPT' | 'REJECT'
export type Source = 'demo' | 'recon'

export interface LogEvent {
  seq: number
  ts: number
  client_order_id: string
  symbol: string
  side: 'BUY' | 'SELL'
  qty: number
  price: number
  decision: Decision
  reason_code: string
  reason_text: string
  prev_hash: string
  hash: string
}

export interface Report {
  source: Source
  chain: { ok: boolean; events_verified: number; first_bad_seq: number | null; reason: string | null }
  counts: { events: number; accepted: number; rejected: number; rejects_by_reason: Record<string, number> }
  pnl: {
    realized_gross: number
    fees: number
    realized_net: number
    by_symbol: Record<string, { gross: number; fees: number; net: number }>
  }
  positions: Record<string, number>
  cash: number
  cash_identity_ok: boolean
  findings: { seq: number; kind: string; detail: string }[]
  head_hash: string
  state_hash: string
  scope: string
  rules: Rules
}

export interface Rules {
  symbols: Record<string, { reference_price: number }>
  max_position_per_symbol: number
  max_daily_loss: number
  price_band_pct: number
  max_orders_per_minute: number
  loss_streak_cooldown: { losses: number; cooldown_seconds: number }
  kill_switch: boolean
  fees: { per_share_minor: number; min_per_order_minor: number }
}

export interface TamperResult {
  tampered_seq: number
  field: string
  before: number
  after: number
  tampered_event: LogEvent
  events_total: number
  events_verified_before_break: number
  chain_ok: boolean
  first_bad_seq: number | null
  reason: string | null
  message: string
  note: string
}

export interface ReconFinding {
  category: string
  detail: string
  log_seqs: number[]
  csv_rows: number[]
  related_log_seqs?: number[]
  differs?: string[]
  group?: { log_fills: number; broker_rows: number }
}

export interface ReconResult {
  input: string
  log_events: number
  reconciliation: {
    status: string
    reason?: string
    counts: Record<string, number>
    findings: ReconFinding[]
    log_fills: number
    csv_rows: number
    rejected_rows: { row: number; message: string }[]
    policy: { tolerance_seconds: number }
    implied_difference: {
      position: Record<string, number>
      cash_excl_fees: number
      excluded_ambiguous: { findings: number; log_fills: number; csv_rows: number }
    }
  }
}

export interface CfBlock {
  row: number
  client_order_id: string
  symbol: string
  side: string
  qty: number
  price: number
  ts: string
  rule: string
  limit: string | number
  value: string | number
  gate_text: string
}

export interface PnlSide {
  gross: number
  fees: number
  net: number
  positions: Record<string, number>
}

export interface CfResult {
  input: string
  orders: number
  accepted: number
  blocked: number
  blocked_by_rule: Record<string, number>
  blocks: CfBlock[]
  pnl: { original: PnlSide; with_rules: PnlSide; impact: { gross: number; fees: number; net: number } }
  assumptions: string[]
  rejected_rows: { row: number; message: string }[]
  duplicates_ignored: unknown[]
}

export interface Stat {
  median: number
  min: number
  max: number
}

export interface BenchRow {
  fsync: 'per_order' | 'batched' | 'none'
  mode: 'closed' | 'pipelined'
  conns: number
  valid_reps: number
  total_reps: number
  orders_per_sec: Stat
  lat_p50: Stat
  lat_p99: Stat
  gate_peak_rss_mb: Stat
}

export interface BenchRecovery {
  events: number
  log_mb: number
  valid: boolean
  outcome?: string
  recovery_s_median?: number
  peak_rss_mb?: number
  note?: string
}

export interface BenchRun {
  file: string
  label: string
  backend: 'native' | 'docker'
  orders: number
  reps: number
  environment: Record<string, string | number | undefined>
  rows: BenchRow[]
  recovery: BenchRecovery[]
}

export interface Bench {
  runs: BenchRun[]
  fsync_micro: null | { file: string; calls: number; 'os.fsync': Record<string, number>; F_FULLFSYNC: Record<string, number> }
}

export interface DemoStatus {
  state: 'idle' | 'running' | 'done' | 'failed'
  run: number
  sent: number
  total: number
  error: string | null
}
