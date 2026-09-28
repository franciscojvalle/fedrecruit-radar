import datetime as dt
import json
from pathlib import Path

import pandas as pd
import pytest

from src import ingest_usaspending as ing
from src import usaspending as us

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "spending_by_award_sample.json").read_text())


class FakeResponse:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class FakeSession:
    """Serves canned responses in order and records request bodies."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.bodies = []

    def post(self, url, json=None, timeout=None):
        self.bodies.append(json)
        return self.responses.pop(0)


# --- API client ---------------------------------------------------------------

def test_fetch_follows_pagination():
    session = FakeSession([
        FakeResponse(200, {"results": FIXTURE[:2], "page_metadata": {"hasNext": True}}),
        FakeResponse(200, {"results": FIXTURE[2:], "page_metadata": {"hasNext": False}}),
    ])
    out = us.fetch_awards(us.AwardSearch("2021-10-01", "2026-09-24"), session=session)
    assert len(out) == 3
    assert [b["page"] for b in session.bodies] == [1, 2]


def test_request_body_uses_niche_filters():
    body = us.AwardSearch("2021-10-01", "2026-09-24").body(1)
    assert body["filters"]["naics_codes"] == ["561311", "561312"]
    assert body["filters"]["award_type_codes"] == ["A", "B", "C", "D"]
    assert "generated_internal_id" in body["fields"]


def test_retries_on_server_error(monkeypatch):
    monkeypatch.setattr(us.time, "sleep", lambda s: None)
    session = FakeSession([
        FakeResponse(503, {"detail": "busy"}),
        FakeResponse(200, {"results": FIXTURE, "page_metadata": {"hasNext": False}}),
    ])
    assert len(us.fetch_awards(us.AwardSearch("a", "b"), session=session)) == 3


def test_client_error_is_not_retried(monkeypatch):
    monkeypatch.setattr(us.time, "sleep", lambda s: None)
    session = FakeSession([FakeResponse(400, {"detail": "bad filter"})])
    with pytest.raises(us.USAspendingError, match="HTTP 400"):
        us.fetch_awards(us.AwardSearch("a", "b"), session=session)


def test_runaway_pagination_is_stopped():
    page = FakeResponse(200, {"results": FIXTURE[:1], "page_metadata": {"hasNext": True}})
    session = FakeSession([page] * 3)
    with pytest.raises(us.USAspendingError, match="Pagination"):
        us.fetch_awards(us.AwardSearch("a", "b", max_pages=3), session=session)


# --- Flattening -----------------------------------------------------------------

def test_to_frame_flattens_and_types():
    df = us.to_frame(FIXTURE)
    assert list(df.columns) == us.RAW_COLUMNS
    first = df.iloc[0]
    assert first["award_key"] == "CONT_AWD_HQ003426FE151_9700_HQ003426DE012_9700"
    assert first["naics_code"] == "561312"
    assert first["psc_code"] == "R431"
    assert first["end_date"] == dt.date(2027, 3, 31)
    assert first["award_amount"] == 17250000
    assert pd.isna(first["total_outlays"])


def test_to_frame_handles_missing_psc():
    df = us.to_frame(FIXTURE)
    assert pd.isna(df.iloc[2]["psc_code"])


def test_to_frame_drops_unrequested_fields():
    df = us.to_frame(FIXTURE)
    assert "agency_slug" not in df.columns and "internal_id" not in df.columns


# --- Window and validation --------------------------------------------------------

@pytest.mark.parametrize("today,expected_start", [
    (dt.date(2026, 9, 24), "2021-10-01"),  # FY2026 -> FY2022..FY2026
    (dt.date(2026, 10, 1), "2022-10-01"),  # FY2027 -> FY2023..FY2027
])
def test_window_is_five_fiscal_years(today, expected_start):
    start, end = ing.window(today)
    assert start == expected_start and end == today.isoformat()


def _frame(n):
    rows = [dict(FIXTURE[0], generated_internal_id=f"K{i}") for i in range(n)]
    return us.to_frame(rows)


def test_validate_rejects_too_few_rows():
    with pytest.raises(ing.ValidationError, match="Only 10 rows"):
        ing.validate(_frame(10))


def test_validate_rejects_duplicates():
    df = pd.concat([_frame(60), _frame(1)], ignore_index=True)
    with pytest.raises(ing.ValidationError, match="duplicate"):
        ing.validate(df)


def test_validate_accepts_normal_pull():
    ing.validate(_frame(280))


def test_local_only_run_writes_parquet(tmp_path, monkeypatch):
    records = [dict(FIXTURE[0], generated_internal_id=f"K{i}") for i in range(60)]
    monkeypatch.setattr(ing, "fetch_awards", lambda search: records)
    monkeypatch.chdir(tmp_path)
    ing.run(dt.date(2026, 9, 24), local_only=True)
    out = tmp_path / "data/raw/usaspending/snapshot_date=2026-09-24/awards.parquet"
    df = pd.read_parquet(out)
    assert len(df) == 60 and df.columns[0] == "snapshot_date"
