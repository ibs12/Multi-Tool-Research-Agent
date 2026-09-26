// Same-origin API client. The SPA is served by the API it talks to (ADR-0013),
// and Vite proxies these paths in dev, so every URL here is relative.

import {
  KNOWN_EVENTS, KNOWN_NODES,
  type CompanyTimeline, type ResearchRequest, type RunRecord, type StreamEvent,
  type Sweep, type WatchlistEntry,
} from './types';

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, init);
  if (!resp.ok) {
    let detail = `${resp.status}`;
    try { detail = (await resp.json()).detail ?? detail; } catch { /* not JSON */ }
    throw new ApiError(resp.status, String(detail));
  }
  return resp.json() as Promise<T>;
}

export const api = {
  health: () => json<{ status: string }>('/health'),
  watchlist: () => json<WatchlistEntry[]>('/watchlist'),
  addCompany: (body: { name: string; cik?: number | null; ticker?: string | null }) =>
    json<WatchlistEntry>('/watchlist', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    }),
  removeCompany: (key: string) =>
    json<{ removed: string }>(`/watchlist/${encodeURIComponent(key)}`, { method: 'DELETE' }),
  company: (key: string, limit = 20) =>
    json<CompanyTimeline>(`/companies/${encodeURIComponent(key)}?limit=${limit}`),
  sweeps: (limit = 5) => json<Sweep[]>(`/sweeps?limit=${limit}`),
  run: (id: string) => json<RunRecord>(`/runs/${encodeURIComponent(id)}`),
};

const KNOWN = new Set<string>(KNOWN_EVENTS);
const NODES = new Set<string>(KNOWN_NODES);

/**
 * One decoded SSE `data:` payload → a typed event. Never throws and never
 * drops: an event name the client does not know becomes `unknown`, which the
 * run view shows — the escalation bug was an event that vanished silently.
 */
export function parseEvent(raw: unknown): StreamEvent {
  const obj = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>;
  const name = typeof obj.event === 'string' ? obj.event : '';
  if (!KNOWN.has(name)) return { event: 'unknown', name: name || '(missing)', raw };
  if (name === 'node_complete') {
    const node = typeof obj.node === 'string' ? obj.node : '';
    const data = (obj.data ?? {}) as Record<string, unknown>;
    if (NODES.has(node)) return { event: 'node_complete', node, data } as StreamEvent;
    return {
      event: 'node_complete', node: 'tool',
      data: { tool: String(data.tool ?? node), success: data.success !== false,
              preview: typeof data.preview === 'string' ? data.preview : undefined },
    };
  }
  return obj as unknown as StreamEvent;
}

/** Splits a byte stream into SSE `data:` payloads. Exported for tests. */
export function createSseDecoder(onEvent: (e: StreamEvent) => void) {
  const decoder = new TextDecoder();
  let buffer = '';
  const flushLine = (line: string) => {
    if (!line.startsWith('data:')) return;
    const body = line.slice(5).trimStart();
    try { onEvent(parseEvent(JSON.parse(body))); }
    catch { onEvent({ event: 'unknown', name: '(unparseable)', raw: body }); }
  };
  return {
    push(chunk: Uint8Array) {
      buffer += decoder.decode(chunk, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() ?? '';
      lines.forEach(flushLine);
    },
    end() {
      buffer += decoder.decode();
      if (buffer) flushLine(buffer);
      buffer = '';
    },
  };
}

/** POST /research/stream. Resolves when the stream closes. */
export async function streamResearch(
  req: ResearchRequest,
  onEvent: (e: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch('/research/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
    signal,
  });
  if (!resp.ok || !resp.body) {
    let detail = `Server error ${resp.status}`;
    try { detail = (await resp.json()).detail ?? detail; } catch { /* not JSON */ }
    throw new ApiError(resp.status, detail);
  }
  const reader = resp.body.getReader();
  const sse = createSseDecoder(onEvent);
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    sse.push(value);
  }
  sse.end();
}
