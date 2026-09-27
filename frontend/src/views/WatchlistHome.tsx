import { useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import type { Sweep, WatchlistEntry } from '../api/types';
import { DeltaList, OutcomeBadge } from '../components/Figures';
import { SweepStatus } from '../components/SweepStatus';
import { ScrollRegion, useTitle } from '../components/a11y';
import { fmtAgo, fmtFigure, fmtWhen, sortPeriods } from '../format';
import { href, onLinkClick } from '../router';
import { AddCompany } from './AddCompany';

const LAST_LOOKED = 'watchlist.lastLooked';

/**
 * "Since you last looked" is per-viewer, so it lives in this browser. The value
 * read at mount is what this visit compares against; the visit itself becomes
 * the next baseline.
 */
function useLastLooked(): string | null {
  const [previous] = useState<string | null>(() => {
    try { return localStorage.getItem(LAST_LOOKED); } catch { return null; }
  });
  useEffect(() => {
    try { localStorage.setItem(LAST_LOOKED, new Date().toISOString()); } catch { /* private mode */ }
  }, []);
  return previous;
}

const needsAttention = (e: WatchlistEntry) =>
  e.latest_run?.termination_reason === 'escalated' || (e.delta != null && !e.delta.is_empty);

const isNewSince = (e: WatchlistEntry, since: string | null) =>
  !since || (e.latest_run?.created_at != null && e.latest_run.created_at > since);

/** The newest actual (non-estimate) fiscal year with a revenue figure. */
function headline(e: WatchlistEntry): string {
  const figs = e.latest_run?.figures ?? {};
  const fy = sortPeriods(Object.keys(figs)).filter(p => /^FY\d{4}$/.test(p) && figs[p]?.revenue != null).pop();
  return fy ? `${fy} revenue ${fmtFigure('revenue', figs[fy].revenue)}` : '';
}

export function WatchlistHome() {
  const [entries, setEntries] = useState<WatchlistEntry[] | null>(null);
  const [sweeps, setSweeps] = useState<Sweep[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const lastLooked = useLastLooked();
  useTitle('Watchlist');

  const load = () => {
    api.watchlist().then(setEntries, e => setError(String(e.message ?? e)));
    api.sweeps(1).then(setSweeps, () => setSweeps([]));
  };
  useEffect(load, []);

  const changed = useMemo(
    () => (entries ?? []).filter(e => needsAttention(e) && isNewSince(e, lastLooked)),
    [entries, lastLooked]);

  if (error) {
    return (
      <main className="page">
        <div className="notice neg" role="alert">Could not load the watchlist: {error}</div>
      </main>
    );
  }
  if (!entries) return <main className="page"><p className="muted" aria-busy="true">Loading watchlist…</p></main>;

  return (
    <main className="page">
      <div className="page-head">
        <div>
          <div className="panel-label">Watchlist</div>
          <h1 className="page-title">What changed</h1>
          <SweepStatus sweeps={sweeps} />
        </div>
        <button className="copy-btn" onClick={() => setAdding(a => !a)} aria-expanded={adding}>
          {adding ? 'Cancel' : '+ Add company'}
        </button>
      </div>

      {adding && <AddCompany onAdded={() => { setAdding(false); load(); }} />}

      {entries.length === 0 ? (
        <section className="card onboarding">
          <h2>Start a watchlist</h2>
          <p>
            Add the companies you are deciding on. Each one gets a baseline brief, then the worker
            checks for new SEC filings and big price moves every weekday and re-researches only when
            one lands.
          </p>
          {!adding && <button className="run-btn" onClick={() => setAdding(true)}><span className="btn-text">+ Add your first company</span></button>}
        </section>
      ) : changed.length === 0 ? (
        <section className="card quiet-card" aria-live="polite">
          <div className="quiet-mark" aria-hidden="true">✓</div>
          <div>
            <h2>Nothing to do</h2>
            <p>
              No material changes across {entries.length} {entries.length === 1 ? 'company' : 'companies'}
              {lastLooked ? <> since you last looked ({fmtWhen(lastLooked)})</> : null}.
              Most days that is the right answer.
            </p>
          </div>
        </section>
      ) : (
        <section aria-labelledby="attention">
          <h2 id="attention" className="section-title">Needs your attention · {changed.length}</h2>
          <div className="attention-grid">
            {changed.map(e => (
              <a key={e.company_key} className="card attention-card" href={href({ name: 'company', key: e.company_key })} onClick={onLinkClick}>
                <div className="card-row">
                  <span className="company-name">{e.name}</span>
                  <OutcomeBadge reason={e.latest_run?.termination_reason} verdict={e.latest_run?.verdict} />
                </div>
                <div className="muted small">Refreshed {fmtAgo(e.latest_run?.created_at)}</div>
                {e.latest_run?.termination_reason === 'escalated'
                  ? <p className="neg small">Compliance escalated — no brief. Review before acting.</p>
                  : <DeltaList delta={e.delta} limit={3} />}
              </a>
            ))}
          </div>
        </section>
      )}

      {entries.length > 0 && (
        <section aria-labelledby="all">
          <h2 id="all" className="section-title">All companies · {entries.length}</h2>
          <ScrollRegion label="All companies, scrollable">
            <table className="watch-table">
              <caption className="sr-only">Tracked companies, their last refresh and whether anything material changed</caption>
              <thead>
                <tr>
                  <th scope="col">Company</th>
                  <th scope="col" className="col-secondary">Latest</th>
                  <th scope="col">Last refresh</th>
                  <th scope="col">Change</th>
                </tr>
              </thead>
              <tbody>
                {entries.map(e => (
                  <tr key={e.company_key}>
                    <th scope="row">
                      <a href={href({ name: 'company', key: e.company_key })} onClick={onLinkClick} className="company-link">
                        {e.name}
                      </a>
                      {e.ticker && <span className="ticker">{e.ticker}</span>}
                    </th>
                    <td className="muted col-secondary">{headline(e) || '—'}</td>
                    <td data-label="Refreshed">{e.latest_run ? fmtAgo(e.latest_run.created_at) : <span className="muted">awaiting baseline</span>}</td>
                    <td>
                      {!e.latest_run ? <span className="muted">—</span>
                        : needsAttention(e) ? <span className="change-flag">● changed</span>
                        : <span className="muted">no material change</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </ScrollRegion>
        </section>
      )}
    </main>
  );
}
