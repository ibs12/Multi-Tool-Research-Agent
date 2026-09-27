import { useEffect, useState } from 'react';
import { ApiError, api } from '../api/client';
import type { CompanyTimeline, WatchlistEntry } from '../api/types';
import { DeltaList, FiguresTable, OutcomeBadge } from '../components/Figures';
import { fmtAgo, fmtWhen } from '../format';
import { href, navigate, onLinkClick } from '../router';
import { useTitle } from '../components/a11y';

export function CompanyView({ companyKey }: { companyKey: string }) {
  const [entry, setEntry] = useState<WatchlistEntry | null | undefined>(undefined);
  const [timeline, setTimeline] = useState<CompanyTimeline | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.watchlist().then(
      list => setEntry(list.find(e => e.company_key === companyKey) ?? null),
      () => setEntry(null));
    api.company(companyKey).then(setTimeline, e => {
      if (e instanceof ApiError && e.status === 404) setTimeline(null);   // tracked, no runs yet
      else setError(String(e.message ?? e));
    });
  }, [companyKey]);

  async function remove() {
    if (!entry || !confirm(`Stop tracking ${entry.name}? Its saved runs stay reachable by link.`)) return;
    try {
      await api.removeCompany(companyKey);
      navigate('/');
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  const name = entry?.name ?? timeline?.company_target ?? companyKey;
  const latest = timeline?.runs[0];
  const researchQuery = `Analyse ${name} investment outlook`;
  useTitle(entry === undefined && timeline === undefined ? null : name);

  return (
    <main className="page">
      <a className="back-link" href="/" onClick={onLinkClick}>← Watchlist</a>
      <div className="page-head">
        <div>
          <div className="panel-label">
            {entry?.ticker ?? ''}{entry?.ticker && entry?.cik ? ' · ' : ''}{entry?.cik ? `CIK ${entry.cik}` : ''}
            {entry === null && 'Not on your watchlist'}
          </div>
          <h1 className="page-title">{name}</h1>
          {latest && <p className="muted small">Last refreshed {fmtAgo(latest.created_at)} · {fmtWhen(latest.created_at)}</p>}
        </div>
        <div className="head-actions">
          <a className="copy-btn" href={href({ name: 'research', query: researchQuery })} onClick={onLinkClick}>▶ Fresh brief</a>
          {entry && <button className="copy-btn danger" onClick={remove}>Remove</button>}
        </div>
      </div>

      {error && <div className="notice neg" role="alert">{error}</div>}
      {timeline === undefined && !error && <p className="muted" aria-busy="true">Loading…</p>}

      {timeline === null && (
        <section className="card">
          <h2 className="section-title">Awaiting its baseline</h2>
          <p>
            No runs yet. The next sweep runs a baseline brief for this company automatically, or start
            one now with <b>Fresh brief</b>.
          </p>
        </section>
      )}

      {timeline && latest && (
        <>
          <section className="card" aria-labelledby="changed">
            <div className="card-row">
              <h2 id="changed" className="section-title">What changed</h2>
              <span className="muted small">latest run vs the one before</span>
            </div>
            {latest.termination_reason === 'escalated' && (
              <p className="neg">The latest run was escalated by compliance — there is no brief to act on. Open it to see why.</p>
            )}
            <DeltaList delta={timeline.delta} />
          </section>

          <section className="card" aria-labelledby="figures">
            <div className="card-row">
              <h2 id="figures" className="section-title">Latest figures</h2>
              <a href={href({ name: 'run', id: latest.id })} onClick={onLinkClick} className="small">Open the brief →</a>
            </div>
            <FiguresTable figures={latest.figures} />
          </section>

          <section className="card" aria-labelledby="timeline">
            <h2 id="timeline" className="section-title">Run timeline · {timeline.runs.length}</h2>
            <ol className="timeline">
              {timeline.runs.map(r => (
                <li key={r.id}>
                  <a href={href({ name: 'run', id: r.id })} onClick={onLinkClick} className="timeline-row">
                    <span className="timeline-when">{fmtWhen(r.created_at)}</span>
                    <OutcomeBadge reason={r.termination_reason} verdict={r.verdict} />
                    <span className="muted small">{r.agent_mode ?? '?'}-agent</span>
                  </a>
                </li>
              ))}
            </ol>
          </section>
        </>
      )}
    </main>
  );
}
