"""Thin client for the USAspending.gov award search API.

Only the one endpoint the pipeline needs: POST /api/v2/search/spending_by_award/.
No API key is required. Docs: https://api.usaspending.gov/docs/endpoints
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Iterable

import pandas as pd
import requests

API_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"

# Contract award types: A = BPA call, B = purchase order, C = delivery order, D = definitive contract.
CONTRACT_AWARD_TYPES = ["A", "B", "C", "D"]

# Niche NAICS codes (see docs/definitions.md, section 2). PSC and description rules
# are applied later, in SQL, so the raw layer keeps everything the API returned.
NICHE_NAICS = ["561311", "561312"]

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

    def body(self, page: int) -> dict:
        return {
            "filters": {
                "naics_codes": self.naics_codes,
                "time_period": [{"start_date": self.start_date, "end_date": self.end_date}],
                "award_type_codes": self.award_types,
            },
            "fields": FIELDS,
            "sort": "Award Amount",
            "order": "desc",
            "limit": self.page_size,
            "page": page,
        }


def _post_with_retry(session: requests.Session, body: dict, retries: int = 4, timeout: int = 90) -> dict:
    """POST once, retrying on network errors, 429 and 5xx with exponential backoff."""
    delay = 5.0
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = session.post(API_URL, json=body, timeout=timeout)
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


def fetch_awards(search: AwardSearch, session: requests.Session | None = None) -> list[dict]:
    """Return every award matching the search, following pagination."""
    session = session or requests.Session()
    results: list[dict] = []
    for page in range(1, search.max_pages + 1):
        payload = _post_with_retry(session, search.body(page))
        results.extend(payload.get("results") or [])
        if not (payload.get("page_metadata") or {}).get("hasNext"):
            return results
    raise USAspendingError(f"Pagination did not end after {search.max_pages} pages; refusing to continue.")


def _code(obj, key: str):
    return obj.get(key) if isinstance(obj, dict) else None


def to_frame(records: Iterable[dict]) -> pd.DataFrame:
    """Flatten API records into the raw-table schema. No business rules here."""
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
