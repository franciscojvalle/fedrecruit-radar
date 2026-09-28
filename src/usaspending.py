"""Thin client for the USAspending.gov search API.

Endpoints used (no API key required, docs: https://api.usaspending.gov/docs/endpoints):
  POST /api/v2/search/spending_by_award/              one row per award (current state)
  POST /api/v2/search/spending_by_transaction/        one row per contract action (modification)
  POST /api/v2/search/spending_by_award_count/        expected row counts, used to prove a pull is complete
  POST /api/v2/search/spending_by_transaction_count/
"""

from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass, field, replace
from typing import Callable, Iterable

import pandas as pd
import requests

BASE_URL = "https://api.usaspending.gov/api/v2/search"
AWARDS_URL = f"{BASE_URL}/spending_by_award/"
TRANSACTIONS_URL = f"{BASE_URL}/spending_by_transaction/"
AWARDS_COUNT_URL = f"{BASE_URL}/spending_by_award_count/"
TRANSACTIONS_COUNT_URL = f"{BASE_URL}/spending_by_transaction_count/"
API_URL = AWARDS_URL  # backwards-compatible alias

# Contract award types: A = BPA call, B = purchase order, C = delivery order, D = definitive contract.
CONTRACT_AWARD_TYPES = ["A", "B", "C", "D"]

# Niche NAICS codes (see docs/definitions.md, section 2). PSC and description rules
# are applied later, in SQL, so the raw layer keeps everything the API returned.
NICHE_NAICS = ["561311", "561312"]

# --- Awards -------------------------------------------------------------------

FIELDS = [
    "Award ID",
    "generated_internal_id",
    "Recipient Name",
    "Recipient UEI",
    "Awarding Agency",
    "Awarding Sub Agency",
    "Award Amount",
    "Total Outlays",
    "Start Date",
    "End Date",
    "NAICS",
    "PSC",
    "Description",
    "Contract Award Type",
    "Last Modified Date",
    "Base Obligation Date",
]

# API field name -> snake_case column name in the raw table.
COLUMN_MAP = {
    "generated_internal_id": "award_key",
    "Award ID": "award_id",
    "Recipient Name": "recipient_name",
    "Recipient UEI": "recipient_uei",
    "Awarding Agency": "awarding_agency",
    "Awarding Sub Agency": "awarding_sub_agency",
    "Award Amount": "award_amount",
    "Total Outlays": "total_outlays",
    "Start Date": "start_date",
    "End Date": "end_date",
    "Description": "description",
    "Contract Award Type": "contract_award_type",
    "Last Modified Date": "last_modified_date",
    "Base Obligation Date": "base_obligation_date",
}

RAW_COLUMNS = [
    "award_key",
    "award_id",
    "recipient_name",
    "recipient_uei",
    "awarding_agency",
    "awarding_sub_agency",
    "award_amount",
    "total_outlays",
    "start_date",
    "end_date",
    "naics_code",
    "naics_description",
    "psc_code",
    "psc_description",
    "description",
    "contract_award_type",
    "last_modified_date",
    "base_obligation_date",
]

# --- Transactions ----------------------------------------------------------------

TX_FIELDS = [
    "generated_internal_id",
    "Award ID",
    "Mod",
    "Action Date",
    "Action Type",
    "Transaction Amount",
    "Transaction Description",
    "Recipient Name",
    "Recipient UEI",
    "Awarding Agency",
    "Awarding Sub Agency",
    "Award Type",
    "naics_code",
    "product_or_service_code",
]

TX_COLUMN_MAP = {
    "generated_internal_id": "award_key",
    "Award ID": "award_id",
    "Mod": "modification_number",
    "Action Date": "action_date",
    "Action Type": "action_type",  # FPDS code; null on the base award (mod 0)
    "Transaction Amount": "obligation_amount",  # can be negative (de-obligation)
    "Transaction Description": "transaction_description",
    "Recipient Name": "recipient_name",
    "Recipient UEI": "recipient_uei",
    "Awarding Agency": "awarding_agency",
    "Awarding Sub Agency": "awarding_sub_agency",
    "Award Type": "contract_award_type",
    "naics_code": "naics_code",
    "product_or_service_code": "psc_code",
}

TX_COLUMNS = list(TX_COLUMN_MAP.values())


class USAspendingError(RuntimeError):
    pass


@dataclass
class AwardSearch:
    start_date: str
    end_date: str
    naics_codes: list[str] = field(default_factory=lambda: list(NICHE_NAICS))
    award_types: list[str] = field(default_factory=lambda: list(CONTRACT_AWARD_TYPES))
    page_size: int = 100
    max_pages: int = 200  # hard stop: 20,000 rows is far beyond the niche's size

    def filters(self) -> dict:
        return {
            "naics_codes": self.naics_codes,
            "time_period": [{"start_date": self.start_date, "end_date": self.end_date}],
            "award_type_codes": self.award_types,
        }

    def body(self, page: int) -> dict:
        """Request body for the awards endpoint."""
        return {
            "filters": self.filters(),
            "fields": FIELDS,
            "sort": "Award Amount",
            "order": "desc",
            "limit": self.page_size,
            "page": page,
        }

    def tx_body(self, page: int) -> dict:
        """Request body for the transactions endpoint (same filters, so both pulls cover the same niche)."""
        return {
            "filters": self.filters(),
            "fields": TX_FIELDS,
            "sort": "Action Date",
            "order": "asc",
            "limit": self.page_size,
            "page": page,
        }


