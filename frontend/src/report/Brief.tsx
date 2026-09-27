import { useLayoutEffect, useRef } from 'react';
import Chart from 'chart.js/auto';
import type { Forecast } from '../api/types';
import { postProcessFinancialTables, renderFinancialCharts, renderForecastSection } from './charts.js';
import { renderMarkdown } from './markdown';

/**
 * The analyst brief. Rendered imperatively into a container React does not
 * own, because the ported chart module then rewrites that DOM (inserting charts
 * and table footnotes) — React reconciling the same nodes would fight it.
 */
export function Brief({ markdown, forecast }: { markdown: string; forecast: Forecast | null }) {
  const ref = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.innerHTML = renderMarkdown(markdown);
    // Order matters: chart extraction reads clean textContent; the table
    // post-processor adds superscripts afterwards.
    try {
      renderFinancialCharts(el);
      renderForecastSection(el, forecast);
      postProcessFinancialTables(el);
    } catch (err) {
      console.error('chart rendering failed; the brief is still shown', err);
    }
    colourVerdict(el);
    const unobserve = makeTablesReachable(el);
    return () => {
      unobserve();
      el.querySelectorAll('canvas').forEach(c => Chart.getChart(c)?.destroy());
    };
  }, [markdown, forecast]);

  return <div className="markdown-body" ref={ref} />;
}

/**
 * On phones the brief's wide tables scroll sideways (display:block), so each one
 * that actually overflows becomes a named keyboard stop
 * (axe: scrollable-region-focusable). Re-checked on resize.
 */
function makeTablesReachable(el: HTMLElement): () => void {
  const tables = Array.from(el.querySelectorAll('table'));
  const sync = () => tables.forEach((t, i) => {
    if (t.scrollWidth > t.clientWidth + 1) {
      const heading = t.closest('div, section')?.querySelector('h2, h3')?.textContent?.trim();
      t.tabIndex = 0;
      t.setAttribute('aria-label', heading ? `${heading} table` : `Table ${i + 1}`);
    } else {
      t.removeAttribute('tabindex');              // no dead tab stops where nothing scrolls
      t.removeAttribute('aria-label');
    }
  });
  sync();
  const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(sync) : null;
  tables.forEach(t => ro?.observe(t));
  return () => ro?.disconnect();
}

function colourVerdict(el: HTMLElement) {
  el.querySelectorAll('h2').forEach(h => {
    if (!h.textContent?.toLowerCase().includes('verdict')) return;
    const next = h.nextElementSibling;
    if (!next) return;
    const text = next.textContent?.toLowerCase() ?? '';
    next.classList.add(text.includes('bullish') ? 'verdict-bullish'
      : text.includes('bearish') ? 'verdict-bearish' : 'verdict-neutral');
  });
}
