# Ground Truth for the Extraction-Accuracy Eval

**Ticket:** R2 (#31), Map 2 (#23) — research only, no application code changed.
**Date:** 2026-09-16. **Scope:** decide the ground-truth source(s) and construction
method that the E6 ticket (case sourcing & ground-truth construction) and the E2 ticket
(extraction-accuracy match rules) will build on.

## Why this document exists

Map 2 charts an **eval suite** for the extraction agent. Its headline metric is
**field-level extraction accuracy**: for a given company + filing period, did the agent
pull the correct figure for **revenue, EPS, gross margin, net income, and forward
estimates**? That metric is meaningless without *ground truth* — the true reported value
for each field across ~**100–200 company-periods** (a company paired with one specific
filing period, e.g. `AAPL · FY2023 10-K`).

The hard constraint from the ticket: this ground truth must be buildable **without manually
eyeballing each case**. A thin, hand-labeled set is a known failure mode — the prior
*TakeMeter* project collapsed when a 212-row hand-collected dataset let the model learn the
majority-class distribution instead of real label boundaries. So the question is not "can a
human read 150 filings" (they can, badly); it is "**is there a high-trust structured source
we can pull ground truth from programmatically, at 100–200-case scale, verifiably?**"

The answer is **yes: SEC XBRL** — for four of the five fields. The fifth (forward
estimates) is not in filings at all and needs a separate truth source. The rest of this
doc is the evidence and the recommended construction approach.

> **Scope note.** The application today reads the EDGAR *submissions* API for filing
> metadata (`tools/sec_edgar.py`) and `yfinance` for forward consensus
> (`tools/consensus_estimates.py`). It does **not** yet touch the XBRL `companyfacts` /
> `frames` APIs. This research covers those XBRL endpoints as a *ground-truth* source for
> the eval; it does not change how the agent under test extracts figures.

---

## 1. The candidate sources (all first-party SEC XBRL)

Every US public company files its financial statements in **XBRL** (structured, tagged
data) alongside the human-readable document. The SEC re-publishes those tagged facts four
ways. All four are the *same underlying facts*; they differ in delivery shape.

| Source | Shape | Best for |
|---|---|---|
| **`frames` REST API** (`data.sec.gov/api/xbrl/frames/...`) | One concept, one period, **all companies** that reported it — thousands of `(cik, value)` points per call.[^apis] | **Sourcing cases in bulk** — this is the workhorse for hitting 100–200 without hand-labeling. |
| **`companyfacts` REST API** (`data.sec.gov/api/xbrl/companyfacts/CIK##########.json`) | **All** concepts, **all** periods, for **one** company, in a single JSON.[^apis] | Per-case verification and pulling every field for a chosen company at once. |
| **`companyconcept` REST API** (`.../companyconcept/CIK.../us-gaap/<Concept>.json`) | One company, one concept, all periods/units.[^apis] | Targeted single-field lookups; smallest payloads. |
| **Financial Statement Data Sets** (bulk TSV, quarterly)[^fsds] | Flat `SUB`/`TAG`/`NUM`/`PRE` tables — every numeric fact from the *face* financials, all filers, per quarter. Coverage Jan 2009 – Jun 2026. | Fully offline/reproducible builds; no rate limit; join-friendly. |

There is also a heavier bulk product, the **Financial Statement *and Notes* Data Sets**,
which adds footnote/disclosure facts (not just the face financials), updated **monthly**,
coverage Jan 2009 – Aug 2026, monthly ZIPs ~41–314 MB.[^fsnds] We do **not** need the
notes tier for this eval — the five target fields all live on the face financials — but
it's the right source if the field set later grows into disclosure-level items (segment
revenue, lease detail, etc.).

**Provenance / trust.** The APIs and data sets are the SEC's own re-publication of facts
that *registrants themselves tagged* in their filings, drawn from forms 10-K, 10-Q, 8-K,
20-F, 40-F, 6-K using the standard taxonomies (`us-gaap`, `ifrs-full`, `dei`, `srt`).[^apis]
This is exactly the "high-trust primary source" the eval needs: the ground-truth value and
the value the agent is supposed to extract come from *the same filing*. Caveat the SEC
states plainly: because the data is registrant-provided, it "cannot guarantee the accuracy
of the data sets" and they are "not a substitute for" the filings themselves.[^fsds] For an
*extraction* eval that is fine — we are testing "did the agent read what the filing says,"
and XBRL *is* what the filing says.

