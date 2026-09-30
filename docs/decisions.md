# Decisions

Each entry: the decision, the alternatives considered, and why this one.

## Three-part niche rule (NAICS + PSC + description exclusions)
**Alternatives considered:** NAICS codes alone (561311/561312); PSC R699 alone (first draft).
**Why this one:** Contracting officers use NAICS loosely, so PSC is the stronger signal. R699 alone matched only 7 awards in the sample, which was too narrow. On live data the rule goes 285 → 214 (PSC) → 192 awards / $128.0M (description rules).

## Description exclusions ignore the IGF:: label; no medical exclusion
**Alternatives considered:** Treating descriptions that start with `IGF::` as IT work; excluding medical and clinical terms.
**Why this one:** `IGF::OT/CT/CL::IGF` is the federal Inherently Governmental Function label, which appears on any kind of service, so it dropped real recruiting awards (e.g. "Search firm to select…", "Marketing and recruiting support"). IT work is caught by IT words instead. Inside the niche PSC codes, medical terms mostly hit clinician recruiting (e.g. VA "Clinical recruitment and placement"), which is in scope.

## BigQuery + Cloud Storage for the public pipeline
**Alternatives considered:** DuckDB in Google Drive (the pattern used for the private companion pipeline).
**Why this one:** An always-on service, a native Looker Studio connector, no file-shuttle step, and IAM scoped per table.

## Separate personal GCP project
**Alternatives considered:** Building under an existing organization's Google Workspace / GCP account.
**Why this one:** Clean ownership of the public project, with no Workspace admin restrictions.

## BigQuery daily query quota capped at 30 GB
**Alternatives considered:** No quota / default limits.
**Why this one:** Guarantees public dashboard traffic can never leave the free tier.

## No key files; GitHub Actions authenticates via Workload Identity Federation
**Alternatives considered:** A service account JSON key stored as a GitHub Secret.
**Why this one:** No long-lived credential exists anywhere. The provider is pinned to this repository's ID, name and `main` branch.

## One BigQuery dataset with table-name prefixes
**Alternatives considered:** Three datasets (staging, intermediate, marts).
**Why this one:** Simpler IAM: one dataset to grant access to instead of three.

## Fiscal-year spending from transactions, not award totals
**Alternatives considered:** Assigning each award's total to the fiscal year it started.
**Why this one:** Award amount is cumulative, so multi-year contracts would land in the wrong year and distort the trend.

## Recompete list by end date only
**Alternatives considered:** Matching each expiring award to a successor (same office + subtype + within 90 days) to mark it "recompeted".
**Why this one:** In a hand check, 6 of 10 matched pairs were wrong, and the matching hid a live lead. A missed lead costs more than an extra row.

## v1 publishes facts only
**Alternatives considered:** Keyword-based subtypes (executive search, staffing, …) and inferred labels such as "likely recompete" vs "option decision".
**Why this one:** Hand checks found mislabels on the largest contracts, e.g. a $17.25M leadership program tagged as a one-off search. The data was right every time and the inferences weren't. The dashboard shows the facts (end date, options remaining, potential end date, ceiling, set-aside, number of offers) and the reader judges. Labels come back in v2, with review.

## "Active" agency = a niche award in the last 12 months
**Alternatives considered:** 24 months (first draft of the definitions).
**Why this one:** 24 months overlapped with "lapsing" (last award 12–24 months ago). 12 months keeps the four statuses (active, new, lapsing, lapsed) non-overlapping.
