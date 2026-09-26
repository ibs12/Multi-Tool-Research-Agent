// Ported verbatim from the pre-SPA frontend/index.html (ADR-0013: the chart
// work is kept, not rewritten). Imperative DOM on purpose: it post-processes the
// already-sanitised brief after React has rendered it. The typed boundary is
// charts.d.ts. The only changes from the original: Chart.js comes from npm, the
// functions are exported, and forecast labels are escaped before interpolation.
import Chart from 'chart.js/auto';

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}


// ── Financial chart helpers ──────────────────────────────────────
const CURRENT_YEAR = new Date().getFullYear();

function parseFinancialValue(raw) {
  if (!raw) return null;
  let s = raw.trim().replace(/,/g, '').replace(/^[^0-9\-\.]+/, '');
  let mult = 1;
  const last = s.slice(-1).toLowerCase();
  if      (last === 'b') { mult = 1e9;  s = s.slice(0, -1); }
  else if (last === 'm') { mult = 1e6;  s = s.slice(0, -1); }
  else if (last === 't') { mult = 1e12; s = s.slice(0, -1); }
  else if (last === 'k') { mult = 1e3;  s = s.slice(0, -1); }
  s = s.replace(/%$/, '');
  const n = parseFloat(s);
  return isNaN(n) ? null : n * mult;
}

const METRIC_RE = /\b(revenue|total revenue|net revenue|eps|earnings per share|net income|gross margin|gross profit|operating income|ebitda|free cash flow|fcf)\b/i;

function isFinancialMetric(str) { return METRIC_RE.test(str); }

function isPeriodLabel(str) {
  return /\bq[1-4]\b/i.test(str) ||
         /\b(fy|cy)?\s*20\d{2}\b/i.test(str) ||
         /\b20\d{2}\s*q[1-4]\b/i.test(str);
}

// True if a period label refers to a future/estimated period
function isEstimate(label) {
  if (/\d[Ee]\s*$/.test(label.trim()))     return true;  // FY2026E, 2027e
  if (/\([Ee]\)\s*$/.test(label.trim()))   return true;  // (E) suffix
  if (/\bestimate\b/i.test(label))         return true;
  const m = label.match(/\b(20\d{2})\b/);
  return !!(m && parseInt(m[1]) > CURRENT_YEAR);
}

// True if the label is a quarterly SEC filing period (Q2 FY2026, Q1 2026, …)
// These bars render in blue to distinguish primary-source data from historical estimates.
function isSecFilingPeriod(label) {
  if (isEstimate(label)) return false;    // forward estimates are not SEC filing data
  return /\bq[1-4]\b/i.test(label);      // any Q[1-4] label that isn't an estimate
}

// ── Period granularity ───────────────────────────────────────────
// A quarterly bar (Q2 FY2026) covers three months, so it must never be
// compared against — or trend-lined into — a full-year bar.
function periodParts(label) {
  const clean = String(label).replace(/[¹²³⁴]/g, '').trim();
  const q = clean.match(/\bq([1-4])\b/i);
  const y = clean.match(/\b(20\d{2})\b/);
  return { quarter: q ? parseInt(q[1]) : null, year: y ? parseInt(y[1]) : null };
}

function isQuarterlyPeriod(label) { return periodParts(label).quarter !== null; }

// Index of the most recent earlier period covering the same span of time:
// annual → previous annual; quarterly → same quarter a year back (YoY),
// falling back to the previous quarter (QoQ). -1 when nothing is comparable.
function previousComparableIndex(labels, i) {
  const cur = periodParts(labels[i]);
  if (cur.quarter === null) {
    for (let j = i - 1; j >= 0; j--) if (periodParts(labels[j]).quarter === null) return j;
    return -1;
  }
  for (let j = i - 1; j >= 0; j--) if (periodParts(labels[j]).quarter === cur.quarter) return j;
  for (let j = i - 1; j >= 0; j--) if (periodParts(labels[j]).quarter !== null) return j;
  return -1;
}

// Charts live between the financial section and Risk Factors; fall back to
// appending when the report doesn't use the expected headings.
function insertChartSection(contentEl, section) {
  let insertBefore = null;
  const headings = Array.from(contentEl.querySelectorAll('h2'));
  for (const h of headings) {
    if (/risk/i.test(h.textContent)) { insertBefore = h; break; }
  }
  if (!insertBefore) {
    const finH2 = headings.find(h => /financial/i.test(h.textContent));
    if (finH2) {
      const i = headings.indexOf(finH2);
      if (i < headings.length - 1) insertBefore = headings[i + 1];
    }
  }
  insertBefore ? contentEl.insertBefore(section, insertBefore) : contentEl.appendChild(section);
}

function extractChartsFromTable(table) {
  const rows = Array.from(table.querySelectorAll('tr'));
  if (rows.length < 2) return [];
  const headerCells = Array.from(rows[0].querySelectorAll('th, td')).map(c => c.textContent.trim());
  if (headerCells.length < 2) return [];

  const charts = [];
  const seen = new Set();

  // Orientation A: header row has period labels in cols 1+, col 0 is metric name
  const periodCols = headerCells.slice(1).reduce((acc, h, i) => {
    if (isPeriodLabel(h)) acc.push({ label: h, colIdx: i + 1 });
    return acc;
  }, []);

  if (periodCols.length >= 2) {
    for (let r = 1; r < rows.length; r++) {
      const cells = Array.from(rows[r].querySelectorAll('th, td')).map(c => c.textContent.trim());
      if (!cells.length || !isFinancialMetric(cells[0])) continue;
      const key = cells[0].toLowerCase().replace(/\s+/g, ' ');
      if (seen.has(key)) continue;
      const pairs = periodCols
        .map(({ label, colIdx }) => ({ label, value: parseFinancialValue(cells[colIdx]) }))
        .filter(p => p.value !== null);
      if (pairs.length >= 2) {
        seen.add(key);
        charts.push({ title: cells[0], labels: pairs.map(p => p.label), values: pairs.map(p => p.value) });
      }
    }
    return charts;
  }

  // Orientation B: col 0 has period labels, header row has metric names
  const periodRows = rows.slice(1).filter(r => {
    const c = Array.from(r.querySelectorAll('th, td'));
    return c.length > 0 && isPeriodLabel(c[0].textContent.trim());
  });
  if (periodRows.length >= 2) {
    const periodLabels = periodRows.map(r => Array.from(r.querySelectorAll('th, td'))[0].textContent.trim());
    for (let c = 1; c < headerCells.length; c++) {
      const metric = headerCells[c];
      if (!isFinancialMetric(metric)) continue;
      const key = metric.toLowerCase().replace(/\s+/g, ' ');
      if (seen.has(key)) continue;
      const values = periodRows.map(r => {
        const cells = Array.from(r.querySelectorAll('th, td'));
        return parseFinancialValue(cells[c] ? cells[c].textContent : '');
      });
      const pairs = periodLabels.map((l, i) => ({ label: l, value: values[i] })).filter(p => p.value !== null);
      if (pairs.length >= 2) {
        seen.add(key);
        charts.push({ title: metric, labels: pairs.map(p => p.label), values: pairs.map(p => p.value) });
      }
    }
  }
  return charts;
}