### What a `frames` call actually returns (verified live, 2026-09-16)

`GET /api/xbrl/frames/us-gaap/RevenueFromContractWithCustomerExcludingAssessedTax/USD/CY2023.json`
returned **3,141** companies in one request, each as:

```json
{ "accn": "0001628280-26-010185", "cik": 1800, "entityName": "Abbott Laboratories",
  "loc": "US-IL", "start": "2023-01-01", "end": "2023-12-31", "val": 40109000000 }
```

That is a ready-made ground-truth row: company (`cik`/`entityName`), period
(`start`→`end`), the source filing (`accn` = accession number), and the reported value
(`val`). One call → thousands of candidate cases. That single fact is why this is
buildable without hand-labeling.

---

## 2. Coverage & reliability, per eval field

Verified live against Apple's `companyfacts` (CIK 0000320193) and the `frames` API on
2026-09-16. "Directly tagged" = there is a US-GAAP concept whose value *is* the field.
"Derived" = no such concept; must be computed from tagged concepts.

| Eval field | Status | US-GAAP concept(s) / formula | Unit | Notes |
|---|---|---|---|---|
| **Net income** | ✅ Directly tagged | `NetIncomeLoss` | USD | Cleanest field. `frames` CY2023 → **6,380** entities. |
| **EPS** | ✅ Directly tagged | `EarningsPerShareDiluted`, `EarningsPerShareBasic` | USD/shares | `frames` CY2023 diluted → **5,716** entities. Frames URL unit segment is `USD-per-shares`; returned `uom` is `USD/shares`. |
| **Revenue** | ✅ Tagged, but **fragmented** | `RevenueFromContractWithCustomerExcludingAssessedTax` **or** `Revenues` **or** (legacy) `SalesRevenueNet` | USD | Filers pick *one* top-line concept. See §3. `frames` CY2023: `RevenueFromContract…` = **3,141**, `Revenues` = **2,677**, `SalesRevenueNet` = **no frame** (retired post-ASC 606). |
| **Gross margin** | ⚠️ **Derived** | `GrossProfit ÷ Revenue` (percent) — **no** `GrossMargin`/`Margin` concept exists | — | `GrossProfit` (USD) *is* tagged, but only **2,788** filers tagged it at CY2023 (vs 3,141 with the revenue concept) — many industries (banks, insurers) never report a gross profit line. See §3. |
| **Forward estimates** | ❌ **Not in filings at all** | — | — | Analyst *consensus* is forward-looking opinion, never a filed fact. XBRL cannot supply it. Needs a separate truth source (see §4). |

Confirmation that gross margin is not tagged: Apple exposes **503** distinct `us-gaap`
concepts; a scan for any concept containing `Margin` returns **zero**. `GrossProfit` is
present as a dollar amount, so the *percentage* margin is a derivation, never a lookup.

Confirmation of revenue fragmentation: Apple simultaneously carries `Revenues`,
`RevenueFromContractWithCustomerExcludingAssessedTax`, **and** the legacy `SalesRevenueNet`
in its own `companyfacts` — proof that "revenue" is not a single well-known tag even within
one filer's history.

---

## 3. The gotchas (what E2 and E6 must design around)

These are the real reasons this needs a design, not just a `curl`.

**G1 — Revenue concept variance across filers (biggest one).** There is no single
"revenue" tag. The two dominant concepts are *largely disjoint* filer populations: at
CY2023, `RevenueFromContractWithCustomerExcludingAssessedTax` had 3,141 filers and
`Revenues` had 2,677, and a filer typically reports its top line under *one or the other*,
not both. Legacy `SalesRevenueNet` is retired post-ASC 606. **Implication for E6:** to source
revenue cases you must **union multiple revenue-concept frames and dedup by CIK**, not read
one frame. **Implication for E2:** the match rule for "revenue" must accept *whichever*
concept that filer used — i.e. compare the agent's number against a **set of acceptable
concepts**, not one hard-coded tag, or the eval will punish correct extractions.

**G2 — Gross margin is derived, and its input is incomplete.** Margin % = `GrossProfit /
Revenue`. First, it's a computed truth value, so E2 needs a **numeric tolerance** (e.g.
±0.1 pp) rather than exact-string match, and must decide the denominator (which revenue
concept) and rounding convention. Second, ~11% fewer filers tag `GrossProfit` than tag
revenue (2,788 vs 3,141 at CY2023), and whole sectors (financials) never report it — so
**gross-margin cases must be sourced only from companies that tag `GrossProfit`**, and the
eval should expect some company-periods to legitimately have *no* gross-margin ground truth.

