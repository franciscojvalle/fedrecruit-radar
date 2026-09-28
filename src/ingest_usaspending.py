"""Daily ingest: USAspending -> Cloud Storage (raw parquet) -> BigQuery (raw tables).

Two pulls over the same niche and window:
  awards        one row per contract, current state        -> raw_awards
  transactions  one row per contract action (modification) -> raw_transactions

Every run pulls the full lookback window, so a run is its own backfill and re-running
the same day is safe:

  gs://<bucket>/raw/usaspending/snapshot_date=YYYY-MM-DD/awards.parquet        (immutable audit copy)
  gs://<bucket>/raw/usaspending/snapshot_date=YYYY-MM-DD/transactions.parquet
  <project>.<dataset>.raw_awards / raw_transactions, partition snapshot_date   (replaced on re-run)

Nothing is written unless BOTH pulls pass validation.

Usage:
  python -m src.ingest_usaspending               # full run (needs GCP credentials)
  python -m src.ingest_usaspending --local-only  # fetch + validate + write ./data/, no cloud
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import sys
from pathlib import Path

import pandas as pd

from src.usaspending import (
    AwardSearch,
    expected_count,
    fetch_awards,
    fetch_transactions,
    to_frame,
    to_tx_frame,
)

log = logging.getLogger("ingest")

PROJECT = os.environ.get("GCP_PROJECT", "fedrecruit-radar")
BUCKET = os.environ.get("GCS_BUCKET", "fedrecruit-raw")
DATASET = os.environ.get("BQ_DATASET", "fedrecruit")
RAW_TABLE = "raw_awards"
TX_TABLE = "raw_transactions"

# Start of the lookback window: 1 Oct of the fiscal year five years back (FY2022 when run in FY2026).
LOOKBACK_FISCAL_YEARS = int(os.environ.get("LOOKBACK_FISCAL_YEARS", "5"))

MIN_ROWS = int(os.environ.get("MIN_ROWS", "50"))  # the niche has ~280 awards; far fewer means the API misbehaved


class ValidationError(RuntimeError):
    pass


def fiscal_year(d: dt.date) -> int:
    return d.year + 1 if d.month >= 10 else d.year


def window(today: dt.date) -> tuple[str, str]:
    start_fy = fiscal_year(today) - LOOKBACK_FISCAL_YEARS + 1
    start = dt.date(start_fy - 1, 10, 1)
    return start.isoformat(), today.isoformat()


# --- Validation (runs before anything is written) ------------------------------------

def validate(df: pd.DataFrame, expected: int | None = None) -> None:
    """Awards: enough rows, complete pull, one row per award."""
    if len(df) < MIN_ROWS:
        raise ValidationError(f"Only {len(df)} rows returned (minimum {MIN_ROWS}). Not writing anything.")
    if expected is not None and len(df) != expected:
        raise ValidationError(f"Awards pull incomplete: got {len(df)}, API count says {expected}.")
    dupes = df["award_key"].duplicated().sum()
    if dupes:
        raise ValidationError(f"{dupes} duplicate award_key values in one pull.")
    missing = df["award_key"].isna().sum()
    if missing:
        raise ValidationError(f"{missing} rows without award_key.")


def validate_transactions(tx: pd.DataFrame, awards: pd.DataFrame, expected: int | None = None) -> None:
    """Transactions: complete pull, one row per (award, modification), every row belongs to a pulled award."""
    if expected is not None and len(tx) != expected:
        raise ValidationError(f"Transactions pull incomplete: got {len(tx)}, API count says {expected}.")
    for col in ("award_key", "action_date"):
        missing = tx[col].isna().sum()
        if missing:
            raise ValidationError(f"{missing} transactions without {col}.")
    dupes = tx.duplicated(subset=["award_key", "modification_number"]).sum()
    if dupes:
        raise ValidationError(f"{dupes} duplicate (award_key, modification_number) pairs.")
    orphans = ~tx["award_key"].isin(awards["award_key"])
    if orphans.any():
        raise ValidationError(f"{orphans.sum()} transactions belong to awards not in the awards pull.")


# --- Writers ----------------------------------------------------------------------

def write_local(df: pd.DataFrame, snapshot: dt.date, name: str = "awards", root: Path = Path("data")) -> Path:
    path = root / "raw" / "usaspending" / f"snapshot_date={snapshot.isoformat()}" / f"{name}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    return path


def upload_to_gcs(local_path: Path, snapshot: dt.date) -> str:
    from google.cloud import storage

    blob_name = f"raw/usaspending/snapshot_date={snapshot.isoformat()}/{local_path.name}"
    storage.Client(project=PROJECT).bucket(BUCKET).blob(blob_name).upload_from_filename(str(local_path))
    return f"gs://{BUCKET}/{blob_name}"


def _schema(kind: str):
    from google.cloud import bigquery

    F = bigquery.SchemaField
    if kind == "awards":
        return [
            F("snapshot_date", "DATE", mode="REQUIRED"),
            F("award_key", "STRING", mode="REQUIRED"),
            F("award_id", "STRING"),
            F("recipient_name", "STRING"),
            F("recipient_uei", "STRING"),
            F("awarding_agency", "STRING"),
            F("awarding_sub_agency", "STRING"),
            F("award_amount", "FLOAT64"),
            F("total_outlays", "FLOAT64"),
            F("start_date", "DATE"),
            F("end_date", "DATE"),
            F("naics_code", "STRING"),
            F("naics_description", "STRING"),
            F("psc_code", "STRING"),
            F("psc_description", "STRING"),
            F("description", "STRING"),
            F("contract_award_type", "STRING"),
            F("last_modified_date", "TIMESTAMP"),
            F("base_obligation_date", "DATE"),
        ]
    return [
        F("snapshot_date", "DATE", mode="REQUIRED"),
        F("award_key", "STRING", mode="REQUIRED"),
        F("award_id", "STRING"),
        F("modification_number", "STRING"),
        F("action_date", "DATE", mode="REQUIRED"),
        F("action_type", "STRING"),
        F("obligation_amount", "FLOAT64"),
        F("transaction_description", "STRING"),
        F("recipient_name", "STRING"),
        F("recipient_uei", "STRING"),
        F("awarding_agency", "STRING"),
        F("awarding_sub_agency", "STRING"),
        F("contract_award_type", "STRING"),
        F("naics_code", "STRING"),
        F("psc_code", "STRING"),
    ]


TABLES = {
    "awards": (RAW_TABLE, "One row per niche award per daily snapshot, exactly as returned by USAspending."),
    "transactions": (TX_TABLE, "One row per contract action (modification) per daily snapshot; "
                               "obligation_amount can be negative (de-obligation)."),
}


def load_to_bigquery(gcs_uri: str, snapshot: dt.date, kind: str = "awards") -> int:
    """Replace today's partition of the raw table with the file just uploaded."""
    from google.cloud import bigquery

    client = bigquery.Client(project=PROJECT)
    table_name, description = TABLES[kind]
    table_id = f"{PROJECT}.{DATASET}.{table_name}"
    schema = _schema(kind)

    table = bigquery.Table(table_id, schema=schema)
    table.time_partitioning = bigquery.TimePartitioning(field="snapshot_date")
    table.description = description
    client.create_table(table, exists_ok=True)

    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.PARQUET,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,  # only this partition
        schema=schema,
    )
    partition = f"{table_id}${snapshot.strftime('%Y%m%d')}"
    job = client.load_table_from_uri(gcs_uri, partition, job_config=job_config)
    job.result()
    return job.output_rows


