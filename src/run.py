"""Transform: build the SQL models in BigQuery with write-audit-publish.

  1. WRITE    build every model into a scratch table  (<dataset>.wap__<model>)
  2. AUDIT    run the data tests in sql/tests/ against the scratch tables, plus a
              reconciliation of our totals against USAspending's own aggregate
  3. PUBLISH  only if every check passed: copy each scratch table over the real one
  4. SNAPSHOT append today's state to snapshot_daily (idempotent per day)
  5. VIEWS    (re)create the views

If any check fails, nothing is published: the dashboard keeps showing yesterday's good data
and the scratch tables stay behind for debugging.

SQL files are plain SELECTs with three placeholders:
  {{ ref('model') }}     another model (scratch table while building, real table after)
  {{ source('table') }}  a raw table written by the ingest
  {{ table('name') }}    a published table or view (snapshot and view scripts)

Usage:
  python -m src.run                  # full build (needs GCP credentials)
  python -m src.run --render MODEL   # print one model's rendered SQL, no cloud
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("run")

PROJECT = os.environ.get("GCP_PROJECT", "fedrecruit-radar")
DATASET = os.environ.get("BQ_DATASET", "fedrecruit")
SQL_DIR = Path(__file__).resolve().parent.parent / "sql"
SCRATCH_PREFIX = "wap__"
RECONCILE_TOLERANCE = 0.01  # 1%

# Build order. Each model only refs models listed before it.
MODELS = [
    "staging/stg_awards",
    "staging/stg_transactions",
    "staging/stg_award_details",
    "intermediate/int_niche_filter",
    "intermediate/int_recompete_flags",
    "marts/fact_awards",
    "marts/recompetes",
    "marts/fct_obligations",
    "marts/dim_agencies",
    "marts/dim_vendors",
]
SNAPSHOT_SCRIPTS = ["snapshot/snapshot_daily"]
VIEWS = [
    "views/vw_award_events",
    # One flat view per dashboard page, so Looker Studio needs no blending.
    "views/vw_dash_recompetes",
    "views/vw_dash_market",
    "views/vw_dash_wins",
    "views/vw_dash_vendor_share",
]

_PLACEHOLDER = re.compile(r"\{\{\s*(ref|source|table)\(\s*'([A-Za-z0-9_]+)'\s*\)\s*\}\}")


class CheckFailed(RuntimeError):
    pass


def model_name(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def render(sql: str, scratch: bool, project: str = PROJECT, dataset: str = DATASET) -> str:
    """Replace placeholders with fully qualified table names."""
    def sub(m: re.Match) -> str:
        kind, name = m.group(1), m.group(2)
        if kind == "ref" and scratch:
            name = SCRATCH_PREFIX + name
        return f"`{project}.{dataset}.{name}`"
    return _PLACEHOLDER.sub(sub, sql)


def read_sql(path: str) -> str:
    return (SQL_DIR / f"{path}.sql").read_text()


def test_files() -> list[Path]:
    return sorted((SQL_DIR / "tests").glob("*.sql"))


@dataclass
class Result:
    name: str
    failing_rows: int


class Runner:
    def __init__(self, client, project: str = PROJECT, dataset: str = DATASET):
        self.client = client
        self.project = project
        self.dataset = dataset

    def fq(self, name: str) -> str:
        return f"{self.project}.{self.dataset}.{name}"

    def query(self, sql: str):
        return self.client.query(sql).result()

    # 1. WRITE
    def build(self) -> None:
        for path in MODELS:
            name = model_name(path)
            select = render(read_sql(path), scratch=True, project=self.project, dataset=self.dataset)
            self.query(f"CREATE OR REPLACE TABLE `{self.fq(SCRATCH_PREFIX + name)}` AS\n{select}")
            log.info("built %s", SCRATCH_PREFIX + name)

    # 2. AUDIT
    def run_tests(self) -> list[Result]:
        results = []
        for path in test_files():
            sql = render(path.read_text(), scratch=True, project=self.project, dataset=self.dataset)
            n = sum(1 for _ in self.query(sql))
            results.append(Result(path.stem, n))
            log.info("test %-32s %s", path.stem, "ok" if n == 0 else f"FAILED ({n} rows)")
        return results

    def agency_totals(self) -> tuple[dt.date, dict[str, float]]:
        rows = list(self.query(
            f"SELECT awarding_agency, SUM(obligation_amount) AS total, ANY_VALUE(as_of_date) AS as_of "
            f"FROM `{self.fq(SCRATCH_PREFIX + 'stg_transactions')}` GROUP BY awarding_agency"))
        as_of = rows[0]["as_of"] if rows else dt.date.today()
        return as_of, {r["awarding_agency"]: float(r["total"]) for r in rows}

    # 3. PUBLISH
    def publish(self) -> None:
        from google.cloud import bigquery

        config = bigquery.CopyJobConfig(write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE)
        for path in MODELS:
            name = model_name(path)
            self.client.copy_table(self.fq(SCRATCH_PREFIX + name), self.fq(name), job_config=config).result()
            log.info("published %s", name)

    # 4-5. SNAPSHOT + VIEWS
    def run_scripts(self, paths: list[str]) -> None:
        for path in paths:
            self.query(render(read_sql(path), scratch=False, project=self.project, dataset=self.dataset))
            log.info("ran %s", path)


def reconcile(as_of: dt.date, ours: dict[str, float], api_totals: dict[str, float]) -> list[str]:
    """Compare our per-agency totals with USAspending's own aggregate for the largest agency.

    Two different API endpoints, same filters: if they disagree by more than 1%, the pull
    or the SQL lost or duplicated rows.
    """
    if not api_totals:
        return ["USAspending returned no agency totals to reconcile against"]
    agency = max(api_totals, key=api_totals.get)
    theirs, mine = api_totals[agency], ours.get(agency, 0.0)
    diff = abs(mine - theirs) / abs(theirs) if theirs else abs(mine)
    log.info("reconcile %s: ours %.0f vs USAspending %.0f (%.2f%%)", agency, mine, theirs, diff * 100)
    if diff > RECONCILE_TOLERANCE:
        return [f"{agency}: ours {mine:,.0f} vs USAspending {theirs:,.0f} ({diff:.1%} > 1%)"]
    return []


def fetch_api_agency_totals(as_of: dt.date) -> dict[str, float]:
    import requests

    from src.ingest_usaspending import window
    from src.usaspending import BASE_URL, AwardSearch, _post_with_retry

    start, end = window(as_of)
    body = {"filters": AwardSearch(start, end).filters(), "limit": 100, "page": 1}
    payload = _post_with_retry(requests.Session(), body, url=f"{BASE_URL}/spending_by_category/awarding_agency/")
    return {r["name"]: float(r["amount"]) for r in payload.get("results") or []}


def run(client, skip_reconcile: bool = False) -> None:
    runner = Runner(client)
    runner.build()

    failures = [f"{r.name}: {r.failing_rows} failing rows" for r in runner.run_tests() if r.failing_rows]
    if not skip_reconcile:
        as_of, ours = runner.agency_totals()
        failures += reconcile(as_of, ours, fetch_api_agency_totals(as_of))
    if failures:
        raise CheckFailed("Not published. " + "; ".join(failures))

    runner.publish()
    runner.run_scripts(SNAPSHOT_SCRIPTS)
    runner.run_scripts(VIEWS)
    log.info("done")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--render", metavar="MODEL", help="print one model's rendered SQL and exit")
    parser.add_argument("--skip-reconcile", action="store_true", help="skip the USAspending aggregate check")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.render:
        path = next(p for p in MODELS + SNAPSHOT_SCRIPTS + VIEWS if model_name(p) == args.render)
        print(render(read_sql(path), scratch=path in MODELS))
        return 0

    from google.cloud import bigquery

    try:
        run(bigquery.Client(project=PROJECT), skip_reconcile=args.skip_reconcile)
    except CheckFailed as exc:
        log.error("%s", exc)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