**G3 — Restatements & comparative re-reporting: the `(company, period)` key is not
unique.** In `companyfacts`, the same period appears in *many* filings — each annual/quarterly
figure is repeated as a prior-year comparative in later filings, and genuine restatements
create new values. Live proof from Apple `EarningsPerShareDiluted`: **338** facts span only
**123** distinct periods; **112 of 123** periods carry >1 fact. FY2008 diluted EPS was
reported as **5.36** in the original 10-K (`accn 0001193125-09-214859`), then **restated to
6.78** in a later 10-K/A (`accn 0001193125-10-012091`) and re-confirmed at 6.78 in the next
10-K. **Implication:** ground truth must be keyed on the **specific accession number
(`accn`) + form** the agent extracted from, and the eval must decide its policy up front:
*as-originally-reported in that filing* (key on `accn`) vs *latest restated value* (what the
`frames` API returns — it keeps only the "most recently filed" fact per entity per
period[^apis]). For an **extraction** eval the correct choice is almost always
**as-reported-in-that-filing**, i.e. pin the `accn`.

**G4 — Foreign & non-XBRL filers fall outside `us-gaap`.** Foreign private issuers (20-F/40-F)
tag under the **`ifrs-full`** taxonomy, not `us-gaap`, so a `us-gaap` frame silently omits
them; pre-2009 filings and any un-tagged filing have no XBRL at all.[^apis][^fsds] **Implication:**
scope the case set to **domestic `us-gaap` filers, 10-K/10-Q, ≥2009** to keep ground truth
clean, and treat IFRS as an explicit out-of-scope (or a separate, later frame family).

**G5 — Unit and scale traps.** Values are raw (`40109000000`, not "$40.1 B"); EPS is
`USD/shares`; the frames URL unit segment for ratio units is written `USD-per-shares` even
though the payload reports `USD/shares`. E2's normalizer must canonicalize scale/units on
both sides before comparing (the agent will emit "$40.1 billion"; truth is `4.0109e10`).

**G6 — Rate limits / access.** The APIs need no key, but SEC fair-access caps automated
traffic at **10 requests/second** and **requires** a `User-Agent` header identifying you
(format: company/name + contact email, e.g. `Sample Company AdminContact@example.com`);
abuse leads to blocking.[^faq] For a one-time build of 100–200 cases the `frames` route is
trivially under budget (a handful of calls); the per-company `companyfacts` route (one call
per company) is also fine. If a build ever needs thousands of companies, prefer the **bulk
Financial Statement Data Sets** (offline TSVs, no rate limit) over hammering the API.[^fsds]

---

## 4. Forward estimates need a different truth source

Forward estimates (next-quarter / next-year revenue and EPS **consensus**) are analyst
*opinion about the future*. They are **never** a filed fact and therefore **cannot** come
from XBRL — no SEC source can supply them. The eval needs a distinct, timestamped source:

- **Analyst consensus** (e.g. Yahoo Finance via `yfinance`, which the app already uses in
  `tools/consensus_estimates.py`; or a vendor API such as Financial Modeling Prep, Nasdaq
  Data Link, Refinitiv/LSEG, FactSet). Trade-off: consensus is a **moving target** — it
  changes daily and is revised — so ground truth for a forward field is only meaningful
  **pinned to a retrieval date/snapshot**. Two consequences for E2: (a) the match rule must
  allow a **wider tolerance / band** than for filed facts, and (b) each forward case must
  record the *as-of date* of the consensus it was scored against.
- Because there is no free primary source of record here, forward-estimate ground truth is
  the **weakest** of the five and should be a **smaller slice** of the case set (or flagged
  as lower-confidence), with the bulk of the 100–200 cases coming from the four XBRL-backed
  fields where truth is exact and verifiable.

This split is worth stating loudly for E6: **four fields have gold, machine-verifiable
truth; one field has soft, dated truth.** Don't let the soft field dictate the eval design.

---

## 5. Trade-offs vs third-party financial datasets

Why XBRL over a commercial API (Financial Modeling Prep, Alpha Vantage, Polygon,
Sharadar/Nasdaq Data Link, Refinitiv)?