// Detect tables with Low / Mean / High columns → grouped bar chart
function extractGroupedEstimateChart(table) {
  const rows = Array.from(table.querySelectorAll('tr'));
  if (rows.length < 2) return null;
  const hCells = Array.from(rows[0].querySelectorAll('th, td')).map(c => c.textContent.trim().toLowerCase());
  const lowIdx  = hCells.findIndex(h => h === 'low');
  const meanIdx = hCells.findIndex(h => h === 'mean');
  const highIdx = hCells.findIndex(h => h === 'high');
  if (lowIdx === -1 || meanIdx === -1 || highIdx === -1) return null;

  const labels = [], lowV = [], meanV = [], highV = [];
  for (let r = 1; r < rows.length; r++) {
    const cells = Array.from(rows[r].querySelectorAll('th, td')).map(c => c.textContent.trim());
    if (!cells.length || !isPeriodLabel(cells[0])) continue;
    const lo = parseFinancialValue(cells[lowIdx]);
    const me = parseFinancialValue(cells[meanIdx]);
    const hi = parseFinancialValue(cells[highIdx]);
    if (lo !== null || me !== null || hi !== null) {
      labels.push(cells[0]);
      lowV.push(lo); meanV.push(me); highV.push(hi);
    }
  }
  if (!labels.length) return null;
  const colHeader = Array.from(rows[0].querySelectorAll('th, td'))[0].textContent.trim();
  return {
    title: colHeader || 'Forward Estimates',
    labels,
    datasets: [
      { label: 'Low',  data: lowV,  color: '#8b6914' },
      { label: 'Mean', data: meanV, color: '#f5a623' },
      { label: 'High', data: highV, color: '#ffd066' },
    ]
  };
}

// Determine chart data provenance from surrounding DOM text
function detectDataSource(table, contentEl) {
  let near = '';
  let el = table ? table.previousElementSibling : null;
  for (let i = 0; i < 6 && el; i++) { near += el.textContent; el = el.previousElementSibling; }
  const all = (contentEl ? contentEl.textContent : '') + near;
  const monthYear = new Date().toLocaleDateString('en-US', { month: 'long', year: 'numeric' });

  if (/\[Analyst Consensus\]|Yahoo Finance/i.test(all)) {
    const nm = all.match(/(\d{1,3})\s*analysts/i);
    return {
      badge: 'YAHOO FINANCE', type: 'yahoo',
      subtitle: `Source: Yahoo Finance Analyst Consensus${nm ? ' — ' + nm[1] + ' analysts' : ''} | Retrieved ${monthYear}`
    };
  }
  if (/\[SEC Filing\]|10-K|10-Q|EDGAR/i.test(all)) {
    return {
      badge: 'SEC FILING', type: 'sec',
      subtitle: `Source: SEC EDGAR 10-K / 10-Q [SEC Filing] | ${monthYear}`
    };
  }
  return { badge: 'LIVE DATA', type: 'live', subtitle: '' };
}

// ── Per-chart inline plugins (closures capture chart-specific data) ───

function makeValueLabelPlugin(displayValues, labels, unit) {
  return {
    afterDatasetsDraw(chart) {
      const meta = chart.getDatasetMeta(0);
      if (!meta || meta.type !== 'bar') return;
      const { ctx } = chart;
      ctx.save();
      ctx.font = '500 10px "IBM Plex Mono"';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'bottom';
      meta.data.forEach((bar, i) => {
        if (displayValues[i] == null) return;
        ctx.fillStyle = isEstimate(labels[i])        ? 'rgba(245,166,35,0.7)' :
                        isSecFilingPeriod(labels[i]) ? '#64b5f6'              :
                        '#e8edf5';
        ctx.fillText('$' + displayValues[i] + unit, bar.x, bar.y - 3);
      });
      ctx.restore();
    }
  };
}

function makeGrowthBadgePlugin(displayValues, labels) {
  return {
    afterDatasetsDraw(chart) {
      const meta = chart.getDatasetMeta(0);
      if (!meta || meta.type !== 'bar') return;
      const { ctx, chartArea } = chart;
      if (!chartArea) return;
      ctx.save();
      ctx.font = '500 9px "IBM Plex Mono"';
      meta.data.forEach((bar, i) => {
        // Compare against the previous period of the same length, not the
        // bar to the left — a quarter next to a full year is not growth.
        const j = previousComparableIndex(labels, i);
        if (j < 0) return;
        const prev = displayValues[j], curr = displayValues[i];
        if (prev == null || curr == null || prev === 0) return;
        const pct = ((curr - prev) / Math.abs(prev)) * 100;
        const text = (pct >= 0 ? '+' : '') + pct.toFixed(1) + '%';
        const isPos = pct >= 0;
        const tw = ctx.measureText(text).width;
        const bw = tw + 10, bh = 14;
        const bx = bar.x - bw / 2;
        const by = bar.y - 30;
        if (by < chartArea.top) return;

        // Badge pill background
        ctx.fillStyle = isPos ? 'rgba(0,208,132,0.18)' : 'rgba(255,71,87,0.18)';
        const r = 2;
        ctx.beginPath();
        ctx.moveTo(bx + r, by); ctx.lineTo(bx + bw - r, by);
        ctx.quadraticCurveTo(bx + bw, by, bx + bw, by + r);
        ctx.lineTo(bx + bw, by + bh - r);
        ctx.quadraticCurveTo(bx + bw, by + bh, bx + bw - r, by + bh);
        ctx.lineTo(bx + r, by + bh);
        ctx.quadraticCurveTo(bx, by + bh, bx, by + bh - r);
        ctx.lineTo(bx, by + r);
        ctx.quadraticCurveTo(bx, by, bx + r, by);
        ctx.closePath();
        ctx.fill();

        ctx.fillStyle = isPos ? '#00d084' : '#ff4757';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(text, bar.x, by + bh / 2);
      });
      ctx.restore();
    }
  };
}

