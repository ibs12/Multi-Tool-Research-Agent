import { TopBar } from './components/TopBar';
import { useRoute } from './router';
import { CompanyView } from './views/CompanyView';
import { RunPanel } from './views/RunPanel';
import { WatchlistHome } from './views/WatchlistHome';

export function App() {
  const { route, visit } = useRoute();
  return (
    <>
      <TopBar route={route} />
      {route.name === 'home' && <WatchlistHome key={visit} />}
      {route.name === 'company' && <CompanyView key={visit} companyKey={route.key} />}
      {route.name === 'research' && <RunPanel key={visit} source={{ kind: 'live', query: route.query }} />}
      {route.name === 'run' && <RunPanel key={visit} source={{ kind: 'saved', id: route.id }} />}
    </>
  );
}
