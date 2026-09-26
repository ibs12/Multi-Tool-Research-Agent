// Typed boundary of the ported chart module (charts.js). The module itself is
// imperative DOM post-processing, kept verbatim rather than rewritten.
import type { Forecast } from '../api/types';

export function renderFinancialCharts(contentEl: HTMLElement): void;
export function renderForecastSection(contentEl: HTMLElement, forecast: Forecast | null): void;
export function postProcessFinancialTables(contentEl: HTMLElement): void;
