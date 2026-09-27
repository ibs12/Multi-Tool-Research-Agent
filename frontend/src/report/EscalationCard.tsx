import type { ComplianceVerdict, EscalationPackage } from '../api/types';

/**
 * An escalated run (ADR-0009): the compliance checker would not stand behind a
 * brief. Rendered as a deliberate safety outcome — never as a missing report.
 * Everything here is text nodes, so model/web-derived strings cannot be markup.
 */
export function EscalationCard({ pkg, verdict, live = false }:
  { pkg: EscalationPackage; verdict: ComplianceVerdict; live?: boolean }) {
  const unresolved = pkg.unresolved?.length ? pkg.unresolved : verdict.reasons ?? [];
  const reason = pkg.reason
    || 'The compliance checker could not verify this research well enough to produce a defensible brief.';
  return (
    <div className="markdown-body">
      {/* An alert only when it happens in front of you; a saved one is just a region. */}
      <div className="escalation-card" role={live ? 'alert' : 'region'} aria-label="Escalated to a human">
        <p className="escalation-title">⚠ Escalated to a human — no brief produced</p>
        <p className="escalation-lead">
          The compliance checker would not stand behind a brief built on this evidence, so none was
          generated. This is a deliberate safety outcome, not an error.
        </p>
        <h2 className="esc-h">Why</h2>
        <p>{reason}</p>
        {verdict.gap_type && (<><h2 className="esc-h">Gap type</h2><p><code>{verdict.gap_type}</code></p></>)}
        {unresolved.length > 0 && (
          <>
            <h2 className="esc-h">What a human needs to decide</h2>
            <ul>{unresolved.map((u, i) => <li key={i}>{u}</li>)}</ul>
          </>
        )}
        {pkg.evidence_summary && (
          <details>
            <summary>Evidence the agents gathered</summary>
            <pre>{pkg.evidence_summary}</pre>
          </details>
        )}
      </div>
    </div>
  );
}

export function escalationAsText(company: string, pkg: EscalationPackage, verdict: ComplianceVerdict) {
  const unresolved = pkg.unresolved?.length ? pkg.unresolved : verdict.reasons ?? [];
  return [
    `ESCALATED — no brief produced: ${company}`,
    pkg.reason ? `Why: ${pkg.reason}` : '',
    verdict.gap_type ? `Gap type: ${verdict.gap_type}` : '',
    unresolved.length ? 'Unresolved:\n' + unresolved.map(u => `- ${u}`).join('\n') : '',
  ].filter(Boolean).join('\n\n');
}
