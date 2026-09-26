import { describe, expect, it } from 'vitest';
import { href, parseRoute } from '../src/router';

describe('routes', () => {
  it('keeps the existing permalink shape working forever', () => {
    expect(parseRoute('/', '?run=K_nruMgF8_gf')).toEqual({ name: 'run', id: 'K_nruMgF8_gf' });
    expect(href({ name: 'run', id: 'a b' })).toBe('/?run=a%20b');
  });

  it('round-trips a company key containing a colon', () => {
    const h = href({ name: 'company', key: 'cik:320193' });
    expect(parseRoute(h, '')).toEqual({ name: 'company', key: 'cik:320193' });
  });

  it('carries a prefilled research query', () => {
    const h = href({ name: 'research', query: 'Analyse Apple Inc. outlook' });
    const [path, search] = h.split('?');
    expect(parseRoute(path, `?${search}`)).toEqual({ name: 'research', query: 'Analyse Apple Inc. outlook' });
  });

  it('falls back to home', () => {
    expect(parseRoute('/nope', '')).toEqual({ name: 'home' });
  });
});

import { sortPeriods } from '../src/format';

describe('period order', () => {
  it('matches the brief: actuals, then a year\'s quarters, then its estimate', () => {
    expect(sortPeriods(['FY2027E', 'Q3 FY2026', 'FY2022', 'FY2026E', 'FY2025', 'Q1 FY2026']))
      .toEqual(['FY2022', 'FY2025', 'Q1 FY2026', 'Q3 FY2026', 'FY2026E', 'FY2027E']);
  });
});
