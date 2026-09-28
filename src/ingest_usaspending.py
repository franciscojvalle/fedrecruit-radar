"""Daily ingest: USAspending -> Cloud Storage (raw parquet) -> BigQuery (raw_awards).

Every run pulls the full niche for the lookback window, so a run is its own backfill
and re-running the same day is safe:

  gs://<bucket>/raw/usaspending/snapshot_date=YYYY-MM-DD/awards.parquet   (immutable audit copy)
  <project>.<dataset>.raw_awards  partition snapshot_date=YYYY-MM-DD      (replaced on re-run)

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

from src.usaspending import AwardSearch, fetch_awards, to_frame

log = logging.getLogger("ingest")

PROJECT = os.environ.get("GCP_PROJECT", "fedrecruit-radar")
BUCKET = os.environ.get("GCS_BUCKET", "fedrecruit-raw")
DATASET = os.environ.get("BQ_DATASET", "fedrecruit")
RAW_TABLE = "raw_awards"

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


def validate(df: pd.DataFrame) -> None:
    """Stop the run before anything is written if the pull looks wrong."""
    if len(df) < MIN_ROWS:
        raise ValidationError(f"Only {len(df)} rows returned (minimum {MIN_ROWS}). Not writing anything.")
    dupes = df["award_key"].duplicated().sum()
    if dupes:
        raise ValidationError(f"{dupes} duplicate award_key values in one pull.")
    missing = df["award_key"].isna().sum()
    if missing:
        raise ValidationError(f"{missing} rows without award_key.")


def write_local(df: pd.DataFrame, snapshot: dt.date, root: Path = Path("data")) -> Path:
    path = root / "raw" / "usaspending" / f"snapshot_date={snapshot.isoformat()}" / "awards.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    return path


def upload_to_gcs(local_path: Path, snapshot: dt.date) -> str:
    from google.cloud import storage

    blob_name = f"raw/usaspending/snapshot_date={snapshot.isoformat()}/awards.parquet"
    storage.Client(project=PROJECT).bucket(BUCKET).blob(blob_name).upload_from_filename(str(local_path))
    return f"gs://{BUCKET}/{blob_name}"


def load_to_bigquery(gcs_uri: str, snapshot: dt.date) -> int:
    """Replace today's partition of raw_awards with the file just uploaded."""
    from google.cloud import bigquery

    client = bigquery.Client(project=PROJECT)
    table_id = f"{PROJECT}.{DATASET}.{RAW_TABLE}"

    schema = [
        bigquery.SchemaField("snapshot_date", "DATE", mode="REQUIRED"),
        bigquery.SchemaField("award_key", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("award_id", "STRING"),
        bigquery.SchemaField("recipient_name", "STRING"),
        bigquery.SchemaField("recipient_uei", "STRING"),
        bigquery.SchemaField("awarding_agency", "STRING"),
        bigquery.SchemaField("awarding_sub_agency", "STRING"),
        bigquery.SchemaField("award_amount", "FLOAT64"),
        bigquery.SchemaField("total_outlays", "FLOAT64"),
        bigquery.SchemaField("start_date", "DATE"),
        bigquery.SchemaField("end_date", "DATE"),
        bigquery.SchemaField("naics_code", "STRING"),
        bigquery.SchemaField("naics_description", "STRING"),
        bigquery.SchemaField("psc_code", "STRING"),
        bigquery.SchemaField("psc_description", "STRING"),
        bigquery.SchemaField("description", "STRING"),
        bigquery.SchemaField("contract_award_type", "STRING"),
        bigquery.SchemaField("last_modified_date", "TIMESTAMP"),
        bigquery.SchemaField("base_obligation_date", "DATE"),
    ]

    table = bigquery.Table(table_id, schema=schema)
    table.time_partitioning = bigquery.TimePartitioning(field="snapshot_date")
    table.description = "One row per niche award per daily snapshot, exactly as returned by USAspending."
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


def run(today: dt.date, local_only: bool) -> None:
    start, end = window(today)
    log.info("Fetching niche awards with activity between %s and %s", start, end)
    records = fetch_awards(AwardSearch(start_date=start, end_date=end))
    df = to_frame(records)
    df.insert(0, "snapshot_date", today)
    log.info("Fetched %d awards", len(df))

    validate(df)
    local_path = write_local(df, today)
    log.info("Wrote %s", local_path)

    if local_only:
        return
    uri = upload_to_gcs(local_path, today)
    log.info("Uploaded %s", uri)
    rows = load_to_bigquery(uri, today)
    log.info("Loaded %d rows into %s.%s partition %s", rows, DATASET, RAW_TABLE, today)


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
