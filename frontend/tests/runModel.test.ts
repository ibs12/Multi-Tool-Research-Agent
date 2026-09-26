import { describe, expect, it } from 'vitest';
import type { RunRecord, StreamEvent } from '../src/api/types';
import { initialRunState, recordToEvents, replay, runReducer, type RunState } from '../src/run/runModel';

const feed = (events: StreamEvent[], from: RunState = runReducer(initialRunState, { type: 'start', query: 'Analyse X' })) =>
  events.reduce((s, event) => runReducer(s, { type: 'event', event }), from);

const escalation: StreamEvent = {
  event: 'escalation',
  data: { package: { reason: 'Revenue could not be verified', unresolved: ['Which FY?'] },
          verdict: { verdict: 'escalate', gap_type: 'unverifiable' } },
};

describe('run reducer', () => {
  it('an escalated stream ends in an escalation outcome, never a missing brief (ADR-0009)', () => {
    const s = feed([{ event: 'node_complete', node: 'compliance_checker', data: { verdict: 'escalate' } },
                    escalation, { event: 'saved', data: { run_id: 'r1' } }, { event: 'done' }]);
    expect(s.phase).toBe('done');
    expect(s.outcome).toMatchObject({ kind: 'escalation', package: { reason: 'Revenue could not be verified' } });
    expect(s.runId).toBe('r1');
  });

  it('a stream that closes with neither brief nor escalation is a visible failure', () => {
    const s = feed([{ event: 'done' }]);
    expect(s.phase).toBe('failed');
    expect(s.error).toMatch(/without a brief/);
  });

  it('streams tokens, then swaps to the final brief', () => {
    const mid = feed([{ event: 'report_chunk', data: '## Ver' }, { event: 'report_chunk', data: 'dict' }]);
    expect(mid).toMatchObject({ phase: 'streaming', streamingText: '## Verdict' });
    const end = feed([{ event: 'report', data: '## Verdict\nBullish' }, { event: 'done' }], mid);
    expect(end).toMatchObject({ phase: 'done', streamingText: '', outcome: { kind: 'brief' } });
  });

  it('an unknown server event is logged, not swallowed', () => {
    const s = feed([{ event: 'unknown', name: 'brand_new', raw: {} }]);
    expect(s.log.at(-1)?.text).toMatch(/brand_new/);
  });
});

describe('replay — the second data source for the same panel', () => {
  const base: RunRecord = { id: 'abc', query: 'Analyse Apple', company_target: 'Apple Inc.',
                            tools_called: ['sec_edgar', 'web_search'], iteration_count: 3 };

  it('replays an escalated saved run as an escalation', () => {
    const s = replay({ ...base, termination_reason: 'escalated',
                       escalation: { reason: 'Unverifiable' }, compliance_verdict: { verdict: 'escalate' } });
    expect(s.outcome?.kind).toBe('escalation');
    expect(s.phase).toBe('done');
  });

  it('replays a brief with its forecast, tools and permalink', () => {
    const s = replay({ ...base, termination_reason: 'completed', final_report: '# Brief',
                       forecast: { fy_label: 'FY2026', metrics: [] } });
    expect(s.outcome).toEqual({ kind: 'brief', markdown: '# Brief' });
    expect(s.forecast?.fy_label).toBe('FY2026');
    expect(s.tools).toEqual(['sec_edgar', 'web_search']);
    expect(s.runId).toBe('abc');
  });

  it('a live stream and a replay of its saved record reach the same outcome', () => {
    const rec: RunRecord = { ...base, termination_reason: 'escalated', escalation: escalation.data.package,
                             compliance_verdict: escalation.data.verdict };
    expect(replay(rec).outcome).toEqual(feed(recordToEvents(rec)).outcome);
  });
});