// Band + caption behind quarterly bars in a mixed annual/quarterly series,
// so a three-month figure is never read as a collapse in the annual trend.
function makeQuarterRegionPlugin(isQuarter) {
  return {
    beforeDatasetsDraw(chart) {
      const meta = chart.getDatasetMeta(0);
      const { ctx, chartArea } = chart;
      if (!meta || !meta.data.length || !chartArea) return;
      const xs = meta.data.map(b => b.x);
      const leftEdge  = i => (i === 0 ? chartArea.left : (xs[i - 1] + xs[i]) / 2);
      const rightEdge = i => (i === xs.length - 1 ? chartArea.right : (xs[i] + xs[i + 1]) / 2);

      ctx.save();
      for (let i = 0; i < isQuarter.length && i < xs.length; i++) {
        if (!isQuarter[i]) continue;
        let j = i;
        while (j + 1 < isQuarter.length && isQuarter[j + 1]) j++;   // contiguous run
        const l = leftEdge(i), r = rightEdge(j);

        ctx.fillStyle = 'rgba(33,150,243,0.06)';
        ctx.fillRect(l, chartArea.top, r - l, chartArea.bottom - chartArea.top);

        ctx.strokeStyle = 'rgba(33,150,243,0.35)';
        ctx.lineWidth = 1;
        ctx.setLineDash([4, 4]);
        ctx.beginPath();
        ctx.moveTo(l, chartArea.top); ctx.lineTo(l, chartArea.bottom);
        ctx.moveTo(r, chartArea.top); ctx.lineTo(r, chartArea.bottom);
        ctx.stroke();
        ctx.setLineDash([]);

        ctx.fillStyle = 'rgba(100,181,246,0.85)';
        ctx.font = '500 9px "IBM Plex Mono"';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';
        ctx.fillText('QUARTER (3-MO)', (l + r) / 2, chartArea.top + 2);

        i = j;
      }
      ctx.restore();
    }
  };
}

let chartInstances = [];

function renderBarChart(canvas, cd) {
  const maxAbs = Math.max(...cd.values.map(Math.abs));
  let divisor = 1, unit = '';
  if      (maxAbs >= 1e9)  { divisor = 1e9;  unit = 'B'; }
  else if (maxAbs >= 1e6)  { divisor = 1e6;  unit = 'M'; }
  else if (maxAbs >= 1e3)  { divisor = 1e3;  unit = 'K'; }
  const displayValues = cd.values.map(v => +(v / divisor).toFixed(2));

  // Mixed annual + quarterly series: the trend line follows the full-year
  // bars only; quarterly points are plotted as standalone markers.
  const quarterFlags  = cd.labels.map(isQuarterlyPeriod);
  const mixedPeriods  = quarterFlags.some(Boolean) && quarterFlags.some(q => !q);
  const trendValues   = displayValues.map((v, i) => (mixedPeriods && quarterFlags[i]) ? null : v);
  const quarterValues = mixedPeriods ? displayValues.map((v, i) => quarterFlags[i] ? v : null) : null;

  const instance = new Chart(canvas, {
    type: 'bar',
    data: {
      labels: cd.labels,
      datasets: [
        {
          type: 'bar',
          data: displayValues,
          // Gradient for historical bars; dimmed for estimates
          backgroundColor(ctx) {
            const chart = ctx.chart;
            const { ctx: c, chartArea } = chart;
            if (!chartArea) return '#f5a623';
            const lbl = cd.labels[ctx.dataIndex];
            if (isEstimate(lbl)) return 'rgba(245,166,35,0.32)';
            if (isSecFilingPeriod(lbl)) {
              const g = c.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
              g.addColorStop(0, '#2196f3');
              g.addColorStop(1, '#1565c0');
              return g;
            }
            const g = c.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
            g.addColorStop(0, '#f5a623');
            g.addColorStop(1, '#c8841a');
            return g;
          },
          borderColor: cd.labels.map(l =>
            isEstimate(l)        ? 'rgba(245,166,35,0.65)' :
            isSecFilingPeriod(l) ? 'rgba(33,150,243,0.8)'  :
            'transparent'),
          borderWidth: cd.labels.map(l => (isEstimate(l) || isSecFilingPeriod(l)) ? 1.5 : 0),
          borderRadius: 3,
          borderSkipped: 'bottom',
          order: 1,
        },
        {
          // Smooth trend line over comparable (same-length) periods
          type: 'line',
          label: 'Trend',
          data: trendValues,
          spanGaps: false,
          borderColor: '#00d084',
          borderWidth: 2,
          tension: 0.4,
          pointBackgroundColor: '#00d084',
          pointBorderColor: '#0f1318',
          pointBorderWidth: 1.5,
          pointRadius: 4,
          pointHoverRadius: 5,
          fill: false,
          order: 0,
        },
        ...(quarterValues ? [{
          // Quarterly points: markers only, never joined to the annual trend
          type: 'line',
          label: 'Quarter',
          data: quarterValues,
          showLine: false,
          borderColor: 'transparent',
          pointBackgroundColor: '#2196f3',
          pointBorderColor: '#0f1318',
          pointBorderWidth: 1.5,
          pointRadius: 4,
          pointHoverRadius: 5,
          order: 0,
        }] : [])
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      layout: { padding: { top: 40 } },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#0a0c0f',
          borderColor: '#1e2a3a',
          borderWidth: 1,
          titleColor: '#7a8fa8',
          bodyColor: '#e8edf5',
          titleFont: { family: 'IBM Plex Mono', size: 10 },
          bodyFont: { family: 'IBM Plex Mono', size: 11 },
          callbacks: {
            label(ctx) {
              const lbl = cd.labels[ctx.dataIndex];
              if (ctx.dataset.label === 'Trend')   return `  Trend: $${ctx.parsed.y}${unit}`;
              if (ctx.dataset.label === 'Quarter') return `  Quarter: $${ctx.parsed.y}${unit}`;
              const sfx = isEstimate(lbl)                              ? 'E'                       :
                          isSecFilingPeriod(lbl)                       ? ' (SEC · 3-month period)' :
                          '';
              return `  $${ctx.parsed.y}${unit}${sfx}`;
            },
            afterBody(items) {
              const i = items && items.length ? items[0].dataIndex : -1;
              if (i < 0 || !quarterFlags[i]) return [];
              return ['  3-month figure — not comparable to full-year bars'];
            }
          }
        }
      },
      scales: {
        x: {
          grid: { color: '#1e2a3a', lineWidth: 0.5 },
          border: { display: false },
          ticks: { color: '#7a8fa8', font: { family: 'IBM Plex Mono', size: 10 } },
        },
        y: {
          grid: { color: '#1e2a3a', lineWidth: 0.5 },
          border: { display: false },
          ticks: {
            color: '#7a8fa8',
            font: { family: 'IBM Plex Mono', size: 10 },
            callback: v => '$' + v + unit,
          }
        }
      },
      animation: { duration: 700, easing: 'easeOutQuart' },
    },
    plugins: [
      ...(mixedPeriods ? [makeQuarterRegionPlugin(quarterFlags)] : []),
      makeValueLabelPlugin(displayValues, cd.labels, unit),
      makeGrowthBadgePlugin(displayValues, cd.labels),
    ]
  });
  chartInstances.push(instance);
}

