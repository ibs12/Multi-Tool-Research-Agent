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
    return () => {
      el.querySelectorAll('canvas').forEach(c => Chart.getChart(c)?.destroy());
    };
  }, [markdown, forecast]);

  return <div className="markdown-body" ref={ref} />;
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
