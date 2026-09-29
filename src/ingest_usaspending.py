"""Daily ingest: USAspending -> Cloud Storage (raw parquet) -> BigQuery (raw tables).

Three pulls over the same niche and window:
  awards        one row per contract, current state        -> raw_awards
  transactions  one row per contract action (modification) -> raw_transactions
  details       one row per still-running contract: options,
                ceiling, set-aside, bids (one request each) -> raw_award_details

Every run pulls the full lookback window, so a run is its own backfill and re-running
the same day is safe:

  gs://<bucket>/raw/usaspending/snapshot_date=YYYY-MM-DD/awards.parquet        (immutable audit copy)
  gs://<bucket>/raw/usaspending/snapshot_date=YYYY-MM-DD/transactions.parquet
  <project>.<dataset>.raw_awards / raw_transactions, partition snapshot_date   (replaced on re-run)

Nothing is written unless ALL pulls pass validation.

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
    fetch_award_detail,
    fetch_awards,
    fetch_transactions,
    to_detail_frame,
    to_detail_row,
    to_frame,
    to_tx_frame,
)

log = logging.getLogger("ingest")

PROJECT = os.environ.get("GCP_PROJECT", "fedrecruit-radar")
BUCKET = os.environ.get("GCS_BUCKET", "fedrecruit-raw")
DATASET = os.environ.get("BQ_DATASET", "fedrecruit")
RAW_TABLE = "raw_awards"
TX_TABLE = "raw_transactions"
DETAIL_TABLE = "raw_award_details"

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


def validate_details(details: pd.DataFrame, requested: list[str]) -> None:
    """Details: exactly one row for every still-running award we asked about."""
    missing = set(requested) - set(details["award_key"].dropna())
    if missing:
        raise ValidationError(f"{len(missing)} still-running awards have no detail row.")
    dupes = details["award_key"].duplicated().sum()
    if dupes:
        raise ValidationError(f"{dupes} duplicate award_key values in details.")
    no_end = details["potential_end_date"].isna().sum()
    if no_end:
        raise ValidationError(f"{no_end} details without a potential end date.")


def fetch_details(award_keys: list[str]) -> pd.DataFrame:
    """One detail request per award. Only still-running awards need it (~60 a day)."""
    import requests

    session = requests.Session()
    return to_detail_frame(to_detail_row(k, fetch_award_detail(k, session)) for k in award_keys)


# --- Complete pulls --------------------------------------------------------------------

FETCH_ATTEMPTS = int(os.environ.get("FETCH_ATTEMPTS", "3"))


def fetch_complete(fetch, to_df, key: list[str], expected: int, label: str) -> pd.DataFrame:
    """Pull until the result matches the API's own count, up to FETCH_ATTEMPTS times.

    Exact duplicate rows (the same record returned twice by an unstable page order) are
    dropped. Rows that share a key but differ in content are NOT dropped: validation
    rejects them, because that would be a real data problem, not a paging glitch.
    If every attempt comes up short, the last pull is returned and validation stops the run.
    """
    df = pd.DataFrame()
    for attempt in range(1, FETCH_ATTEMPTS + 1):
        raw = to_df(fetch())
        df = raw.drop_duplicates(ignore_index=True)
        if len(raw) != len(df):
            log.warning("%s: dropped %d exact duplicate rows", label, len(raw) - len(df))
        if len(df) == expected and not df.duplicated(subset=key).any():
            return df
        log.warning("%s attempt %d/%d: %d unique rows, API count %d; pulling again",
                    label, attempt, FETCH_ATTEMPTS, len(df), expected)
    return df


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
    if kind == "award_details":
        return [
            F("snapshot_date", "DATE", mode="REQUIRED"),
            F("award_key", "STRING", mode="REQUIRED"),
            F("current_end_date", "DATE"),
            F("potential_end_date", "DATE"),
            F("obligated_amount", "FLOAT64"),
            F("base_exercised_options_value", "FLOAT64"),
            F("base_and_all_options_value", "FLOAT64"),
            F("type_set_aside", "STRING"),
            F("type_set_aside_description", "STRING"),
            F("extent_competed_description", "STRING"),
            F("number_of_offers_received", "INT64"),
            F("solicitation_identifier", "STRING"),
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
    "award_details": (DETAIL_TABLE, "One row per still-running award per daily snapshot, from the award "
                                    "detail page: options (potential end date, ceiling), set-aside, bids."),
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

    n_awards = expected_count(search, "awards")
    n_tx = expected_count(search, "transactions")
    awards = fetch_complete(lambda: fetch_awards(search), to_frame, ["award_key"], n_awards, "awards")
    tx = fetch_complete(lambda: fetch_transactions(search), to_tx_frame,
                        ["award_key", "modification_number"], n_tx, "transactions")
    awards.insert(0, "snapshot_date", today)
    tx.insert(0, "snapshot_date", today)
    log.info("Fetched %d awards (API count %d) and %d transactions (API count %d)",
             len(awards), n_awards, len(tx), n_tx)

    validate(awards, n_awards)
    validate_transactions(tx, awards, n_tx)

    # Missing end dates count as not running (NaT compares False).
    running = awards.loc[pd.to_datetime(awards["end_date"]) >= pd.Timestamp(today), "award_key"].tolist()
    details = fetch_details(running)
    details.insert(0, "snapshot_date", today)
    validate_details(details, running)
    log.info("Validation passed (%d detail pages for still-running awards)", len(details))

    paths = {"awards": write_local(awards, today, "awards"),
             "transactions": write_local(tx, today, "transactions"),
             "award_details": write_local(details, today, "award_details")}
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