function renderGroupedChart(canvas, cd) {
  const allVals = cd.datasets.flatMap(d => d.data.filter(v => v !== null));
  const maxAbs = Math.max(...allVals.map(Math.abs));
  let divisor = 1, unit = '';
  if      (maxAbs >= 1e9)  { divisor = 1e9;  unit = 'B'; }
  else if (maxAbs >= 1e6)  { divisor = 1e6;  unit = 'M'; }
  else if (maxAbs >= 1e3)  { divisor = 1e3;  unit = 'K'; }

  const instance = new Chart(canvas, {
    type: 'bar',
    data: {
      labels: cd.labels,
      datasets: cd.datasets.map(ds => ({
        label: ds.label,
        data: ds.data.map(v => v !== null ? +(v / divisor).toFixed(2) : null),
        backgroundColor: ds.color,
        borderRadius: 3,
        borderSkipped: 'bottom',
      }))
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      layout: { padding: { top: 20 } },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#0a0c0f',
          borderColor: '#1e2a3a',
          borderWidth: 1,
          titleColor: '#7a8fa8',
          bodyColor: '#e8edf5',
          titleFont: { family: 'IBM Plex Mono', size: 10 },
          bodyFont: { family: 'IBM Plex Mono', size: 11 },
          callbacks: { label: ctx => `  ${ctx.dataset.label}: $${ctx.parsed.y}${unit}` }
        }
      },
      scales: {
        x: {
          grid: { color: '#1e2a3a', lineWidth: 0.5 },
          border: { display: false },
          ticks: { color: '#7a8fa8', font: { family: 'IBM Plex Mono', size: 10 } },
        },
        y: {
          grid: { color: '#1e2a3a', lineWidth: 0.5 },
          border: { display: false },
          ticks: {
            color: '#7a8fa8',
            font: { family: 'IBM Plex Mono', size: 10 },
            callback: v => '$' + v + unit,
          }
        }
      },
      animation: { duration: 700, easing: 'easeOutQuart' },
    }
  });
  chartInstances.push(instance);
}

