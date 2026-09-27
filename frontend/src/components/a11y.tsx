import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';

const APP = 'Financial Research Agent';

/** Every view names itself: the tab, history, and what a screen reader hears on arrival. */
export function useTitle(title: string | null | undefined) {
  useEffect(() => {
    document.title = title ? `${title} — ${APP}` : APP;
  }, [title]);
}

/**
 * A horizontal scroller that keyboard users can reach. It only becomes a tab
 * stop while its content actually overflows — a focusable box with nothing to
 * scroll is a dead stop (axe: scrollable-region-focusable).
 */
export function ScrollRegion({ label, children, className = 'table-scroll' }:
  { label: string; children: ReactNode; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [scrolls, setScrolls] = useState(false);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const check = () => setScrolls(el.scrollWidth > el.clientWidth + 1);
    check();
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(check) : null;
    ro?.observe(el);
    return () => ro?.disconnect();
  }, []);
  return (
    <div ref={ref} className={className} role={scrolls ? 'region' : undefined}
         aria-label={scrolls ? label : undefined} tabIndex={scrolls ? 0 : undefined}>
      {children}
    </div>
  );
}

/** A polite announcement for outcomes that happen while the user is elsewhere on the page. */
export function Announce({ message }: { message: string }) {
  return <div className="sr-only" role="status" aria-live="polite" aria-atomic="true">{message}</div>;
}
