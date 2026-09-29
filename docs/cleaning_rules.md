# Cleaning rules

Every rule the SQL applies to the raw USAspending data, with a real example. Numbers are from
the 2026-09-29 pull (FY2022 to today, NAICS 561311/561312, contract types A–D).

Rules label rows; they never delete them. `int_niche_filter` keeps every award with an
`exclusion_reason`, so each rule's effect can be counted and reversed.

## Funnel

| Step | Awards | Obligated to date |
|---|---:|---:|
| Pulled from the API (NAICS + contract types + dates) | 285 | $148.8M |
| After the PSC rule | 214 | $131.1M |
| After the description rules (the niche) | 192 | $128.0M |

## 1. Latest snapshot only (`stg_awards`, `stg_transactions`)

Raw tables keep every day's pull. Models read only the latest `snapshot_date`, and every "today"
in the logic is that date (`as_of_date`), so rebuilding an old day gives the same answer it gave then.

## 2. Text cleanup (`stg_awards`)

Names and descriptions are trimmed; an empty UEI becomes NULL; a missing base obligation date
falls back to the start date (`award_date`).

## 3. Data-quality flags, not filters (`stg_awards`)

| Flag | Rule | Example |
|---|---|---|
| `is_zero_amount` | `award_amount = 0` | 12 of 285; kept because the end date still signals a recompete |
| `is_masked_recipient` | name contains UNDISCLOSED / REDACTED / MULTIPLE RECIPIENTS | 9 of 285, e.g. "FOREIGN AWARDEES (UNDISCLOSED)" on USAID personal services contracts |

## 4. Niche: industry and service codes (`int_niche_filter`)

- NAICS must be 561311 or 561312 (the API filter already does this; kept as a guard).
- PSC must be R431, R499, R408, R699 or R497. NAICS alone is too loose: contracting officers use
  561311 for many things.

Before → after: `FA300223C0008`, Air Force, NAICS 561311, **PSC R701** (advertising), $5.95M → out (`psc`).

## 5. Niche: description rules (`int_niche_filter`)

Checked in this order; the first match is the reason reported. Rules marked *override* are skipped
when the description also says RECRUIT / SEARCH / HEADHUNT / STAFFING / PLACEMENT / HIRING.

| Reason | Matches | Override | Example (before → after) | Count |
|---|---|---|---|---:|
| `software_tool` | LinkedIn, HireEZ, Polihire, Avue, Salesforce, software, license, subscription | no | "LINKEDIN LICENSES AND JOB POSTINGS", $274K → out | 9 |
| `it_system` | IT support, systems support, ATRRS, eFlow, eRADS, electronic case, Oracle, PRISM | yes | "IGF::OT::IGF ELECTRONIC CASE ADJUDICATION", $1.1M → out | 5 |
| `av_equipment` | AV, audio-visual, lighting, equipment | yes | "AV SUPPORT SERVICES", $104K → out | 6 |
| `advertising` | advertis… | yes | "COMMERCIAL ADVERTISING FOR NAVFAC FAR EAST", $92K → out | 1 |
| `unlabeled_call` | the whole description is an order number | no | "BPA CALL 0008", $61K → out | 1 |

Override example: "EXECUTIVE SEARCH FIRM TO ASSIST THE CGA …" mentions a system but is recruiting → **in**.

### Changes from the Definitions doc (2026-09-24), for approval

1. **`IGF::` is not an exclusion.** It marks inherently-governmental work, not IT. The old rule dropped
   "IGF::CT::IGF MARKETING AND RECRUITING SUPPORT" (W9124918C0014, $587K). IT is now matched by IT words.
2. **No medical rule.** Inside the niche PSCs, medical words mean recruiting clinicians
   ("CLINICAL RECRUITMENT AND PLACEMENT", VA, $792K; "HEADHUNTING … GASTROENTEROLOGIST"), which is
   on-topic. Medical services themselves already fail the PSC rule (e.g. ultrasound technologist, PSC Q522).
3. **Added** HR/recruiting software (Avue, HireEZ, Polihire, Salesforce) and AV/equipment work,
   found by reading every niche description.

## 6. Subtype (`int_niche_filter`)

First match wins; default `staffing`.

| Subtype | Matches | Example | Awards / $ |
|---|---|---|---:|
| `executive_search` | executive search, "search for", (SES), private sector leadership, headhunt, selection board | "SENIOR EXECUTIVE SEARCH (SES)" | 22 / $34.6M |
| `veteran_employment` | warrior, yellow ribbon, veteran | "SFP: WORK FOR WARRIORS HAWAII" | 12 / $19.9M |
| `recruiting` | recruit, outreach, hiring, job fair, job posting, talent acquisition | "TALENT ACQUISITION MANAGED SERVICES" | 59 / $33.4M |
| `hr_support` | human resource, HR, human capital, EEO, classification | "EEO COUNSELING AND EEO INVESTIGATION" | 9 / $1.8M |
| `staffing` | everything else: individual positions | "3 EXECUTIVE ASSISTANTS FOR OCE" | 112 / $41.3M |

(Counts are over the 214 PSC-matched awards.) v2 replaces these keywords with LLM classification plus human review.

## 7. Recompete status (`int_recompete_flags`)

- **Successor:** a newer niche award, same sub-agency, same subtype, awarded within 90 days either
  side of the old end date. The earliest one wins.
- **Status**, first match wins: `recompeted` → `expired_no_successor` (ended >90 days ago) →
  `expired_pending` (ended ≤90 days ago) → `expiring_6m` → `expiring_12m` → `active`.

Example: `HQ003425CE025`, Heidrick & Struggles / WHS, $15.1M, ended 2026-09-17 → `expired_pending`.
It becomes `expired_no_successor` after 2026-12-16 unless a successor appears.

Known limit (Definitions, open item C): DFC places many small staffing orders at one sub-agency, so
an unrelated order can look like a successor. To be checked against 10 known recompetes.

## 8. Money

- `award_amount` = obligated to date ("approved so far"), not the ceiling. Running contracts can grow.
- `annualized_value` = `award_amount / max(duration in years, 1)`, so a 3-month order and a 5-year
  contract compare fairly.
- Yearly totals come from `fct_obligations` (one row per contract action, summed by the action's
  fiscal year), never from `award_amount`. Negative actions (de-obligations) are kept.
- `total_outlays` (cash paid) is not used: agencies report it inconsistently (87 of 285 blank).

## 9. Checks before publishing (`sql/tests/`, `src/run.py`)

Nothing is published unless all of these pass:

| Check | Why |
|---|---|
| one row per award in `fact_awards` | joins downstream would double-count |
| every award has agency, sub-agency, vendor name and UEI | dashboard filters would silently drop rows |
| no recompete has already ended | the list must be actionable |
| one row per (award, modification) in `fct_obligations` | yearly totals would double-count |
| every transaction belongs to an award | orphaned money |
| niche between 100 and 600 awards | catches a broken rule or a source change |
| status is a documented value | dashboard legends stay complete |
| largest agency's total within 1% of USAspending's own aggregate | independent check that no rows were lost or duplicated |