function renderFinancialCharts(contentEl) {
  chartInstances.forEach(c => c.destroy());
  chartInstances = [];

  // ── 1. Extract all chart data from rendered tables ──────────────
  const allCharts = [];
  const seen = new Set();
  contentEl.querySelectorAll('table').forEach(table => {
    const grouped = extractGroupedEstimateChart(table);
    if (grouped) {
      const key = (grouped.title || 'grouped').toLowerCase();
      if (!seen.has(key)) { seen.add(key); allCharts.push({ ...grouped, isGrouped: true, table }); }
      return;
    }
    extractChartsFromTable(table).forEach(cd => {
      const key = cd.title.toLowerCase().replace(/\s+/g, ' ');
      if (!seen.has(key)) { seen.add(key); allCharts.push({ ...cd, isGrouped: false, table }); }
    });
  });
  if (!allCharts.length) return;

  // ── 2. Group non-grouped metrics by source table ─────────────────
  // Each table → one chart block (tabbed if 2+ metrics, plain if 1).
  // Grouped (Low/Mean/High) charts always get their own block.
  const blocks = [];
  const tableMap = new Map();
  allCharts.forEach(cd => {
    if (cd.isGrouped) {
      blocks.push({ isGrouped: true, metrics: [cd], table: cd.table });
    } else {
      if (!tableMap.has(cd.table)) tableMap.set(cd.table, []);
      tableMap.get(cd.table).push(cd);
    }
  });
  tableMap.forEach((metrics, table) => blocks.push({ isGrouped: false, metrics, table }));

  const section = document.createElement('div');
  section.className = 'chart-section';

  const analystMatch = contentEl ? contentEl.textContent.match(/(\d{1,3})\s+analysts/i) : null;
  const analystStr   = analystMatch ? ` (${analystMatch[1]} analysts)` : '';

  blocks.forEach((blk, blkIdx) => {
    const cd0      = blk.metrics[0];
    const src      = detectDataSource(blk.table, contentEl);
    const isTabbed = !blk.isGrouped && blk.metrics.length > 1;

    const block = document.createElement('div');
    block.className = 'chart-block';

    // ── Header ───────────────────────────────────────────────────
    const hdr = document.createElement('div');
    hdr.className = 'chart-header';
    const titleEl = document.createElement('div');
    titleEl.className = 'chart-block-title';
    titleEl.textContent = isTabbed ? 'Financial Metrics' : cd0.title;
    const bdg = document.createElement('div');
    bdg.className = `chart-source-badge badge-${src.type}`;
    bdg.textContent = src.badge;
    hdr.appendChild(titleEl);
    hdr.appendChild(bdg);
    block.appendChild(hdr);

    if (src.subtitle) {
      const sub = document.createElement('div');
      sub.className = 'chart-source-subtitle';
      sub.textContent = src.subtitle;
      block.appendChild(sub);
    }

    // ── Legend ───────────────────────────────────────────────────
    const anyEst    = blk.metrics.some(cd => cd.labels && cd.labels.some(l => isEstimate(l)));
    const anySecBar = blk.metrics.some(cd => cd.labels && cd.labels.some(l => isSecFilingPeriod(l)));
    const anyQtr    = blk.metrics.some(cd => cd.labels &&
                        cd.labels.some(l => isQuarterlyPeriod(l)) &&
                        cd.labels.some(l => !isQuarterlyPeriod(l)));
    if (anyEst || anySecBar || blk.isGrouped) {
      const leg = document.createElement('div');
      leg.className = 'chart-legend';
      if (blk.isGrouped) {
        leg.innerHTML = '<span style="color:#8b6914">■ Low</span><span style="color:#f5a623">■ Mean</span><span style="color:#ffd066">■ High</span>';
      } else {
        let lhtml = '<span class="legend-actual">■ Actual</span>';
        if (anySecBar) lhtml += `<span style="color:var(--blue)">■ SEC Filing${anyQtr ? ' (quarter, 3-mo)' : ''}</span>`;
        if (anyEst)    lhtml += '<span class="legend-estimate">▪ Estimate</span>';
        lhtml += `<span class="legend-trend">— Trend${anyQtr ? ' (full years)' : ''}</span>`;
        leg.innerHTML = lhtml;
      }
      block.appendChild(leg);
    }

    // ── Tabs + canvases ──────────────────────────────────────────
    const wraps = [];
    if (isTabbed) {
      const tabRow  = document.createElement('div');
      tabRow.className = 'chart-tabs';
      const tabBtns = [];

      blk.metrics.forEach((cd, mIdx) => {
        const btn = document.createElement('button');
        btn.className = 'chart-tab' + (mIdx === 0 ? ' active' : '');
        btn.textContent = cd.title;
        tabRow.appendChild(btn);
        tabBtns.push(btn);
      });
      block.appendChild(tabRow);

      blk.metrics.forEach((cd, mIdx) => {
        const wrap = document.createElement('div');
        wrap.className = 'chart-canvas-wrap';
        if (mIdx !== 0) wrap.style.display = 'none';
        const canvas = document.createElement('canvas');
        canvas.id = `fin-chart-${blkIdx}-${mIdx}-${Date.now()}`;
        wrap.appendChild(canvas);
        block.appendChild(wrap);
        wraps.push(wrap);
      });

      // Tab clicks: toggle active class + show/hide the matching canvas wrap
      tabBtns.forEach((btn, i) => {
        btn.addEventListener('click', () => {
          tabBtns.forEach((t, j) => t.classList.toggle('active', i === j));
          wraps.forEach((w, j) => { w.style.display = i === j ? 'block' : 'none'; });
        });
      });
    } else {
      const wrap = document.createElement('div');
      wrap.className = 'chart-canvas-wrap';
      const canvas = document.createElement('canvas');
      canvas.id = `fin-chart-${blkIdx}-0-${Date.now()}`;
      wrap.appendChild(canvas);
      block.appendChild(wrap);
      wraps.push(wrap);
    }

    // ── Footnote ─────────────────────────────────────────────────
    const foot = document.createElement('div');
    foot.className = 'chart-footnote';
    foot.textContent =
      `Historical data: Yahoo Finance | Current period: SEC Filing | ` +
      `Forward estimates: Analyst Consensus${analystStr} | ` +
      (anyQtr ? `Shaded band = quarterly (3-month) figure, not comparable to full-year bars | ` : '') +
      `All figures subject to restatement`;
    block.appendChild(foot);

    section.appendChild(block);
  });

  insertChartSection(contentEl, section);

  // Render all charts (canvases are in DOM order: all metrics of block 0, then block 1, …)
  const allCanvases = Array.from(section.querySelectorAll('canvas'));
  let cIdx = 0;
  blocks.forEach(blk => {
    blk.metrics.forEach(cd => {
      const canvas = allCanvases[cIdx++];
      if (!canvas) return;
      blk.isGrouped ? renderGroupedChart(canvas, cd) : renderBarChart(canvas, cd);
    });
  });
}

// ── Forward outlook ──────────────────────────────────────────────────
// Driven by the structured `forecast` payload from the API, not by parsing
// the report markdown: every figure here is the number Yahoo returned.

let forecastChartsRendered = 0;

function fmtMoney(v, m, cur) {
  if (v == null) return '—';
  const sym = cur === 'USD' ? '$' : cur === 'EUR' ? '€' : cur === 'GBP' ? '£' : (cur ? cur + ' ' : '');
  const scaled = v / (m.scale || 1);
  return `${sym}${scaled.toFixed(m.decimals != null ? m.decimals : 2)}${m.unit || ''}`;
}

function fmtPct(frac, digits) {
  if (frac == null) return '—';
  return `${frac >= 0 ? '+' : ''}${(frac * 100).toFixed(digits == null ? 1 : digits)}%`;
}

// Bars + low/high whiskers + YoY badges for one metric's quarterly series.
function makeForecastOverlayPlugin(points, fmt) {
  return {
    afterDatasetsDraw(chart) {
      const meta = chart.getDatasetMeta(0);
      const { ctx, chartArea, scales } = chart;
      if (!meta || !chartArea || !scales.y) return;
      const y = scales.y;

      ctx.save();
      meta.data.forEach((bar, i) => {
        const p = points[i];
        if (!p) return;
        let topY = bar.y;

        // Consensus range: analysts' low-to-high on estimated quarters only
        if (p.low != null && p.high != null) {
          const yLow  = y.getPixelForValue(p.low);
          const yHigh = y.getPixelForValue(p.high);
          const cap   = Math.min(9, Math.max(5, bar.width * 0.22));
          ctx.strokeStyle = 'rgba(245,166,35,0.85)';
          ctx.lineWidth = 1.25;
          ctx.beginPath();
          ctx.moveTo(bar.x, yHigh); ctx.lineTo(bar.x, yLow);
          ctx.moveTo(bar.x - cap, yHigh); ctx.lineTo(bar.x + cap, yHigh);
          ctx.moveTo(bar.x - cap, yLow);  ctx.lineTo(bar.x + cap, yLow);
          ctx.stroke();
          topY = Math.min(topY, yHigh);
        }

        // Value label, lifted above the whisker so the two never overlap
        ctx.font = '500 10px "IBM Plex Mono"';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'bottom';
        ctx.fillStyle = p.kind === 'estimate' ? 'rgba(245,166,35,0.85)' : '#e8edf5';
        ctx.fillText(fmt(p.value) + (p.kind === 'estimate' ? 'E' : ''), bar.x, topY - 4);

        // YoY badge — always against the same quarter a year earlier
        if (p.yoy == null) return;
        const text = fmtPct(p.yoy);
        const isPos = p.yoy >= 0;
        ctx.font = '500 9px "IBM Plex Mono"';
        const bw = ctx.measureText(text).width + 10, bh = 14;
        const bx = bar.x - bw / 2, by = topY - 32;
        if (by < chartArea.top) return;
        ctx.fillStyle = isPos ? 'rgba(0,208,132,0.18)' : 'rgba(255,71,87,0.18)';
        const r = 2;
        ctx.beginPath();
        ctx.moveTo(bx + r, by); ctx.lineTo(bx + bw - r, by);
        ctx.quadraticCurveTo(bx + bw, by, bx + bw, by + r);
        ctx.lineTo(bx + bw, by + bh - r);
        ctx.quadraticCurveTo(bx + bw, by + bh, bx + bw - r, by + bh);
        ctx.lineTo(bx + r, by + bh);
        ctx.quadraticCurveTo(bx, by + bh, bx, by + bh - r);
        ctx.lineTo(bx, by + r);
        ctx.quadraticCurveTo(bx, by, bx + r, by);
        ctx.closePath();
        ctx.fill();
        ctx.fillStyle = isPos ? '#00d084' : '#ff4757';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(text, bar.x, by + bh / 2);
      });
      ctx.restore();
    }
  };
}

