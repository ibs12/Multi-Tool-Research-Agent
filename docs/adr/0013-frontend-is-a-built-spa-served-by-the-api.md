# The frontend becomes a built SPA served by the API

`frontend/index.html` is a single ~2,800-line file (CSS + JS inline, two CDN scripts, no build step). That was the right shape for one screen: type a query, watch a log, read a brief. The watchlist direction needs four views sharing state — watchlist home, company timeline, run view, deltas — which is more than one file of vanilla JS wants to carry.

## Considered options

- **Modularised vanilla.** Split into ES modules with a small hash router; no build step, `railway up` keeps working unchanged. Rejected not because it *can't* work but because every added view means hand-rolling more state and rendering that a framework already solves.
- **Next.js as a separate service.** Rejected: a second Railway service, a Node runtime to deploy and pay for, and CORS between halves — while Next's actual strengths (SSR, file routing, server actions) are largely wasted when FastAPI owns the backend and this is a single-user tool.
- **React + Vite + TypeScript, built to static assets served by FastAPI (chosen).** One service; the built output lands where `StaticFiles` already serves from.

## The deciding argument

The frontend **silently ignored the `escalation` SSE event** for days. Nothing connected the API's event contract to the code consuming it, so a new event type simply vanished and an escalated run rendered as a blank page — the precise outcome [ADR-0009](./0009-human-escalation-is-a-terminal-state.md) exists to forbid. A typed event union makes that class of bug a compile error, and the watchlist work adds far more contract surface (signals, deltas, run timelines, watchlist mutations). TypeScript here is not ceremony; it is the guard against a failure this codebase has already produced once.

## Consequences

- **A build step is the price.** Deployment stops being "`railway up` uploads Python" and becomes a multi-stage Docker build — a Node stage builds the frontend, the Python stage serves it. More moving parts and slower builds, in a deploy path that currently works. This is the real cost, and it is why a framework was *refused* while the app was still one screen: the justification is the view count, not fashion.
- **The visual identity is ported, not replaced.** The dark terminal/Bloomberg aesthetic suits a financial research tool and is distinctive; a structural rewrite is not a licence to fall back on default component-library grey.
- **The live-run view survives as one component with two data sources** — streaming SSE while you watch, replayed from the run store when cron ran it at 3am. The permalink work already proved that second path (`loadSavedRun`).
- Sanitisation stays mandatory: the brief is model output quoting scraped pages, so rendering it in React must keep the DOMPurify step rather than trusting `dangerouslySetInnerHTML`.
