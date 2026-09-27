import { Suspense, lazy, useEffect, useReducer, useRef, useState } from 'react';
import { ApiError, api, streamResearch } from '../api/client';
import type { AgentMode, RunRecord } from '../api/types';
import { EscalationCard, escalationAsText } from '../report/EscalationCard';
import { fmtWhen } from '../format';
import { href, navigate } from '../router';
import { Announce, useTitle } from '../components/a11y';
import { initialRunState, replay, runReducer, type RunState } from '../run/runModel';

/**
 * The run panel (ADR-0013): ONE component, two data sources.
 *   live  — a run you are watching, fed by the SSE stream
 *   saved — a finished run (yours, a shared link, or the 3am cron), replayed
 *           from the run store through the same reducer
 */
export type RunSource = { kind: 'live'; query: string } | { kind: 'saved'; id: string };

const QUICK = [
  'Analyse JPMorgan Chase investment outlook',
  'Analyse Apple Inc. investment outlook',
  'Analyse NVIDIA investment outlook',
  'Analyse Eli Lilly investment outlook',
  'What are the investment risks in the US banking sector?',
  'Analyse Goldman Sachs investment risk profile',
];

// Chart.js + marked + DOMPurify are most of the bundle and only a brief needs
// them, so the watchlist screens load without them.
const Brief = lazy(() => import('../report/Brief').then(m => ({ default: m.Brief })));

function readMode(): AgentMode {
  try { return localStorage.getItem('agentMode') === 'single' ? 'single' : 'multi'; } catch { return 'multi'; }
}

