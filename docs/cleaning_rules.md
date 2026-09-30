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

## 6. No subtype tags in v1

Keyword tags (executive search / recruiting / staffing / HR / veteran programs) were built and
dropped on 2026-09-29: they were guesses, and a hand check showed them mislabelling real contracts
(e.g. Heidrick's $17.25M BOND leadership program tagged as a one-off search, while the PBGC CFO and
Navy provost searches were missed). v1 publishes facts only; tags return in v2 with review.

## 7. Recompete status (`int_recompete_flags`)

Status follows the **end date only**, first match wins:
`expired` (end date passed) → `expiring_6m` → `expiring_12m` → `active`.

Example: `HQ003425CE025`, Heidrick & Struggles / WHS, $15.1M, ended 2026-09-17 → `expired`.

**Why no successor matching (decision 2026-09-29).** The first version marked an award
"recompeted" when a newer niche award appeared at the same office, same subtype, within 90 days
of its end date. A hand check of 10 such pairs found 2 right, 2 doubtful and 6 wrong:

| Old award | Picked as replacement | Problem |
|---|---|---|
| CDC clerical assistant, $82K | CFA planning and administration support, $3.0M | different work |
| DFC full-time KYC analyst, ends Dec 2026 | "OCCS staffing", started Sep 2026 | different role, **and it hid a live lead** |
| Army Work for Warriors outreach, $996K | Work for Warriors outreach, $1.0M | correct |

A missed lead costs a user more than an extra one to skip, so the list follows end dates.
Right pairs always had similar descriptions; won/lost analysis comes back in v2 with description
matching and review.

### Options and set-aside (from the award detail page)

For every still-running award (61 on 2026-09-29) the ingest also reads USAspending's award detail
page, which the search API doesn't expose. These are published as facts; the reader judges what's
a real opportunity.

| Field | Rule | Example |
|---|---|---|
| `potential_end_date` | last possible end date if every option is exercised | `W912LA24P0011`, Army Work for Warriors: current end Oct 2026, potential Oct 2029 |
| `has_options_remaining` | `potential_end_date` more than 30 days after the current end date | same contract: yes. `16PBGC26F0005`, PBGC CFO search: no |
| `ceiling_value` | value if every option is exercised | Army Work for Warriors: $6.82M ceiling vs $2.73M obligated |
| `set_aside` | as reported; blank becomes "NOT REPORTED" | "8A COMPETED", "SMALL BUSINESS SET ASIDE - TOTAL"; 34 of 61 not reported (mostly orders under a parent contract) |
| `number_of_offers_received`, `extent_competed` | as reported | Army Work for Warriors: 6 bids, "COMPETED UNDER SAP" |

Why it matters: in a hand check of 10 listed awards, 7 still had option years. Agencies usually
extend rather than re-bid while options remain, so "ends in 3 months" means something different
with and without options. The 30-day slack keeps a date that differs by a few days (e.g. Navy
provost search: ends Sep 29, potential Sep 30) from counting as an option.

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
