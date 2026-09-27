import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { href, onLinkClick, type Route } from '../router';

export function TopBar({ route }: { route: Route }) {
  const [online, setOnline] = useState<boolean | null>(null);
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    api.health().then(() => setOnline(true), () => setOnline(false));
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  const offset = -now.getTimezoneOffset() / 60;
  const clock = `${now.toLocaleTimeString('en-GB', { hour12: false })} UTC${offset >= 0 ? '+' : '-'}${String(Math.abs(offset)).padStart(2, '0')}`;
  const active = (n: Route['name'][]) => (n.includes(route.name) ? 'nav-link active' : 'nav-link');

  return (
    <header className="topbar">
      <a className="topbar-brand" href="/" onClick={onLinkClick} aria-label="Watchlist home">
        <span className="brand-badge">ALPHA</span>
        <span className="brand-name">FINANCIAL RESEARCH AGENT</span>
      </a>
      <nav className="topbar-nav" aria-label="Main">
        <a className={active(['home', 'company'])} href={href({ name: 'home' })} onClick={onLinkClick}>Watchlist</a>
        <a className={active(['research', 'run'])} href={href({ name: 'research', query: '' })} onClick={onLinkClick}>Research</a>
      </nav>
      <div className="topbar-meta">
        <div
          className={`status-dot ${online === false ? 'offline' : ''}`}
          title={online === false ? 'API offline' : 'API connected'}
          role="img"
          aria-label={online === false ? 'API offline' : 'API connected'}
        />
        <span className="topbar-time">{clock}</span>
      </div>
    </header>
  );
}
