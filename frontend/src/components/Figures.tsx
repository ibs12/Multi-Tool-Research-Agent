import type { Delta, Figures } from '../api/types';
import { ScrollRegion } from './a11y';
import { fieldLabel, fmtFigure, fmtPct, sortPeriods } from '../format';

/**
 * The latest run's structured figures. Periods run across on wide screens and
 * down on phones (CSS picks one), so a phone never has to scroll an 8-column
 * grid sideways to read a number.
 */
export function FiguresTable({ figures }: { figures: Figures }) {
  const periods = sortPeriods(Object.keys(figures));
  const fields = [...new Set(periods.flatMap(p => Object.keys(figures[p] ?? {})))];
  if (!periods.length) return <p className="muted">No figures could be read from this run's brief.</p>;
  return (
    <ScrollRegion label="Latest figures, scrollable">
      <table className="figures-table figures-tall">
        <caption className="sr-only">Latest figures by period</caption>
        <thead>
          <tr>
            <th scope="col">Period</th>
            {fields.map(f => <th scope="col" key={f}>{fieldLabel(f)}</th>)}
          </tr>
        </thead>
        <tbody>
          {periods.map(p => (
            <tr key={p} className={p.endsWith('E') ? 'is-estimate' : ''}>
              <th scope="row">{p}</th>
              {fields.map(f => <td key={f}>{fmtFigure(f, figures[p]?.[f])}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
      <table className="figures-table figures-wide">
        <caption className="sr-only">Latest figures by metric</caption>
        <thead>
          <tr>
            <th scope="col">Metric</th>
            {periods.map(p => <th scope="col" key={p} className={p.endsWith('E') ? 'is-estimate' : ''}>{p}</th>)}
          </tr>
        </thead>
        <tbody>
          {fields.map(f => (
            <tr key={f}>
              <th scope="row">{fieldLabel(f)}</th>
              {periods.map(p => (
                <td key={p} className={p.endsWith('E') ? 'is-estimate' : ''}>{fmtFigure(f, figures[p]?.[f])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </ScrollRegion>
  );
}

/** What changed between a company's last two runs. An empty delta is an answer, not a gap. */
export function DeltaList({ delta, limit }: { delta: Delta | null; limit?: number }) {
  if (!delta) return <p className="muted">Only one run so far — the next refresh will show what changed.</p>;
  if (delta.is_empty) return <p className="quiet">No material change since the previous run.</p>;
  const changes = limit ? delta.figure_changes.slice(0, limit) : delta.figure_changes;
  const more = delta.figure_changes.length - changes.length;
  return (
    <ul className="delta-list">
      {delta.verdict_change && (
        <li className="delta-item delta-verdict">
          Compliance verdict <b>{delta.verdict_change.before}</b> → <b>{delta.verdict_change.after}</b>
        </li>
      )}
      {delta.red_flag_change && (
        <li className="delta-item">
          Risk flags <b>{delta.red_flag_change.before}</b> → <b>{delta.red_flag_change.after}</b>
        </li>
      )}
      {changes.map(c => {
        const up = c.after > c.before;
        return (
          <li className="delta-item" key={`${c.period}-${c.field}`}>
            <span className="delta-field">{fieldLabel(c.field)} <span className="muted">{c.period}</span></span>
            <span className="delta-values">
              {fmtFigure(c.field, c.before)} → {fmtFigure(c.field, c.after)}
              {c.pct_change != null && <span className={up ? 'pos' : 'neg'}> {fmtPct(c.pct_change)}</span>}
            </span>
          </li>
        );
      })}
      {more > 0 && <li className="delta-item muted">+{more} more</li>}
    </ul>
  );
}

export function OutcomeBadge({ reason, verdict }: { reason?: string | null; verdict?: string | null }) {
  if (reason === 'escalated') return <span className="badge badge-escalated">escalated</span>;
  if (verdict === 'clear') return <span className="badge badge-clear">clear</span>;
  if (verdict) return <span className="badge badge-iters">{verdict}</span>;
  if (reason && reason !== 'completed') return <span className="badge badge-iters">{reason.replace(/_/g, ' ')}</span>;
  return <span className="badge badge-tools">brief</span>;
}