export function RunPanel({ source }: { source: RunSource }) {
  const [state, dispatch] = useReducer(runReducer, initialRunState);
  const [replayed, setReplayed] = useState<RunState | null>(null);
  const [record, setRecord] = useState<RunRecord | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [query, setQuery] = useState(source.kind === 'live' ? source.query : '');
  const [mode, setMode] = useState<AgentMode>(readMode);
  const [maxIter, setMaxIter] = useState(8);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const abort = useRef<AbortController | null>(null);
  const input = useRef<HTMLTextAreaElement>(null);

  // Saved source: fetch once and replay through the reducer.
  useEffect(() => {
    if (source.kind !== 'saved') return;
    setReplayed(null); setRecord(null); setLoadError(null);
    api.run(source.id).then(r => {
      setRecord(r);
      setReplayed(replay(r));
      setQuery(r.query ?? '');
    }, e => setLoadError(e instanceof ApiError && e.status === 404
      ? 'This run does not exist — the link may be mistyped.'
      : `Could not load this run: ${e.message ?? e}`));
  }, [source.kind === 'saved' ? source.id : null]);

  useEffect(() => () => abort.current?.abort(), []);

  const view: RunState = source.kind === 'saved' ? (replayed ?? initialRunState) : state;
  const running = view.phase === 'running' || view.phase === 'streaming';

  function chooseMode(m: AgentMode) {
    setMode(m);
    try { localStorage.setItem('agentMode', m); } catch { /* private mode */ }
  }

  async function run() {
    const q = query.trim();
    if (q.length < 5) { input.current?.focus(); return; }
    if (source.kind === 'saved') { navigate(href({ name: 'research', query: q })); return; }
    abort.current?.abort();
    const ctrl = new AbortController();
    abort.current = ctrl;
    dispatch({ type: 'start', query: q });
    setStartedAt(Date.now());
    let runId: string | null = null;
    try {
      await streamResearch({ query: q, max_iterations: maxIter, agent_mode: mode }, event => {
        if (event.event === 'saved') runId = event.data.run_id;
        dispatch({ type: 'event', event });
      }, ctrl.signal);
      // The finished run is a permalink now; make the address bar say so
      // without remounting the view mid-read.
      if (runId) history.replaceState(null, '', href({ name: 'run', id: runId }));
    } catch (e) {
      if (ctrl.signal.aborted) return;
      dispatch({ type: 'failed', message: e instanceof ApiError && e.status === 503
        ? 'The agent is at capacity (3 concurrent runs). Try again in a minute.'
        : `Connection error: ${e instanceof Error ? e.message : e}` });
    } finally {
      setStartedAt(null);
    }
  }

  const company = view.companyTarget || record?.company_target || query || 'Analyst Brief';
  useTitle(view.outcome ? `${company} ${view.outcome.kind === 'escalation' ? '— escalated' : 'brief'}`
    : source.kind === 'saved' ? 'Saved run' : running ? 'Researching…' : 'Research');

  // One polite announcement per outcome; per-token streaming is deliberately silent.
  const announcement = source.kind !== 'live' ? ''
    : view.outcome?.kind === 'brief' ? `Brief ready for ${company}.`
    : view.outcome?.kind === 'escalation' ? 'Escalated to a human. No brief was produced.'
    : view.phase === 'failed' ? `Run failed. ${view.error ?? ''}` : '';

  return (
    <div className="layout">
      <aside className="left-panel">
        <div className="panel-header">
          <div className="panel-label">Research Terminal</div>
          <div className="panel-title">{source.kind === 'saved' ? 'Saved Run' : 'Query Interface'}</div>
        </div>

        <form className="query-section" onSubmit={e => { e.preventDefault(); run(); }}>
          <label className="query-label" htmlFor="query">Research Query</label>
          <textarea
            id="query" ref={input} className="query-input" rows={3} value={query}
            placeholder="e.g. Analyse Apple Inc. investment outlook"
            onChange={e => setQuery(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) run(); }}
          />
          <div className="query-footer">
            <div className="mode-control" role="group" aria-label="Agent pipeline">
              <span className="iter-label">Pipeline</span>
              <div className="mode-toggle">
                <button type="button" className={`mode-btn ${mode === 'multi' ? 'active' : ''}`} aria-pressed={mode === 'multi'}
                  title="Research → risk analyst → compliance checker. Compliance can send work back or escalate to a human instead of shipping a brief."
                  onClick={() => chooseMode('multi')}>Multi-agent</button>
                <button type="button" className={`mode-btn ${mode === 'single' ? 'active' : ''}`} aria-pressed={mode === 'single'}
                  title="Research → brief. No risk or compliance review; always produces a report."
                  onClick={() => chooseMode('single')}>Single</button>
              </div>
            </div>
            <label className="iter-control">
              <span className="iter-label">Max iterations</span>
              <select className="iter-select" value={maxIter} onChange={e => setMaxIter(Number(e.target.value))}>
                {[4, 6, 8, 12].map(n => <option key={n} value={n}>{n}</option>)}
              </select>
            </label>
            <button className={`run-btn ${running ? 'loading' : ''}`} type="submit" disabled={running}>
              <div className="spinner" aria-hidden="true" />
              <span className="btn-text">{source.kind === 'saved' ? '▶ RUN AGAIN' : '▶ RUN'}</span>
            </button>
          </div>
        </form>

        {source.kind === 'live' && (
          <div className="quick-section">
            <span className="quick-label">Quick Queries</span>
            <div className="quick-grid">
              {QUICK.map(q => (
                <button key={q} type="button" className="quick-btn" onClick={() => { setQuery(q); input.current?.focus(); }}>{q}</button>
              ))}
            </div>
          </div>
        )}

        <div className="activity-section">
          <div className="activity-header">
            <span className="panel-label" style={{ margin: 0 }}>Agent Activity</span>
          </div>
          {/* Not live: the status line below the loading bars carries progress,
              and a live log would read every tool result aloud. */}
          <div className="activity-log" role="log" aria-live="off" aria-label="Agent activity" tabIndex={0}>
            {view.log.length === 0 && (
              <div className="log-entry system"><span className="log-icon">◦</span>
                <span className="log-text">Agent ready. Enter a query to begin.</span></div>
            )}
            {view.log.map((l, i) => (
              <div key={i} className={`log-entry ${l.kind}`}>
                <span className="log-icon">{l.icon}</span>
                <span className="log-text">
                  {l.text}
                  {l.detail && <span className="log-plan">{l.detail.length > 120 ? `${l.detail.slice(0, 120)}…` : l.detail}</span>}
                </span>
              </div>
            ))}
          </div>
        </div>
      </aside>

      <main className="right-panel">
        <ReportHeader view={view} company={company} record={record} mode={mode} />
        <div className="report-body">
          <ReportBody view={view} source={source} startedAt={startedAt} mode={mode} loadError={loadError} />
        </div>
        <Announce message={announcement} />
      </main>
    </div>
  );
}

