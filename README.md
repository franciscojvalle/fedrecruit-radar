# FedRecruit Radar

Which federal recruiting and executive-search contracts end in the next 12 months, who holds them, and
which agencies buy. A daily pipeline on public USAspending data, built for small staffing and
executive-search firms deciding where to bid.

**Live dashboard:** [open in Data Studio](https://datastudio.google.com/reporting/0743660f-a2b4-431d-8ae1-872a2df1ef46) · also embedded at [franciscojvalle.github.io](https://franciscojvalle.github.io)

## What it shows

| Page | Question it answers |
|---|---|
| Potential recompetes | Which contracts end in the next 6 / 12 months, how much they're worth, who the incumbent is, whether the agency can extend instead of rebidding, and whether it's set aside for small business |
| Market | How much agencies spend on this niche per fiscal year, and which agencies buy |
| Competition | How concentrated the market is (top-5 vendor share), and who wins |

Facts only in v1: the dashboard shows contracts that are *ending*, not predictions of which will be rebid.

## How it works

```
GitHub Actions (daily, 6:17 AM PT)
  └─ src/ingest_usaspending.py   USAspending API → Parquet in Cloud Storage → BigQuery raw tables
  └─ src/run.py                  SQL layers in BigQuery → tests → publish
        staging → intermediate → marts → views (vw_dash_*) → Data Studio
```

- **Niche:** NAICS 561311/561312, PSC R431/R499/R408/R699/R497, then description rules that label
  (never delete) software licenses, IT systems, AV equipment and advertising. Every rule and its
  effect is in [docs/cleaning_rules.md](docs/cleaning_rules.md).
- **Window:** contracts active in the last 5 fiscal years (rolls forward each October). Spending per fiscal year comes from transactions
  (action date), not award totals, so multi-year contracts aren't counted in a single year.
- **Snapshots:** each day's pull is kept by `snapshot_date`; models read the latest one, so any past
  day can be rebuilt.
- **Auth:** Workload Identity Federation from GitHub to Google Cloud. No service-account keys anywhere.
- **Reliability:** two backup schedules run only if the morning run didn't succeed (GitHub sometimes
  drops scheduled runs).

## Tests

- 78 Python tests (`pytest`): API parsing, flattening, ingest logic, SQL rendering.
- 8 SQL data tests run on every build in BigQuery (unique keys, no expired recompetes, niche size in
  range, every obligation belongs to an award, …). A failing test stops the publish step.

## Run it

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest                                   # unit tests, no cloud needed

# needs Google Cloud credentials for project fedrecruit-radar
python -m src.ingest_usaspending         # pull today's snapshot
python -m src.run                        # build, test, publish
```

## Known limits

- **Set-aside is missing on most orders.** Orders under a parent contract don't carry the set-aside
  field; about 71% of the $ ending in the next 12 months is "not reported", so the dashboard offers
  set-aside as a filter (with "Not reported" as its own value) rather than a headline number.
  v2: read it from the parent contract.
- **Two buyers drive recent growth.** U.S. International Development Finance Corporation (from FY2024) and
  Washington Headquarters Services (from FY2025). The rest of the niche is about $8–12M a year.
- **Not yet:** open SAM.gov notices, a weekly email brief, and won/lost recompetes (v2).

## Repo

```
src/        ingest + build runner
sql/        staging, intermediate, marts, views, tests
docs/       cleaning rules, decisions
tests/      pytest suite + API fixtures
.github/    daily ingest, tests on push, Dependabot
```

Data: [USAspending.gov](https://www.usaspending.gov) (public domain). Built by
[Francisco Valle](https://github.com/franciscojvalle).
