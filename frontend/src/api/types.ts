// The API contract, typed. Mirrors api/main.py and api/run_store.py.
//
// ADR-0013's deciding argument lives here: the old frontend silently ignored the
// `escalation` SSE event, so an escalated run rendered as a blank page for days.
// `StreamEvent` is a closed union and the run reducer switches over it with an
// exhaustive `never` check — a new server event that the client does not handle
// is now a compile error, not a blank screen.

export type AgentMode = 'multi' | 'single';

export type TerminationReason =
  | 'completed'
  | 'escalated'
  | 'max_iterations'
  | (string & {}); // the server may add reasons; unknown ones render generically

export type ComplianceVerdictKind = 'clear' | 'needs-revision' | 'escalate' | (string & {});

/** {period: {field: value}} parsed from the brief's snapshot table (ADR-0012). */
export type Figures = Record<string, Record<string, number>>;

export interface ComplianceVerdict {
  verdict?: ComplianceVerdictKind;
  reasons?: string[];
  gap_type?: string | null;
}

export interface RiskAssessment {
  risk_summary?: string;
  red_flags?: string[];
}

/** The interrupt-shaped package an escalated run carries instead of a brief (ADR-0009). */
export interface EscalationPackage {
  reason?: string;
  unresolved?: string[];
  evidence_summary?: string;
  research_findings?: unknown;
  risk_assessment?: RiskAssessment | null;
  compliance_reasons?: string[];
}

/** Structured outlook from the consensus tool. Rendered by the ported chart module, never by the LLM. */
export interface Forecast {
  ticker?: string;
  company?: string;
  as_of?: string;
  currency?: string;
  fy_label?: string;
  metrics?: Array<{ quarters?: Array<{ kind?: string }> } & Record<string, unknown>>;
  drivers?: unknown[];
  source?: string;
}

export interface Signal {
  kind: 'filing' | 'price' | 'baseline' | (string & {});
  detail?: string;
}

/** GET /runs/{id} — the persisted shape (build_run_record + save_run). */
export interface RunRecord {
  id: string;
  created_at?: string;
  query?: string;
  company_target?: string;
  company_key?: string;
  agent_mode?: AgentMode;
  termination_reason?: TerminationReason | null;
  final_report?: string;
  figures?: Figures;
  escalation?: EscalationPackage | null;
  compliance_verdict?: ComplianceVerdict | null;
  risk_assessment?: RiskAssessment | null;
  forecast?: Forecast | null;
  tools_called?: string[];
  iteration_count?: number;
  elapsed_seconds?: number;
  model?: string;
  signal?: Signal | null;
}

export interface FigureChange {
  period: string;
  field: string;
  before: number;
  after: number;
  pct_change: number | null;
}

/** agent/deltas.py — an empty delta is a correct, expected answer (ADR-0012). */
export interface Delta {
  figure_changes: FigureChange[];
  verdict_change: { before: string; after: string } | null;
  red_flag_change: { before: number; after: number } | null;
  is_empty: boolean;
}

export interface RunSummary {
  id: string;
  created_at?: string;
  agent_mode?: AgentMode;
  termination_reason?: TerminationReason | null;
  verdict?: ComplianceVerdictKind | null;
  figures: Figures;
}

/** GET /watchlist — one tracked company. */
export interface WatchlistEntry {
  id?: string;
  company_key: string;
  name: string;
  cik: number | null;
  ticker: string | null;
  created_at?: string;
  last_seen_accession?: string | null;
  latest_run: RunSummary | null;
  delta: Delta | null;
}

/** GET /companies/{key} */
export interface CompanyTimeline {
  company_key: string;
  company_target?: string;
  delta: Delta | null;
  runs: RunSummary[];
}

/** GET /sweeps — the evidence the refresh worker is alive (ADR-0011). */
export interface Sweep {
  id: string;
  started_at: string;
  finished_at: string;
  checked: number;
  refreshed: number;
  notified: number;
  error: string | null;
}

export interface ResearchRequest {
  query: string;
  max_iterations?: number;
  agent_mode?: AgentMode;
}

// ── SSE ─────────────────────────────────────────────────────────────────────

export interface ToolPreview {
  tool: string;
  success: boolean;
  preview?: string;
}

/**
 * `node_complete` payloads, discriminated by node (api/main.py `_diff_state`).
 * On the wire a lone tool reports under its own name (`node: "web_search"`);
 * the parser normalises that to `node: 'tool'` so every variant here is a
 * literal and the switch over them stays exhaustive.
 */
export type NodeComplete =
  | { event: 'node_complete'; node: 'supervisor';
      data: { plan?: string; tools_queued?: string[]; iteration?: number; company_target?: string } }
  | { event: 'node_complete'; node: 'dispatcher'; data: { tools?: string[]; tool_results?: ToolPreview[] } }
  | { event: 'node_complete'; node: 'risk_analyst'; data: RiskAssessment }
  | { event: 'node_complete'; node: 'compliance_checker'; data: ComplianceVerdict }
  | { event: 'node_complete'; node: 'synthesis'; data: { streaming?: boolean } }
  | { event: 'node_complete'; node: 'tool'; data: ToolPreview };

export type StreamEvent =
  | NodeComplete
  | { event: 'forecast'; data: Forecast }
  | { event: 'meta'; data: { termination_reason: TerminationReason } }
  | { event: 'escalation'; data: { package: EscalationPackage; verdict: ComplianceVerdict } }
  | { event: 'report_chunk'; data: string }
  | { event: 'report'; data: string }
  | { event: 'saved'; data: { run_id: string } }
  | { event: 'error'; data: string }
  | { event: 'done' }
  // Runtime half of the guard: a name the client has never heard of is kept
  // and shown, never dropped. (Compile time covers the names we do know.)
  | { event: 'unknown'; name: string; raw: unknown };

export type StreamEventName = StreamEvent['event'];

/** Every wire event name the client handles — `satisfies` fails the build if one is missing. */
export const KNOWN_EVENTS = [
  'node_complete', 'forecast', 'meta', 'escalation', 'report_chunk',
  'report', 'saved', 'error', 'done',
] as const satisfies readonly Exclude<StreamEventName, 'unknown'>[];

type MissingEvents = Exclude<Exclude<StreamEventName, 'unknown'>, (typeof KNOWN_EVENTS)[number]>;
// Compile-time: KNOWN_EVENTS must list every event in the union.
export const _allEventsListed: MissingEvents extends never ? true : never = true;

export const KNOWN_NODES = [
  'supervisor', 'dispatcher', 'risk_analyst', 'compliance_checker', 'synthesis',
] as const;

export function assertNever(x: never, what: string): never {
  throw new Error(`Unhandled ${what}: ${JSON.stringify(x)}`);
}
