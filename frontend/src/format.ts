// Display formatting. `fmtFigure` mirrors agent/deltas.py `_fmt`, so a number
// reads the same in a notification and on screen.

export const FIELD_LABELS: Record<string, string> = {
  revenue: 'Revenue',
  net_income: 'Net income',
  eps: 'EPS',
  gross_margin: 'Gross margin',
  operating_income: 'Operating income',
  free_cash_flow: 'Free cash flow',
};

export const fieldLabel = (f: string) => FIELD_LABELS[f] ?? f.replace(/_/g, ' ');

export function fmtFigure(field: string, v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—';
  if (field === 'gross_margin') return `${v.toFixed(1)}%`;
  if (field === 'eps') return `$${v.toFixed(2)}`;
  const a = Math.abs(v);
  if (a >= 1e12) return `$${(v / 1e12).toFixed(2)}T`;
  if (a >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
  return `$${v.toLocaleString('en-US', { maximumFractionDigits: 0 })}`;
}

export function fmtPct(p: number | null | undefined): string {
  if (p == null) return '';
  return `${p >= 0 ? '+' : ''}${p.toFixed(1)}%`;
}

export function fmtWhen(iso?: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}

export function fmtAgo(iso?: string | null, now = Date.now()): string {
  if (!iso) return 'never';
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  const d = Math.floor(s / 86400);
  return d === 1 ? 'yesterday' : `${d}d ago`;
}

/**
 * Periods in the brief's reading order: fiscal years ascending, and within a
 * year the reported quarters, then the full-year actual, then the estimate —
 * FY2025, Q3 FY2026, FY2026E, FY2027E.
 */
export function sortPeriods(periods: string[]): string[] {
  const key = (p: string): [number, number] => {
    const fy = p.match(/^FY(\d{4})(E?)$/);
    if (fy) return [Number(fy[1]), fy[2] ? 2 : 1];
    const q = p.match(/Q(\d)\s*FY(\d{4})/);
    if (q) return [Number(q[2]), Number(q[1]) / 10];
    return [Number.MAX_SAFE_INTEGER, 0];
  };
  return [...periods].sort((a, b) => {
    const [ya, ra] = key(a), [yb, rb] = key(b);
    return ya - yb || ra - rb || a.localeCompare(b);
  });
}