function ReportHeader({ view, company, record, mode }:
  { view: RunState; company: string; record: RunRecord | null; mode: AgentMode }) {
  const [copied, setCopied] = useState<'report' | 'link' | null>(null);
  const o = view.outcome;
  const flash = (k: 'report' | 'link') => { setCopied(k); setTimeout(() => setCopied(null), 2000); };

  function copy(text: string, k: 'report' | 'link') {
    navigator.clipboard.writeText(text).then(() => flash(k), () => window.prompt('Copy this:', text));
  }

  const meta = record
    ? `Saved run · ${fmtWhen(record.created_at)} · ${record.agent_mode ?? '?'}-agent`
    : o ? `Generated ${new Date().toLocaleString('en-GB')} · ${mode}-agent`
    : view.phase === 'idle' ? 'No report generated yet' : 'In progress';

  return (
    <div className="report-header">
      <div className="report-title-group">
        <h1 className="report-company">{o ? company : 'Analyst Brief'}</h1>
        <div className="report-meta">{meta}</div>
      </div>
      <div className="report-actions">
        <div className="report-badges">
          {o?.kind === 'escalation' && <span className="badge badge-escalated">escalated</span>}
          {o && <span className="badge badge-tools">{view.tools.length} tools</span>}
          {o?.kind === 'brief' && <span className="badge badge-iters">{view.iterations} iters</span>}
          {record?.elapsed_seconds != null && <span className="badge badge-time">{Math.round(record.elapsed_seconds)}s</span>}
        </div>
        {view.runId && o && (
          <button className="copy-btn" title="Copy a permalink to this run"
            onClick={() => copy(`${location.origin}${href({ name: 'run', id: view.runId! })}`, 'link')}>
            {copied === 'link' ? '✓ Link copied' : '🔗 Share'}
          </button>
        )}
        {o && (
          <button className="copy-btn" onClick={() => copy(
            o.kind === 'brief' ? o.markdown : escalationAsText(company, o.package, o.verdict), 'report')}>
            {copied === 'report' ? '✓ Copied' : '⎘ Copy'}
          </button>
        )}
      </div>
    </div>
  );
}

function ReportBody({ view, source, startedAt, mode, loadError }:
  { view: RunState; source: RunSource; startedAt: number | null; mode: AgentMode; loadError: string | null }) {
  if (loadError) {
    return <div className="empty-state"><div className="empty-icon">⬡</div>
      <div className="empty-title">Run unavailable</div><div className="empty-sub">{loadError}</div></div>;
  }
  const o = view.outcome;
  if (o?.kind === 'escalation') return <EscalationCard pkg={o.package} verdict={o.verdict} live={source.kind === 'live'} />;
  if (o?.kind === 'brief') {
    return (
      <Suspense fallback={<div className="markdown-body muted">Rendering brief…</div>}>
        <Brief markdown={o.markdown} forecast={view.forecast} />
      </Suspense>
    );
  }
  if (view.phase === 'streaming') {
    return <div className="markdown-body streaming" aria-busy="true">{view.streamingText}▊</div>;
  }
  if (view.phase === 'running') {
    return (
      <div className="loading-state visible">
        <div className="loading-bars" aria-hidden="true">{[0, 1, 2, 3, 4].map(i => <div key={i} className="loading-bar" />)}</div>
        <div className="loading-text">{source.kind === 'saved' ? 'LOADING' : 'RESEARCHING'}</div>
        <div className="loading-node" role="status" aria-live="polite">{view.statusLine || 'Loading…'}</div>
        {startedAt && <Eta startedAt={startedAt} mode={mode} />}
      </div>
    );
  }
  if (view.phase === 'failed') {
    return <div className="empty-state"><div className="empty-icon">✗</div>
      <div className="empty-title">No brief produced</div>
      <div className="empty-sub">{view.error}</div></div>;
  }
  return (
    <div className="empty-state">
      <div className="empty-icon">⬡</div>
      <div className="empty-title">No Report Generated</div>
      <div className="empty-sub">Enter a company name or sector query to generate a research brief.</div>
    </div>
  );
}

/**
 * A multi-agent run is minutes, not seconds. Show elapsed time against what is
 * normal, so a long run reads as working rather than hung.
 */
function Eta({ startedAt, mode }: { startedAt: number; mode: AgentMode }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t); }, []);
  const s = Math.floor((now - startedAt) / 1000);
  const clock = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
  const usual = mode === 'multi' ? '5–7 min' : '1–2 min';
  const overrun = mode === 'multi' ? 8 * 60 : 3 * 60;
  return (
    <div className="loading-eta">
      {s > overrun
        ? `${clock} elapsed · longer than usual — still streaming, not stuck`
        : `${clock} elapsed · ${mode === 'multi' ? 'multi-agent' : 'single-agent'} runs usually take ${usual}`}
    </div>
  );
}
