import { useEffect, useState } from 'react';

// Four views don't justify a router dependency. Paths are real (not #hash) so
// links read well; the API serves index.html for them.
//
// `/?run=<id>` stays the permalink: shared links and the worker's notifications
// already point there, so it must keep resolving forever.

export type Route =
  | { name: 'home' }
  | { name: 'company'; key: string }
  | { name: 'research'; query: string }
  | { name: 'run'; id: string };

export function parseRoute(pathname: string, search: string): Route {
  const params = new URLSearchParams(search);
  const run = params.get('run');
  if (run) return { name: 'run', id: run };
  const company = pathname.match(/^\/company\/(.+?)\/?$/);
  if (company) return { name: 'company', key: decodeURIComponent(company[1]) };
  if (/^\/research\/?$/.test(pathname)) return { name: 'research', query: params.get('q') ?? '' };
  return { name: 'home' };
}

export function href(route: Route): string {
  switch (route.name) {
    case 'home': return '/';
    case 'company': return `/company/${encodeURIComponent(route.key)}`;
    case 'research': return route.query ? `/research?q=${encodeURIComponent(route.query)}` : '/research';
    case 'run': return `/?run=${encodeURIComponent(route.id)}`;
  }
}

export function navigate(to: string, replace = false) {
  if (replace) history.replaceState(null, '', to);
  else history.pushState(null, '', to);
  window.dispatchEvent(new PopStateEvent('popstate'));
  window.scrollTo(0, 0);
}

/**
 * The current route plus a visit counter. Views are keyed by the counter, so
 * following a link — even to the route already on screen — starts that view
 * fresh (e.g. "Research" after a finished run gives a clean terminal).
 */
export function useRoute(): { route: Route; visit: number } {
  const read = () => parseRoute(location.pathname, location.search);
  const [state, setState] = useState(() => ({ route: read(), visit: 0 }));
  useEffect(() => {
    const on = () => setState(s => ({ route: read(), visit: s.visit + 1 }));
    window.addEventListener('popstate', on);
    return () => window.removeEventListener('popstate', on);
  }, []);
  return state;
}

/** Left-click on an <a href> navigates in-app; modified clicks open tabs as usual. */
export function onLinkClick(e: React.MouseEvent<HTMLAnchorElement>) {
  if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  e.preventDefault();
  navigate(e.currentTarget.getAttribute('href') ?? '/');
}