def _post_with_retry(session, body: dict, url: str = AWARDS_URL, retries: int = 4, timeout: int = 90) -> dict:
    """POST once, retrying on network errors, 429 and 5xx with exponential backoff."""
    delay = 5.0
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = session.post(url, json=body, timeout=timeout)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 429 or resp.status_code >= 500:
                last_err = USAspendingError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            else:
                # 4xx other than 429 is a bug in our request; retrying won't help.
                raise USAspendingError(f"HTTP {resp.status_code}: {resp.text[:500]}")
        except requests.RequestException as exc:
            last_err = exc
        if attempt < retries:
            time.sleep(delay)
            delay *= 2
    raise USAspendingError(f"USAspending request failed after {retries + 1} attempts: {last_err}")


def _fetch_pages(url: str, body_for_page: Callable[[int], dict], max_pages: int, session) -> list[dict]:
    results: list[dict] = []
    for page in range(1, max_pages + 1):
        payload = _post_with_retry(session, body_for_page(page), url=url)
        results.extend(payload.get("results") or [])
        if not (payload.get("page_metadata") or {}).get("hasNext"):
            return results
    raise USAspendingError(f"Pagination did not end after {max_pages} pages; refusing to continue.")


def fetch_awards(search: AwardSearch, session=None) -> list[dict]:
    """Return every award matching the search, following pagination."""
    return _fetch_pages(AWARDS_URL, search.body, search.max_pages, session or requests.Session())


def fetch_transactions(search: AwardSearch, session=None) -> list[dict]:
    """Return every contract action (modification) matching the search.

    The API's sort is not stable when values tie, so paging through a large result can
    return one row twice and skip another. Instead, the date range is split in half until
    each slice fits in a single page. A transaction has exactly one action date, so the
    slices never overlap and no paging order is involved.
    """
    return _fetch_tx_range(search, session or requests.Session())


def _fetch_tx_range(search: AwardSearch, session) -> list[dict]:
    payload = _post_with_retry(session, search.tx_body(1), url=TRANSACTIONS_URL)
    results = payload.get("results") or []
    if not (payload.get("page_metadata") or {}).get("hasNext"):
        return results

    start = dt.date.fromisoformat(search.start_date)
    end = dt.date.fromisoformat(search.end_date)
    if start >= end:
        # One day with more than a page of actions: nothing left to split, so page through it.
        return _fetch_pages(TRANSACTIONS_URL, search.tx_body, search.max_pages, session)

    mid = start + (end - start) // 2
    left = replace(search, start_date=start.isoformat(), end_date=mid.isoformat())
    right = replace(search, start_date=(mid + dt.timedelta(days=1)).isoformat(), end_date=end.isoformat())
    return _fetch_tx_range(left, session) + _fetch_tx_range(right, session)


def expected_count(search: AwardSearch, kind: str, session=None) -> int:
    """How many rows the API says the search should return ('awards' or 'transactions')."""
    url = {"awards": AWARDS_COUNT_URL, "transactions": TRANSACTIONS_COUNT_URL}[kind]
    payload = _post_with_retry(session or requests.Session(), {"filters": search.filters()}, url=url)
    return int((payload.get("results") or {}).get("contracts", 0))


def _code(obj, key: str):
    return obj.get(key) if isinstance(obj, dict) else None


def to_frame(records: Iterable[dict]) -> pd.DataFrame:
    """Flatten award records into the raw_awards schema. No business rules here."""
    rows = []
    for rec in records:
        row = {col: rec.get(api) for api, col in COLUMN_MAP.items()}
        row["naics_code"] = _code(rec.get("NAICS"), "code")
        row["naics_description"] = _code(rec.get("NAICS"), "description")
        row["psc_code"] = _code(rec.get("PSC"), "code")
        row["psc_description"] = _code(rec.get("PSC"), "description")
        rows.append(row)

    df = pd.DataFrame(rows, columns=RAW_COLUMNS)
    for col in ("award_amount", "total_outlays"):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")
    for col in ("start_date", "end_date", "base_obligation_date"):
        df[col] = pd.to_datetime(df[col], errors="coerce").dt.date
    df["last_modified_date"] = pd.to_datetime(df["last_modified_date"], errors="coerce")
    str_cols = [c for c in RAW_COLUMNS if c not in
                ("award_amount", "total_outlays", "start_date", "end_date",
                 "base_obligation_date", "last_modified_date")]
    df[str_cols] = df[str_cols].astype("string")
    return df


def to_tx_frame(records: Iterable[dict]) -> pd.DataFrame:
    """Flatten transaction records into the raw_transactions schema. No business rules here."""
    rows = [{col: rec.get(api) for api, col in TX_COLUMN_MAP.items()} for rec in records]
    df = pd.DataFrame(rows, columns=TX_COLUMNS)
    df["obligation_amount"] = pd.to_numeric(df["obligation_amount"], errors="coerce").astype("float64")
    df["action_date"] = pd.to_datetime(df["action_date"], errors="coerce").dt.date
    str_cols = [c for c in TX_COLUMNS if c not in ("obligation_amount", "action_date")]
    df[str_cols] = df[str_cols].astype("string")
    return df
