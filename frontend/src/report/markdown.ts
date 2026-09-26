import DOMPurify from 'dompurify';
import { marked } from 'marked';

/**
 * Markdown → sanitised HTML for the brief.
 *
 * The brief is model output that quotes scraped web pages, and marked passes
 * raw HTML straight through. React does not make that safe: the HTML has to go
 * into the DOM as HTML, so it is sanitised here, always (ADR-0013). There is no
 * code path that renders unsanitised markup.
 */
export function renderMarkdown(md: string): string {
  const html = marked.parse(String(md ?? ''), { async: false }) as string;
  return DOMPurify.sanitize(html);
}
