// The run panel's model: one reducer over typed SSE events.
//
// ADR-0013: the live-run view is one component with two data sources. A run you
// are watching feeds this reducer from the SSE stream; a run the cron produced
// at 3am is replayed through the *same* reducer by `recordToEvents`, so the two
// can never render differently.

import {
  assertNever,
  type ComplianceVerdict, type EscalationPackage, type Forecast, type NodeComplete,
  type RunRecord, type StreamEvent, type TerminationReason,
} from '../api/types';

export type LogKind = 'system' | 'supervisor' | 'tool' | 'tool failed' | 'dispatcher'
  | 'agent' | 'agent escalate' | 'synthesis';

export interface LogEntry {
  kind: LogKind;
  icon: string;
  text: string;
  detail?: string;
}

export type Outcome =
  | { kind: 'brief'; markdown: string }
  | { kind: 'escalation'; package: EscalationPackage; verdict: ComplianceVerdict };

export interface RunState {
  phase: 'idle' | 'running' | 'streaming' | 'done' | 'failed';
  outcome: Outcome | null;
  streamingText: string;
  forecast: Forecast | null;
  runId: string | null;
  companyTarget: string;
  tools: string[];
  iterations: number;
  terminationReason: TerminationReason | null;
  statusLine: string;
  error: string | null;
  log: LogEntry[];
}

export const initialRunState: RunState = {
  phase: 'idle',
  outcome: null,
  streamingText: '',
  forecast: null,
  runId: null,
  companyTarget: '',
  tools: [],
  iterations: 0,
  terminationReason: null,
  statusLine: '',
  error: null,
  log: [],
};

export type RunAction =
  | { type: 'start'; query: string }
  | { type: 'event'; event: StreamEvent }
  | { type: 'failed'; message: string }
  | { type: 'reset' };

const COMPLIANCE_NOTE: Record<string, string> = {
  clear: 'brief can ship',
  'needs-revision': 'sending back to research',
  escalate: 'escalating to a human',
};

const addTool = (tools: string[], t: string) => (tools.includes(t) ? tools : [...tools, t]);
const log = (s: RunState, e: LogEntry): RunState => ({ ...s, log: [...s.log, e] });

function onNode(s: RunState, e: NodeComplete): RunState {
  switch (e.node) {
    case 'supervisor': {
      const tools = e.data.tools_queued ?? [];
      const next = {
        ...s,
        iterations: e.data.iteration ?? s.iterations,
        companyTarget: e.data.company_target || s.companyTarget,
        statusLine: `🧠 Supervisor → queuing: ${tools.length ? tools.join(', ') : 'synthesising…'}`,
      };
      return log(next, {
        kind: 'supervisor', icon: '🧠',
        text: `Supervisor [iter ${e.data.iteration ?? '?'}] → ${tools.length ? tools.join(', ') : 'ready to synthesise'}`,
        detail: e.data.plan,
      });
    }
    case 'dispatcher': {
      const results = e.data.tool_results ?? [];
      let next: RunState = { ...s, statusLine: `⚙️ Ran: ${(e.data.tools ?? []).join(', ')}` };
      if (!results.length) {
        return log({ ...next, statusLine: '⚙️ Running tools in parallel…' },
          { kind: 'dispatcher', icon: '⚙️', text: 'Dispatcher running tools concurrently' });
      }
      for (const r of results) {
        const ok = r.success !== false;
        next = log({ ...next, tools: addTool(next.tools, r.tool) }, {
          kind: ok ? 'tool' : 'tool failed', icon: ok ? '✓' : '✗',
          text: `${r.tool}: ${ok ? 'success' : 'failed'}`,
          detail: ok && r.preview ? r.preview.slice(0, 100) : undefined,
        });
      }
      return next;
    }
    case 'tool': {
      const ok = e.data.success !== false;
      return log({ ...s, tools: addTool(s.tools, e.data.tool) }, {
        kind: ok ? 'tool' : 'tool failed', icon: ok ? '✓' : '✗',
        text: `${e.data.tool}: ${ok ? 'success' : 'failed'}`,
        detail: ok && e.data.preview ? e.data.preview.slice(0, 100) : undefined,
      });
    }
    case 'risk_analyst': {
      const flags = e.data.red_flags ?? [];
      return log({ ...s, statusLine: '⚠️ Risk analyst reviewing the evidence…' }, {
        kind: 'agent', icon: '⚠️',
        text: `Risk analyst → ${flags.length} red flag${flags.length === 1 ? '' : 's'}`,
        detail: e.data.risk_summary,
      });
    }
    case 'compliance_checker': {
      const verdict = e.data.verdict ?? 'unknown';
      const note = COMPLIANCE_NOTE[verdict];
      return log({ ...s, statusLine: `⚖️ Compliance check: ${verdict}` }, {
        kind: verdict === 'escalate' ? 'agent escalate' : 'agent', icon: '⚖️',
        text: `Compliance → ${verdict}${note ? ` (${note})` : ''}`,
        detail: (e.data.reasons ?? []).join('; ') || undefined,
      });
    }
    case 'synthesis':
      return log({ ...s, statusLine: '✍️ Writing analyst brief…' },
        { kind: 'synthesis', icon: '✍️', text: 'Synthesis node generating report' });
    default:
      return assertNever(e, 'node');
  }
}