function renderForecastChart(canvas, metric, currency) {
  const scale = metric.scale || 1;
  const fmt   = v => fmtMoney(v * scale, metric, currency);

  // Everything is charted in display units so the whiskers share the y-scale
  const points = metric.quarters.map(q => ({
    kind:  q.kind,
    value: q.value / scale,
    low:   q.low  != null ? q.low  / scale : null,
    high:  q.high != null ? q.high / scale : null,
    yoy:   q.yoy != null ? q.yoy : null,
    analysts: q.analysts,
    year_ago: q.year_ago != null ? q.year_ago / scale : null,
  }));

  // Reported quarters get a YoY badge when the year-ago quarter is on-chart
  const labels = metric.quarters.map(q => q.label);
  points.forEach((p, i) => {
    if (p.kind !== 'actual' || p.yoy != null) return;
    const j = previousComparableIndex(labels, i);
    if (j >= 0 && points[j].value) p.yoy = (p.value - points[j].value) / Math.abs(points[j].value);
  });

  const instance = new Chart(canvas, {
    type: 'bar',
    data: {
      labels,
      datasets: [{
        data: points.map(p => +p.value.toFixed(4)),
        backgroundColor(ctx) {
          const { ctx: c, chartArea } = ctx.chart;
          if (!chartArea) return '#f5a623';
          if (points[ctx.dataIndex].kind === 'estimate') return 'rgba(245,166,35,0.30)';
          const g = c.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
          g.addColorStop(0, '#f5a623');
          g.addColorStop(1, '#c8841a');
          return g;
        },
        borderColor: points.map(p => p.kind === 'estimate' ? 'rgba(245,166,35,0.75)' : 'transparent'),
        borderWidth: points.map(p => p.kind === 'estimate' ? 1.5 : 0),
        borderRadius: 3,
        borderSkipped: 'bottom',
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      layout: { padding: { top: 44 } },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#0a0c0f',
          borderColor: '#1e2a3a',
          borderWidth: 1,
          titleColor: '#7a8fa8',
          bodyColor: '#e8edf5',
          titleFont: { family: 'IBM Plex Mono', size: 10 },
          bodyFont: { family: 'IBM Plex Mono', size: 11 },
          callbacks: {
            label(ctx) {
              const p = points[ctx.dataIndex];
              return p.kind === 'estimate'
                ? `  Consensus ${fmt(p.value)}`
                : `  Reported ${fmt(p.value)}`;
            },
            afterBody(items) {
              const p = points[items[0].dataIndex];
              const out = [];
              if (p.low != null && p.high != null) {
                out.push(`  Range ${fmt(p.low)} – ${fmt(p.high)}`);
              }
              if (p.analysts) out.push(`  ${p.analysts} analysts`);
              if (p.year_ago != null) out.push(`  Year ago ${fmt(p.year_ago)}`);
              if (p.yoy != null) out.push(`  ${fmtPct(p.yoy)} YoY`);
              return out;
            }
          }
        }
      },
      scales: {
        x: {
          grid: { color: '#1e2a3a', lineWidth: 0.5 },
          border: { display: false },
          ticks: {
            color: '#7a8fa8',
            font: { family: 'IBM Plex Mono', size: 10 },
            callback(v, i) { return labels[i] + (points[i].kind === 'estimate' ? 'E' : ''); },
          },
        },
        y: {
          beginAtZero: true,
          grid: { color: '#1e2a3a', lineWidth: 0.5 },
          border: { display: false },
          ticks: {
            color: '#7a8fa8',
            font: { family: 'IBM Plex Mono', size: 10 },
            callback: v => fmt(v),
          }
        }
      },
      animation: { duration: 700, easing: 'easeOutQuart' },
    },
    plugins: [makeForecastOverlayPlugin(points, fmt)]
  });
  chartInstances.push(instance);
}