# --- Orchestration ---------------------------------------------------------------------

def run(today: dt.date, local_only: bool) -> None:
    start, end = window(today)
    search = AwardSearch(start_date=start, end_date=end)
    log.info("Window: activity between %s and %s", start, end)

    awards = to_frame(fetch_awards(search))
    awards.insert(0, "snapshot_date", today)
    tx = to_tx_frame(fetch_transactions(search))
    tx.insert(0, "snapshot_date", today)
    log.info("Fetched %d awards and %d transactions", len(awards), len(tx))

    validate(awards, expected_count(search, "awards"))
    validate_transactions(tx, awards, expected_count(search, "transactions"))
    log.info("Validation passed")

    paths = {"awards": write_local(awards, today, "awards"),
             "transactions": write_local(tx, today, "transactions")}
    if local_only:
        log.info("Local only: wrote %s", ", ".join(str(p) for p in paths.values()))
        return

    for kind, path in paths.items():
        uri = upload_to_gcs(path, today)
        rows = load_to_bigquery(uri, today, kind)
        log.info("%s: %s -> %s.%s partition %s (%d rows)", kind, uri, DATASET, TABLES[kind][0], today, rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--local-only", action="store_true", help="fetch and validate; write ./data only")
    parser.add_argument("--date", help="snapshot date (YYYY-MM-DD); default today UTC")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    today = dt.date.fromisoformat(args.date) if args.date else dt.datetime.now(dt.timezone.utc).date()
    try:
        run(today, args.local_only)
    except ValidationError as exc:
        log.error("Validation failed: %s", exc)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
