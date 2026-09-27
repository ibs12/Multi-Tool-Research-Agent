import { act, cleanup, render, screen } from '@testing-library/react';
import axe from 'axe-core';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { App } from '../src/App';
import { navigate } from '../src/router';
import type { RunRecord, Sweep, WatchlistEntry } from '../src/api/types';

// Issue #45: accessibility as a regression gate, not a one-off pass. axe runs
// on each view with realistic data. Colour contrast needs real layout, which
// jsdom lacks — that part is checked in a real browser, not here.

const entry = (over: Partial<WatchlistEntry> = {}): WatchlistEntry => ({
  company_key: 'cik:320193', name: 'Apple Inc.', cik: 320193, ticker: 'AAPL',
  latest_run: { id: 'r2', created_at: '2026-09-23T22:32:00+00:00', termination_reason: 'completed',
                verdict: 'clear', figures: { FY2025: { revenue: 416.2e9, eps: 7.1 }, FY2026E: { revenue: 477.8e9 } } },
  delta: { figure_changes: [{ period: 'FY2026E', field: 'eps', before: 8.2, after: 8.82, pct_change: 7.6 }],
           verdict_change: null, red_flag_change: null, is_empty: false },
  ...over,
});

const sweep: Sweep = { id: 's1', started_at: new Date().toISOString(), finished_at: new Date().toISOString(),
                       checked: 2, refreshed: 1, notified: 1, error: null };

const escalated: RunRecord = {
  id: 'esc1', query: 'Analyse Acme', company_target: 'Acme Corp', agent_mode: 'multi',
  termination_reason: 'escalated', created_at: '2026-09-23T22:00:00+00:00',
  escalation: { reason: 'Revenue could not be verified', unresolved: ['Which fiscal year?'], evidence_summary: 'x' },
  compliance_verdict: { verdict: 'escalate', gap_type: 'unverifiable' }, tools_called: ['sec_edgar'],
};

function mockApi() {
  const routes: Record<string, unknown> = {
    '/health': { status: 'ok' },
    '/watchlist': [entry(), entry({ company_key: 'cik:2488', name: 'AMD', ticker: 'AMD', delta: null })],
    '/sweeps': [sweep],
    '/companies/cik%3A320193': { company_key: 'cik:320193', company_target: 'Apple Inc.', delta: entry().delta,
                                 runs: [entry().latest_run, { ...entry().latest_run, id: 'r1' }] },
    '/runs/esc1': escalated,
  };
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    const path = String(url).split('?')[0];
    const body = routes[path];
    return new Response(JSON.stringify(body ?? { detail: 'not found' }), { status: body ? 200 : 404 });
  }));
}

async function renderAt(url: string) {
  history.replaceState(null, '', url);
  const utils = render(<App />);
  await act(async () => { await new Promise(r => setTimeout(r, 50)); });
  return utils;
}

async function violations(root: Element) {
  const res = await axe.run(root, { rules: { 'color-contrast': { enabled: false } } });
  return res.violations.map(v => `${v.id}: ${v.nodes.map(n => n.target.join(' ')).join(', ')}`);
}

beforeEach(() => { localStorage.clear(); mockApi(); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('views pass axe (WCAG 2.1 AA, minus contrast)', () => {
  it('watchlist home, with a company needing attention', async () => {
    const { container } = await renderAt('/');
    expect(screen.getByRole('heading', { level: 1, name: 'What changed' })).toBeTruthy();
    expect(screen.getByText(/Needs your attention/)).toBeTruthy();
    expect(await violations(container)).toEqual([]);
  });

  it('company view', async () => {
    const { container } = await renderAt('/company/cik%3A320193');
    expect(screen.getByRole('heading', { level: 1, name: 'Apple Inc.' })).toBeTruthy();
    expect(await violations(container)).toEqual([]);
  });

  it('a saved escalated run is a named region, not an alert that fires on page load', async () => {
    const { container } = await renderAt('/?run=esc1');
    const card = screen.getByRole('region', { name: 'Escalated to a human' });
    expect(card.textContent).toContain('Revenue could not be verified');
    expect(screen.queryByRole('alert')).toBeNull();
    expect(await violations(container)).toEqual([]);
  });

  it('the research terminal', async () => {
    const { container } = await renderAt('/research');
    expect(screen.getByLabelText('Research Query')).toBeTruthy();
    expect(await violations(container)).toEqual([]);
  });
});

describe('navigation', () => {
  it('moves focus to the new page heading and names the tab', async () => {
    await renderAt('/');
    await act(async () => {
      navigate('/company/cik%3A320193');
      await new Promise(r => setTimeout(r, 150));
    });
    expect(document.activeElement?.tagName).toBe('H1');
    expect(document.activeElement?.textContent).toBe('Apple Inc.');
    expect(document.title).toMatch(/^Apple Inc\. — /);
  });

  it('offers a skip link to the content', async () => {
    await renderAt('/');
    const skip = screen.getByRole('link', { name: 'Skip to content' });
    expect(skip.getAttribute('href')).toBe('#content');
    expect(document.getElementById('content')).toBeTruthy();
  });
});