| Dimension | SEC XBRL (`frames`/`companyfacts`/bulk) | Third-party financial APIs |
|---|---|---|
| **Provenance** | The *filing itself* — same source the agent extracts from. Ideal for an extraction eval. | A vendor's *normalized re-presentation*; may silently adjust/standardize figures, so "truth" no longer matches the filing text. |
| **Traceability** | Every fact carries `accn` + `form` + `filed` → you can point at the exact document. | Usually no filing-level lineage. |
| **Cost / access** | Free, no key, 10 req/s. | Often paywalled / rate-limited by tier. |
| **Forward estimates** | ❌ none | ✅ many vendors carry consensus — this is where a vendor *is* the right call (see §4). |
| **Normalization done for you** | ❌ you handle concept variance yourself (G1) | ✅ vendor collapses revenue variants into one field — convenient, but that's exactly the normalization the eval is meant to test the *agent* on. |

**Verdict:** for the four filed fields, a vendor's convenience (collapsing revenue variants)
is a *liability* here — it hides the very ambiguity the extraction agent must handle, and
its numbers may not equal the filing. Use **SEC XBRL** for the filed fields; use a
**consensus vendor** only for forward estimates.

---

## 6. Recommended ground-truth-construction approach

**One-line rationale:** pull ground truth from the SEC **`frames` API** (bulk, one call per
concept-period yields thousands of `(company, period, accn, value)` rows), pin each case to
its **accession number**, and derive gross margin from `GrossProfit ÷ Revenue` — so 100–200
verifiable cases are assembled programmatically with **zero hand-labeling**, sidestepping
the TakeMeter failure.

Concrete recipe for E6:

1. **Pick a period grid.** Choose ~6–10 recent calendar frames spanning annual and
   quarterly (e.g. `CY2021`…`CY2024`, plus a couple of `CY2024Q#`). Diversity across
   periods and sectors is what protects against the majority-class collapse.
2. **Pull `frames` per field, per period, and index by `(cik, period)`:**
   - Net income → `NetIncomeLoss/USD`
   - EPS → `EarningsPerShareDiluted/USD-per-shares` (and `…Basic` if desired)
   - Revenue → **union** of `RevenueFromContractWithCustomerExcludingAssessedTax/USD`
     **and** `Revenues/USD`, dedup by CIK, recording *which* concept won (G1).
   - Gross profit → `GrossProfit/USD` (present for a subset only — G2).
3. **Select the case set by intersection.** Keep company-periods that have all four filed
   fields available (net income + EPS + revenue + gross profit) so each case scores the full
   field set; sample **across sectors and market caps** to avoid a homogeneous set. Target
   ~150 to land in the 100–200 band with margin for drops.
4. **Derive gross margin** = `GrossProfit / Revenue` (as a percent), storing the exact
   inputs and the rounding rule so E2 can reproduce it.
5. **Pin provenance.** For each case store `cik`, `entityName`, period (`start`/`end`), the
   `accn`, and `form` — so the *filing the agent is asked to extract from* is exactly the
   filing the truth came from (kills the restatement ambiguity, G3).
6. **Attach forward-estimate truth separately** (§4): a small slice, from a consensus vendor,
   each stamped with an as-of retrieval date; scored with a looser band.
7. **Reproducibility.** Snapshot the pulled frames to disk (or use the quarterly bulk
   Financial Statement Data Sets[^fsds]) so the eval set is frozen and re-runnable, and stay
   under the 10 req/s / `User-Agent` fair-access rules (G6).[^faq]

This yields a **large, machine-generated, filing-traceable** ground-truth set — the exact
opposite of a thin hand-labeled table.

---

## 7. What E6 and E2 depend on (hand-off facts)

**E6 — case sourcing & ground-truth construction, will rely on:**
- **`frames` is the bulk workhorse:** `GET https://data.sec.gov/api/xbrl/frames/us-gaap/<Concept>/<Unit>/<Period>.json` returns one value per company for that period, each with `cik`, `entityName`, `accn`, `start`, `end`, `val` (verified: 3,141 rows for revenue CY2023 in one call).[^apis]
- **Period formats:** annual `CY####`, quarterly `CY####Q#`, instantaneous `CY####Q#I`; durational vs point-in-time concepts use the matching form.[^apis]
- **Per-company fill-in:** `companyfacts/CIK##########.json` (10-digit zero-padded CIK) gives every field for a chosen company in one call.[^apis]
- **Scope to keep truth clean:** domestic `us-gaap` filers, 10-K/10-Q, ≥2009; exclude IFRS/20-F and pre-XBRL (G4).
- **Revenue = a union+dedup problem, not one tag** (G1); **gross margin = derived and only where `GrossProfit` is tagged** (G2); **forward estimates come from a consensus vendor, not SEC** (§4).
- **Offline/reproducible alternative:** quarterly bulk Financial Statement Data Sets (SUB/TAG/NUM/PRE TSVs, 2009–2026, face financials).[^fsds]