// Horizontal build-up of the fiscal year: reported quarters, then the
// quarters still to come, measured against the full-year consensus band.
function buildFyPath(metric, currency, fyLabel) {
  const fy = metric.fy;
  const ytd = metric.ytd && metric.ytd.value;
  if (ytd == null || !fy) return null;

  const fwdQuarters = metric.quarters.filter(
    q => q.kind === 'estimate' && (metric.forecast.labels || []).includes(q.label));
  const fwdTotal = metric.forecast.value || 0;
  const balance  = metric.balance;
  const pathTotal = ytd + fwdTotal + (metric.uncovered_quarters > 0 && balance > 0 ? balance : 0);
  const axisMax  = Math.max(pathTotal, fy.high || 0, fy.value) * 1.04;
  const pct = v => Math.max(0, (v / axisMax) * 100);

  const wrap = document.createElement('div');
  wrap.className = 'fy-path';

  const head = document.createElement('div');
  head.className = 'fy-path-head';
  const gapPct = fy.value ? ((ytd + fwdTotal - fy.value) / Math.abs(fy.value)) * 100 : null;
  head.innerHTML =
    `<span class="fy-path-title">${esc(fyLabel)} path — reported + rest of year</span>` +
    `<span class="fy-path-total">${fmtMoney(ytd + fwdTotal, metric, currency)} tracked ` +
    `vs ${fmtMoney(fy.value, metric, currency)} full-year consensus</span>`;
  wrap.appendChild(head);

  const bar = document.createElement('div');
  bar.className = 'fy-path-bar';

  const seg = (cls, value, title) => {
    const d = document.createElement('div');
    d.className = `fy-seg ${cls}`;
    d.style.width = pct(value) + '%';
    d.title = title;
    return d;
  };

  bar.appendChild(seg('fy-seg-actual', ytd,
    `Reported: ${(metric.ytd.labels || []).join(', ')} = ${fmtMoney(ytd, metric, currency)}`));
  fwdQuarters.forEach(q => {
    bar.appendChild(seg('fy-seg-est', q.value,
      `${q.label} consensus ${fmtMoney(q.value, metric, currency)}` +
      (q.low != null ? ` (${fmtMoney(q.low, metric, currency)} – ${fmtMoney(q.high, metric, currency)})` : '') +
      (q.analysts ? `, ${q.analysts} analysts` : '')));
  });
  if (metric.uncovered_quarters > 0 && balance > 0) {
    bar.appendChild(seg('fy-seg-balance', balance,
      `${metric.uncovered_quarters} further quarter(s) not individually forecast — ` +
      `${fmtMoney(balance, metric, currency)} implied by the full-year consensus`));
  }

  // Full-year consensus envelope overlaid on the same scale
  if (fy.low != null && fy.high != null) {
    const band = document.createElement('div');
    band.className = 'fy-band';
    band.style.left  = pct(fy.low) + '%';
    band.style.width = Math.max(0.4, pct(fy.high) - pct(fy.low)) + '%';
    bar.appendChild(band);
  }
  const mark = document.createElement('div');
  mark.className = 'fy-mark';
  mark.style.left = pct(fy.value) + '%';
  mark.title = `${fy.label} consensus mean ${fmtMoney(fy.value, metric, currency)}`;
  bar.appendChild(mark);
  wrap.appendChild(bar);

  const key = document.createElement('div');
  key.className = 'fy-key';
  const items = [
    `<span class="fy-key-item">Reported ${(metric.ytd.labels || []).length}Q: <b>${fmtMoney(ytd, metric, currency)}</b></span>`,
    `<span class="fy-key-item">Rest of year (consensus): <b>${fmtMoney(fwdTotal, metric, currency)}</b></span>`,
    `<span class="fy-key-item">${esc(fy.label)} consensus: <b>${fmtMoney(fy.value, metric, currency)}</b>` +
      (fy.low != null ? ` (${fmtMoney(fy.low, metric, currency)} – ${fmtMoney(fy.high, metric, currency)})` : '') + `</span>`,
  ];
  if (fy.yoy != null) items.push(`<span class="fy-key-item">Implied YoY: <b>${fmtPct(fy.yoy)}</b></span>`);
  if (metric.run_rate) {
    const vs = (fy.value - metric.run_rate) / Math.abs(metric.run_rate);
    items.push(`<span class="fy-key-item">vs trailing 4Q run-rate: <b>${fmtPct(vs)}</b></span>`);
  }
  key.innerHTML = items.join('');
  wrap.appendChild(key);

  const notes = [];
  if (metric.uncovered_quarters > 0) {
    notes.push(
      `${metric.uncovered_quarters} quarter(s) of ${fyLabel} carry no individual consensus — ` +
      `shown as the balance implied by the full-year figure.`);
  } else if (gapPct != null && Math.abs(gapPct) >= 0.05) {
    notes.push(
      `Quarterly estimates sum ${gapPct >= 0 ? 'above' : 'below'} the full-year consensus by ` +
      `${Math.abs(gapPct).toFixed(1)}% — analysts' quarterly and annual models differ slightly.`);
  }
  if (notes.length) {
    const n = document.createElement('div');
    n.className = 'fy-note';
    n.textContent = notes.join(' ');
    wrap.appendChild(n);
  }
  return wrap;
}

function renderForecastSection(contentEl, fc) {
  if (!fc || !Array.isArray(fc.metrics) || !fc.metrics.length) return;
  const metrics = fc.metrics.filter(m => m.quarters && m.quarters.some(q => q.kind === 'estimate'));
  if (!metrics.length) return;

  const cur     = fc.currency || 'USD';
  const fyLabel = (fc.fy_label || '').replace(/E$/, '') || 'the fiscal year';

  const section = document.createElement('div');
  section.className = 'chart-section';

  const block = document.createElement('div');
  block.className = 'chart-block';

  const hdr = document.createElement('div');
  hdr.className = 'chart-header';
  hdr.innerHTML =
    `<div class="chart-block-title">Forward Outlook — ${esc(fyLabel)}</div>` +
    `<div class="chart-source-badge badge-yahoo">Consensus</div>`;
  block.appendChild(hdr);

  const primary  = metrics[0];
  const fyAnalysts = primary.fy && primary.fy.analysts;
  const sub = document.createElement('div');
  sub.className = 'chart-source-subtitle';
  sub.textContent =
    `Reported quarters plus analyst consensus for the remainder of ${fyLabel}` +
    (fyAnalysts ? ` — ${fyAnalysts} analysts on the full year` : '') +
    ` | Yahoo Finance, retrieved ${fc.as_of}`;
  block.appendChild(sub);

  const leg = document.createElement('div');
  leg.className = 'chart-legend';
  leg.innerHTML =
    '<span class="legend-actual">■ Reported</span>' +
    '<span class="legend-estimate">▪ Consensus estimate</span>' +
    '<span style="color:rgba(245,166,35,0.85)">├ Analyst low–high</span>' +
    '<span style="color:var(--text-primary)">│ Full-year consensus</span>';
  block.appendChild(leg);

  // Tabs when more than one metric has a forecast (Revenue / EPS)
  const wraps = [];
  const stamp = Date.now();
  let tabBtns = [];
  if (metrics.length > 1) {
    const tabRow = document.createElement('div');
    tabRow.className = 'chart-tabs';
    metrics.forEach((m, i) => {
      const btn = document.createElement('button');
      btn.className = 'chart-tab' + (i === 0 ? ' active' : '');
      btn.textContent = m.label;
      tabRow.appendChild(btn);
      tabBtns.push(btn);
    });
    block.appendChild(tabRow);
  }

  metrics.forEach((m, i) => {
    const pane = document.createElement('div');
    if (i !== 0) pane.style.display = 'none';

    const cw = document.createElement('div');
    cw.className = 'chart-canvas-wrap';
    const canvas = document.createElement('canvas');
    canvas.id = `forecast-chart-${forecastChartsRendered++}-${stamp}`;
    cw.appendChild(canvas);
    pane.appendChild(cw);

    const path = buildFyPath(m, cur, fyLabel);
    if (path) pane.appendChild(path);

    block.appendChild(pane);
    wraps.push({ pane, canvas, metric: m });
  });

  tabBtns.forEach((btn, i) => {
    btn.addEventListener('click', () => {
      tabBtns.forEach((t, j) => t.classList.toggle('active', i === j));
      wraps.forEach((w, j) => { w.pane.style.display = i === j ? 'block' : 'none'; });
    });
  });

  // ── What the forecast rests on ───────────────────────────────────
  if (Array.isArray(fc.drivers) && fc.drivers.length) {
    const dt = document.createElement('div');
    dt.className = 'drivers-title';
    dt.textContent = 'What this forecast rests on';
    block.appendChild(dt);

    const grid = document.createElement('div');
    grid.className = 'forecast-drivers';
    fc.drivers.forEach(d => {
      const el = document.createElement('div');
      el.className = 'driver';
      // Green/red only where the sign means growth — a model-reconciliation
      // gap is neither good nor bad, so it stays in neutral ink.
      const signed = d.tone === 'growth' && /^[+-]/.test(d.value || '');
      const cls = signed ? (d.value.startsWith('+') ? ' pos' : ' neg') : '';
      el.innerHTML =
        `<div class="driver-label"></div><div class="driver-value${cls}"></div>` +
        `<div class="driver-detail"></div>`;
      el.querySelector('.driver-label').textContent  = d.label || '';
      el.querySelector('.driver-value').textContent  = d.value || '';
      el.querySelector('.driver-detail').textContent = d.detail || '';
      grid.appendChild(el);
    });
    block.appendChild(grid);
  }

  const foot = document.createElement('div');
  foot.className = 'chart-footnote';
  foot.textContent =
    `Reported quarters: Yahoo Finance income statement and earnings history | ` +
    `Forward quarters and full-year figures: analyst consensus, Yahoo Finance | ` +
    `EPS is on the adjusted basis analysts estimate against, not GAAP diluted EPS | ` +
    `Estimates are forecasts, not outcomes — the low–high range is the spread of contributing analysts`;
  block.appendChild(foot);

  section.appendChild(block);
  insertChartSection(contentEl, section);

  wraps.forEach(w => renderForecastChart(w.canvas, w.metric, cur));
}

