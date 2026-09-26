import { useState } from 'react';
import { api } from '../api/client';

/**
 * Track a company. The CIK is optional but preferred: it survives renames and
 * ticker changes, and it is what the filing Signal polls — without it a company
 * refreshes only on price moves or by hand (docs/watchlist-operations.md).
 */
export function AddCompany({ onAdded }: { onAdded: () => void }) {
  const [name, setName] = useState('');
  const [cik, setCik] = useState('');
  const [ticker, setTicker] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const cikValid = cik.trim() === '' || /^\d{1,10}$/.test(cik.trim());

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || !cikValid) return;
    setBusy(true);
    setError(null);
    try {
      await api.addCompany({
        name: name.trim(),
        cik: cik.trim() ? Number(cik.trim()) : null,
        ticker: ticker.trim().toUpperCase() || null,
      });
      onAdded();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card add-form" onSubmit={submit} aria-labelledby="add-title">
      <h2 id="add-title" className="section-title">Add a company</h2>
      <div className="form-grid">
        <label>
          <span className="query-label">Company name</span>
          <input className="text-input" value={name} onChange={e => setName(e.target.value)}
                 placeholder="Apple Inc." required autoFocus />
        </label>
        <label>
          <span className="query-label">SEC CIK <span className="muted">(recommended)</span></span>
          <input className="text-input" value={cik} onChange={e => setCik(e.target.value)}
                 inputMode="numeric" placeholder="320193" aria-invalid={!cikValid}
                 aria-describedby="cik-help" />
        </label>
        <label>
          <span className="query-label">Ticker</span>
          <input className="text-input" value={ticker} onChange={e => setTicker(e.target.value)}
                 placeholder="AAPL" maxLength={10} />
        </label>
      </div>
      <p id="cik-help" className={`small ${cikValid ? 'muted' : 'neg'}`}>
        {cikValid
          ? <>The CIK lets new 10-K/10-Q/8-K filings trigger a refresh. Find it on <a href="https://www.sec.gov/search-filings" target="_blank" rel="noreferrer">SEC EDGAR</a>. The ticker enables price-move signals.</>
          : 'A CIK is digits only, up to 10.'}
      </p>
      {error && <p className="neg small" role="alert">{error}</p>}
      <p className="small muted">The first sweep after adding runs a baseline brief for it (~$0.40 on the current model).</p>
      <button className="run-btn" type="submit" disabled={busy || !name.trim() || !cikValid}>
        <span className="btn-text">{busy ? 'Adding…' : 'Track company'}</span>
      </button>
    </form>
  );
}
