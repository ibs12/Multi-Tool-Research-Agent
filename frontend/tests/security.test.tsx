import { cleanup, render } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { Brief } from '../src/report/Brief';
import { EscalationCard } from '../src/report/EscalationCard';
import { renderMarkdown } from '../src/report/markdown';

// Issue #44 acceptance: "A report containing <script> / <img onerror> still renders inert."
// The brief is model output quoting scraped pages; this is the XSS the old
// frontend already shipped once.
const HOSTILE = [
  '## Verdict',
  'Bullish <script>window.__pwned = 1</script>',
  '<img src="x" onerror="window.__pwned = 2">',
  '<a href="javascript:window.__pwned=3">click</a>',
  '<iframe src="https://evil.example"></iframe>',
  // a table, so the ported chart module and table post-processor run on it too
  ['| Metric | FY2024 | FY2025 |',
   '|---|---|---|',
   '| Revenue <img src=x onerror="window.__pwned=4"> | $1.0B | $2.0B |'].join('\n'),
].join('\n\n');

afterEach(cleanup);

function assertInert(root: HTMLElement) {
  expect(root.querySelectorAll('script, iframe').length).toBe(0);
  root.querySelectorAll('*').forEach(el => {
    for (const a of Array.from(el.attributes)) {
      expect(a.name.startsWith('on'), `${el.tagName} has ${a.name}`).toBe(false);
      expect(a.value.trim().toLowerCase().startsWith('javascript:'), `${el.tagName} ${a.name}=${a.value}`).toBe(false);
    }
  });
  expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
}

describe('brief rendering is inert', () => {
  it('sanitises markdown output', () => {
    const div = document.createElement('div');
    div.innerHTML = renderMarkdown(HOSTILE);
    assertInert(div);
    expect(div.textContent).toContain('Bullish');
  });

  it('stays inert through the full Brief component, charts and table post-processing included', () => {
    const { container } = render(<Brief markdown={HOSTILE} forecast={null} />);
    assertInert(container);
    expect(container.querySelector('table')).not.toBeNull();
  });

  it('renders escalation text as text, never markup', () => {
    const { container } = render(
      <EscalationCard
        pkg={{ reason: '<img src=x onerror="window.__pwned=5">', unresolved: ['<script>window.__pwned=6</script>'],
               evidence_summary: '<b onmouseover="x()">hover</b>' }}
        verdict={{ gap_type: '<svg onload=alert(1)>' }} />);
    assertInert(container);
    expect(container.textContent).toContain('<img src=x onerror=');
    expect(container.textContent).toContain('Escalated to a human');
  });
});