// ── Financial table post-processor ───────────────────────────────────
// Must run AFTER renderFinancialCharts so superscripts don't confuse
// parseFinancialValue (which reads textContent, not innerHTML).
function postProcessFinancialTables(contentEl) {
  const analystMatch = contentEl.textContent.match(/(\d{1,3})\s+analysts/i);
  const nAnalysts    = analystMatch ? analystMatch[1] : '';

  contentEl.querySelectorAll('table').forEach(table => {
    const rows = Array.from(table.querySelectorAll('tr'));
    if (rows.length < 2) return;
    const headers = Array.from(rows[0].querySelectorAll('th, td')).map(c => c.textContent.trim());
    if (headers.length < 3) return;

    // Only act on tables that have ≥2 period columns
    const periodCount = headers.slice(1).filter(h => isPeriodLabel(h.replace(/[¹²³⁴\s]/g, ''))).length;
    if (periodCount < 2) return;

    // Classify each column
    const colTypes = headers.map((h, i) => {
      if (i === 0) return 'label';
      const clean = h.replace(/[¹²³⁴\s]/g, '').trim();
      if (!isPeriodLabel(clean)) return 'other';
      if (isEstimate(clean))     return 'estimate';
      if (/q[1-4]/i.test(clean)) return 'sec';   // Quarterly label → SEC 10-Q
      return 'historical';
    });

    const hasHist = colTypes.includes('historical');
    const hasSec  = colTypes.includes('sec');
    const hasEst  = colTypes.includes('estimate');

    // Style header cells
    Array.from(rows[0].querySelectorAll('th, td')).forEach((cell, i) => {
      if (colTypes[i] === 'sec') {
        cell.style.color      = 'var(--blue)';
        cell.style.borderLeft = '2px solid rgba(33,150,243,0.5)';
      } else if (colTypes[i] === 'estimate') {
        cell.style.opacity = '0.65';
      }
    });

    // Process data rows
    const toRemove = [];
    for (let r = 1; r < rows.length; r++) {
      const row   = rows[r];
      const cells = Array.from(row.querySelectorAll('th, td'));
      if (!cells.length) continue;

      const firstText = cells[0].textContent.trim().toLowerCase();
      const rowText   = cells.map(c => c.textContent).join(' ');

      // Drop source/attribution rows that Claude sometimes emits
      if (/\bsource\b/.test(firstText) ||
          /yahoo finance historical|sec filing|analyst consensus/i.test(rowText)) {
        toRemove.push(row);
        continue;
      }

      // Style + superscript data cells
      cells.forEach((cell, i) => {
        const type = colTypes[i];
        if (type === 'sec') {
          cell.style.color           = 'var(--blue)';
          cell.style.borderLeft      = '2px solid rgba(33,150,243,0.2)';
          cell.style.backgroundColor = 'rgba(33,150,243,0.04)';
        } else if (type === 'estimate') {
          cell.style.opacity = '0.65';
        }
        // Add superscript number if cell has content and doesn't already have one
        if (i > 0 && (type === 'historical' || type === 'sec' || type === 'estimate')) {
          const txt = cell.textContent.trim();
          if (txt && !/^n\/?a$/i.test(txt) && !/[¹²³]/.test(txt)) {
            const n   = type === 'historical' ? '¹' : type === 'sec' ? '²' : '³';
            const col = type === 'sec' ? 'var(--blue)' : 'var(--text-dim)';
            cell.innerHTML += `<sup style="font-size:7px;color:${col};opacity:0.65;margin-left:1px">${n}</sup>`;
          }
        }
      });
    }
    toRemove.forEach(r => r.remove());

    // Append footnote below table
    const parts = [];
    if (hasHist) parts.push('<span>¹ Yahoo Finance Historical</span>');
    if (hasSec)  parts.push('<span style="color:var(--blue)">² SEC Filing (10-K / 10-Q)</span>');
    if (hasEst)  parts.push(`<span>³ Analyst Consensus${nAnalysts ? ' — ' + nAnalysts + ' analysts' : ''}</span>`);
    if (parts.length) {
      const fn = document.createElement('div');
      fn.className = 'table-footnote';
      fn.innerHTML = parts.join('<span style="color:var(--text-dim);margin:0 8px">·</span>');
      table.insertAdjacentElement('afterend', fn);
    }
  });
}


export { renderFinancialCharts, renderForecastSection, postProcessFinancialTables };