**E2 — extraction-accuracy match rules, will rely on:**
- **Per-field acceptable concepts, not one tag:** revenue must match against `{RevenueFromContractWithCustomerExcludingAssessedTax, Revenues, (legacy) SalesRevenueNet}`; net income → `NetIncomeLoss`; EPS → `EarningsPerShareDiluted`/`Basic`.
- **Units & scale normalization is mandatory:** truth values are raw numbers (`4.0109e10`), EPS in `USD/shares`; the agent emits formatted strings ("$40.1 B") — canonicalize both sides before compare (G5).
- **Numeric tolerance, not string equality:** exact for filed dollar/EPS facts (allow rounding to the filing's reported precision), and an explicit tolerance (e.g. ±0.1 pp) for **derived** gross margin (G2).
- **Restatement policy must be fixed:** score against **as-reported-in-`accn`** (pin the accession), not the latest restated value; note that `frames` returns the *most-recently-filed* value while `companyfacts` holds all versions — FY2008 Apple EPS was 5.36 as-filed, 6.78 restated (G3).
- **Forward estimates scored on a wider band and stamped with an as-of date** (§4).

---

## Sources

[^apis]: SEC EDGAR Application Programming Interfaces (data.sec.gov). Documents the
    `companyfacts` endpoint `https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json`
    (10-digit zero-padded CIK; all concepts for one entity), `companyconcept`
    `.../companyconcept/CIK##########/us-gaap/<Concept>.json`, and `frames`
    `.../frames/us-gaap/<Concept>/<Unit>/<Period>.json` where a *frame* aggregates the one
    fact per reporting entity **most recently filed** within a calendar period (annual
    `CY####`, quarterly `CY####Q#`, instantaneous `CY####Q#I`); no auth/key required; facts
    drawn from forms 10-K/10-Q/8-K/20-F/40-F/6-K under `us-gaap`/`ifrs-full`/`dei`/`srt`.
    Endpoint shapes, JSON fields (`accn`, `cik`, `entityName`, `start`, `end`, `val`, `fy`,
    `fp`, `form`, `filed`, `frame`), counts, and restatement behavior additionally
    **verified live against the API on 2026-09-16** (Apple CIK 0000320193 `companyfacts`
    and `companyconcept`; `frames` for revenue/net income/EPS/gross profit at CY2023).
    <https://www.sec.gov/search-filings/edgar-application-programming-interfaces>
[^fsds]: SEC Financial Statement Data Sets (DERA). Flat TSV data sets (`SUB`, `TAG`, `NUM`,
    `PRE`) of numeric facts from the **face** of financial statements, extracted from filers'
    XBRL, forms 10-K/10-Q, coverage Jan 2009 – Jun 2026, updated **quarterly**. SEC
    disclaimer: "Because the data sets are derived from information provided by individual
    registrants, we cannot guarantee the accuracy of the data sets" and they are "not a
    substitute for" the filings.
    <https://www.sec.gov/dera/data/financial-statement-data-sets>
[^fsnds]: SEC Financial Statement **and Notes** Data Sets (DERA). Adds footnote/disclosure
    facts beyond the face financials; presented unchanged from the "as filed" reports;
    coverage Jan 2009 – Aug 2026; updated **monthly** since Nov 2020 (consolidated to
    quarterly after a year); monthly ZIPs ~41–314 MB. Not required for the current five-field
    set but the right source if disclosure-level fields are added later.
    <https://www.sec.gov/dera/data/financial-statement-and-notes-data-set>
[^faq]: SEC Webmaster / Developer FAQ — fair access. Automated access capped at **10
    requests per second**, "carefully monitored to preserve equitable access"; requests must
    declare a `User-Agent` header identifying the requester (format: company/name +
    contact email, e.g. `Sample Company Name AdminContact@<domain>.com`); excessive traffic
    can be blocked ("Access Denied"). <https://www.sec.gov/os/webmaster-faq>
