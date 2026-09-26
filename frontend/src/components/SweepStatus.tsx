import type { Sweep } from '../api/types';
import { fmtAgo, fmtWhen } from '../format';

// The worker runs weekdays; the longest normal gap is Friday → Monday (~72h).
const STALE_HOURS = 80;

/**
 * Proof the refresh worker is alive (ADR-0011). Silence on the watchlist only
 * means "nothing changed" if this says the sweeps are still happening.
 */
export function SweepStatus({ sweeps }: { sweeps: Sweep[] | null }) {
  if (sweeps === null) return null;
  const last = sweeps[0];
  if (!last) {
    return <p className="sweep-status warn" role="status">No sweep recorded yet — the refresh worker has not run.</p>;
  }
  const ageH = (Date.now() - new Date(last.finished_at).getTime()) / 3.6e6;
  const stale = ageH > STALE_HOURS;
  return (
    <p className={`sweep-status ${stale || last.error ? 'warn' : ''}`} role="status" title={fmtWhen(last.finished_at)}>
      <span className="sweep-dot" aria-hidden="true" />
      Last sweep {fmtAgo(last.finished_at)} · {last.checked} checked · {last.refreshed} refreshed
      {last.error && <> · <span className="neg">error: {last.error}</span></>}
      {stale && <> · <b>overdue — check the worker</b></>}
    </p>
  );
}
