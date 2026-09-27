import { useEffect } from 'react';
import { TopBar } from './components/TopBar';
import { useRoute } from './router';
import { CompanyView } from './views/CompanyView';
import { RunPanel } from './views/RunPanel';
import { WatchlistHome } from './views/WatchlistHome';

export function App() {
  const { route, visit } = useRoute();

  // On in-app navigation, move focus to the new page's heading so keyboard and
  // screen-reader users land at the top of what changed, not on a stale link.
  // (Not on first load — the browser already starts at the top.)
  useEffect(() => {
    if (visit === 0) return;
    let tries = 0;
    const land = () => {
      const h1 = document.querySelector<HTMLElement>('#content h1');
      if (h1) { h1.tabIndex = -1; h1.focus({ preventScroll: true }); return; }
      if (++tries < 30) requestAnimationFrame(land);      // views render their h1 after data loads
      else document.getElementById('content')?.focus({ preventScroll: true });
    };
    requestAnimationFrame(land);
  }, [visit]);

  return (
    <>
      <a className="skip-link" href="#content">Skip to content</a>
      <TopBar route={route} />
      <div id="content" tabIndex={-1}>
      {route.name === 'home' && <WatchlistHome key={visit} />}
      {route.name === 'company' && <CompanyView key={visit} companyKey={route.key} />}
      {route.name === 'research' && <RunPanel key={visit} source={{ kind: 'live', query: route.query }} />}
      {route.name === 'run' && <RunPanel key={visit} source={{ kind: 'saved', id: route.id }} />}
      </div>
    </>
  );
}
