import { describe, expect, it } from 'vitest';
import { createSseDecoder, parseEvent } from '../src/api/client';
import type { StreamEvent } from '../src/api/types';

const enc = new TextEncoder();
const frame = (o: unknown) => `data: ${JSON.stringify(o)}\n\n`;

describe('SSE decoding', () => {
  it('reassembles events split across network chunks', () => {
    const got: StreamEvent[] = [];
    const d = createSseDecoder(e => got.push(e));
    const wire = frame({ event: 'report_chunk', data: 'Hello ' }) + frame({ event: 'report_chunk', data: 'world' });
    for (let i = 0; i < wire.length; i += 7) d.push(enc.encode(wire.slice(i, i + 7)));
    d.end();
    expect(got).toEqual([
      { event: 'report_chunk', data: 'Hello ' },
      { event: 'report_chunk', data: 'world' },
    ]);
  });

  it('keeps multi-byte characters intact across chunk boundaries', () => {
    const got: StreamEvent[] = [];
    const d = createSseDecoder(e => got.push(e));
    const bytes = enc.encode(frame({ event: 'report_chunk', data: '— €1.2B ✓' }));
    bytes.forEach(b => d.push(new Uint8Array([b])));
    d.end();
    expect(got).toEqual([{ event: 'report_chunk', data: '— €1.2B ✓' }]);
  });

  it('never drops an event it does not know — the escalation bug class', () => {
    const e = parseEvent({ event: 'brand_new_event', data: { x: 1 } });
    expect(e).toMatchObject({ event: 'unknown', name: 'brand_new_event' });
  });

  it('normalises a lone tool node to the tool variant', () => {
    expect(parseEvent({ event: 'node_complete', node: 'web_search',
                        data: { tool: 'web_search', success: false, preview: 'x' } }))
      .toEqual({ event: 'node_complete', node: 'tool', data: { tool: 'web_search', success: false, preview: 'x' } });
  });

  it('passes known nodes through typed', () => {
    expect(parseEvent({ event: 'node_complete', node: 'compliance_checker', data: { verdict: 'clear' } }))
      .toEqual({ event: 'node_complete', node: 'compliance_checker', data: { verdict: 'clear' } });
  });

  it('reports unparseable frames instead of throwing', () => {
    const got: StreamEvent[] = [];
    const d = createSseDecoder(e => got.push(e));
    d.push(enc.encode('data: {not json\n\n'));
    expect(got[0]).toMatchObject({ event: 'unknown', name: '(unparseable)' });
  });
});