function onEvent(s: RunState, e: StreamEvent): RunState {
  switch (e.event) {
    case 'node_complete':
      return onNode(s, e);
    case 'forecast': {
      const metrics = e.data.metrics ?? [];
      const nQ = metrics.reduce((n, m) => n + (m.quarters ?? []).filter(q => q.kind === 'estimate').length, 0);
      return log({ ...s, forecast: e.data }, {
        kind: 'tool', icon: '📈', text: `Forward outlook ready — ${e.data.fy_label ?? ''} consensus`,
        detail: `${nQ} forecast quarter(s) across ${metrics.length} metric(s)`,
      });
    }
    case 'meta':
      return { ...s, terminationReason: e.data.termination_reason };
    case 'escalation':
      // Terminal (ADR-0009): no brief follows, and this must never look like one is missing.
      return log({
        ...s, phase: 'done', terminationReason: 'escalated', streamingText: '',
        outcome: { kind: 'escalation', package: e.data.package ?? {}, verdict: e.data.verdict ?? {} },
      }, {
        kind: 'agent escalate', icon: '⚠', text: 'Escalated to a human — no brief produced',
        detail: e.data.package?.reason,
      });
    case 'report_chunk':
      return { ...s, phase: 'streaming', streamingText: s.streamingText + e.data };
    case 'report':
      return log({ ...s, phase: 'done', streamingText: '', outcome: { kind: 'brief', markdown: e.data } }, {
        kind: 'synthesis', icon: '✅',
        text: `Report complete — ${s.tools.length} tools, ${s.iterations} iterations`,
      });
    case 'saved':
      return log({ ...s, runId: e.data.run_id }, {
        kind: 'system', icon: '🔗', text: `Saved — permalink ready (${e.data.run_id})`,
      });
    case 'error':
      return log({ ...s, phase: s.outcome ? s.phase : 'failed', error: e.data },
        { kind: 'system', icon: '✗', text: `Error: ${e.data}` });
    case 'done':
      // A closed stream with neither a brief nor an escalation is a failure the
      // user must see — never an empty panel that reads as "nothing happened".
      return s.outcome
        ? { ...s, phase: 'done' }
        : { ...s, phase: 'failed', error: s.error ?? 'The run ended without a brief or an escalation.' };
    case 'unknown':
      return log(s, { kind: 'system', icon: '?', text: `Unrecognised server event "${e.name}" — the client may be out of date` });
    default:
      return assertNever(e, 'event');
  }
}

export function runReducer(s: RunState, a: RunAction): RunState {
  switch (a.type) {
    case 'start':
      return { ...initialRunState, phase: 'running', statusLine: 'Initialising agent…',
               log: [{ kind: 'system', icon: '▶', text: `Starting research: "${a.query.slice(0, 60)}"` }] };
    case 'event':
      return onEvent(s, a.event);
    case 'failed':
      return log({ ...s, phase: 'failed', error: a.message }, { kind: 'system', icon: '✗', text: a.message });
    case 'reset':
      return initialRunState;
    default:
      return assertNever(a, 'action');
  }
}

/**
 * A saved run, as the event sequence that would have produced it. Replaying it
 * through `runReducer` is the second data source for the same run panel.
 */
export function recordToEvents(r: RunRecord): StreamEvent[] {
  const events: StreamEvent[] = [];
  const tools = r.tools_called ?? [];
  if (r.company_target || r.iteration_count) {
    events.push({ event: 'node_complete', node: 'supervisor',
                  data: { iteration: r.iteration_count, company_target: r.company_target, tools_queued: [] } });
  }
  if (tools.length) {
    events.push({ event: 'node_complete', node: 'dispatcher',
                  data: { tools, tool_results: tools.map(t => ({ tool: t, success: true })) } });
  }
  if (r.risk_assessment) events.push({ event: 'node_complete', node: 'risk_analyst', data: r.risk_assessment });
  if (r.compliance_verdict?.verdict) {
    events.push({ event: 'node_complete', node: 'compliance_checker', data: r.compliance_verdict });
  }
  if (r.termination_reason === 'escalated') {
    events.push({ event: 'escalation',
                  data: { package: r.escalation ?? {}, verdict: r.compliance_verdict ?? {} } });
  } else {
    if (r.forecast) events.push({ event: 'forecast', data: r.forecast });
    if (r.termination_reason && r.termination_reason !== 'completed') {
      events.push({ event: 'meta', data: { termination_reason: r.termination_reason } });
    }
    if (r.final_report) events.push({ event: 'report', data: r.final_report });
  }
  events.push({ event: 'saved', data: { run_id: r.id } });
  events.push({ event: 'done' });
  return events;
}

export function replay(r: RunRecord): RunState {
  const start: RunState = { ...initialRunState, phase: 'running',
    log: [{ kind: 'system', icon: '◦', text: `Replaying saved run ${r.id}` }] };
  return recordToEvents(r).reduce((s, event) => runReducer(s, { type: 'event', event }), start);
}
