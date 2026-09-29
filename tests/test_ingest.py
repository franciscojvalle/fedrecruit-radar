import datetime as dt
import json
from pathlib import Path

import pandas as pd
import pytest

from src import ingest_usaspending as ing
from src import usaspending as us

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE = json.loads((FIXTURES / "spending_by_award_sample.json").read_text())
TX_FIXTURE = json.loads((FIXTURES / "spending_by_transaction_sample.json").read_text())
DETAIL_FIXTURE = json.loads((FIXTURES / "award_detail_sample.json").read_text())


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


def test_validate_rejects_incomplete_pull():
    with pytest.raises(ing.ValidationError, match="incomplete"):
        ing.validate(_frame(60), expected=61)


# --- Transactions -------------------------------------------------------------------

def test_fetch_transactions_uses_transaction_endpoint():
    session = FakeSession([FakeResponse(200, {"results": TX_FIXTURE, "page_metadata": {"hasNext": False}})])
    session_urls = []
    orig_post = session.post
    session.post = lambda url, json=None, timeout=None: (session_urls.append(url), orig_post(url, json, timeout))[1]
    out = us.fetch_transactions(us.AwardSearch("2021-10-01", "2026-09-24"), session=session)
    assert len(out) == 4
    assert session_urls == [us.TRANSACTIONS_URL]
    assert "Mod" in session.bodies[0]["fields"]


def test_fetch_transactions_splits_date_range_instead_of_paging():
    """A range that doesn't fit in one page is split in half; no page 2 is ever requested."""
    session = FakeSession([
        FakeResponse(200, {"results": TX_FIXTURE[:1], "page_metadata": {"hasNext": True}}),   # full range: too big
        FakeResponse(200, {"results": TX_FIXTURE[:2], "page_metadata": {"hasNext": False}}),  # left half
        FakeResponse(200, {"results": TX_FIXTURE[2:], "page_metadata": {"hasNext": False}}),  # right half
    ])
    out = us.fetch_transactions(us.AwardSearch("2026-01-01", "2026-01-10"), session=session)
    assert len(out) == 4
    periods = [b["filters"]["time_period"][0] for b in session.bodies]
    assert periods[1] == {"start_date": "2026-01-01", "end_date": "2026-01-05"}
    assert periods[2] == {"start_date": "2026-01-06", "end_date": "2026-01-10"}
    assert all(b["page"] == 1 for b in session.bodies)


def test_fetch_transactions_pages_through_a_single_busy_day():
    session = FakeSession([
        FakeResponse(200, {"results": TX_FIXTURE[:2], "page_metadata": {"hasNext": True}}),  # probe
        FakeResponse(200, {"results": TX_FIXTURE[:2], "page_metadata": {"hasNext": True}}),  # page 1
        FakeResponse(200, {"results": TX_FIXTURE[2:], "page_metadata": {"hasNext": False}}), # page 2
    ])
    out = us.fetch_transactions(us.AwardSearch("2026-01-01", "2026-01-01"), session=session)
    assert len(out) == 4


def test_fetch_complete_drops_exact_duplicates():
    rows = TX_FIXTURE + [TX_FIXTURE[1]]  # same record returned twice by the API
    df = ing.fetch_complete(lambda: rows, us.to_tx_frame, ["award_key", "modification_number"], 4, "tx")
    assert len(df) == 4


def test_fetch_complete_retries_until_count_matches():
    pulls = iter([TX_FIXTURE[:3] + [TX_FIXTURE[1]],   # one duplicated, one missing
                  TX_FIXTURE])                        # clean
    df = ing.fetch_complete(lambda: next(pulls), us.to_tx_frame,
                            ["award_key", "modification_number"], 4, "tx")
    assert len(df) == 4


def test_fetch_complete_keeps_conflicting_duplicates_for_validation():
    changed = dict(TX_FIXTURE[1], **{"Transaction Amount": 1.0})  # same key, different content
    df = ing.fetch_complete(lambda: TX_FIXTURE + [changed], us.to_tx_frame,
                            ["award_key", "modification_number"], 4, "tx")
    with pytest.raises(ing.ValidationError):
        ing.validate_transactions(df, us.to_frame(FIXTURE), expected=4)


def test_expected_count_reads_contracts():
    session = FakeSession([FakeResponse(200, {"results": {"contracts": 715, "grants": 0}})])
    assert us.expected_count(us.AwardSearch("a", "b"), "transactions", session=session) == 715
    assert set(session.bodies[0]) == {"filters"}


def test_to_tx_frame_flattens_and_types():
    tx = us.to_tx_frame(TX_FIXTURE)
    assert list(tx.columns) == us.TX_COLUMNS
    assert tx["obligation_amount"].dtype == "float64"
    assert tx.iloc[1]["modification_number"] == "P00001"
    assert tx.iloc[1]["action_date"] == dt.date(2026, 5, 1)
    assert pd.isna(tx.iloc[0]["action_type"])          # base award has no action type
    assert tx.iloc[3]["obligation_amount"] == -1000    # de-obligations are kept, negative
    assert pd.isna(tx.iloc[3]["psc_code"])
    assert "internal_id" not in tx.columns


def _awards_and_tx():
    return us.to_frame(FIXTURE), us.to_tx_frame(TX_FIXTURE)


def test_validate_transactions_accepts_good_pull():
    awards, tx = _awards_and_tx()
    ing.validate_transactions(tx, awards, expected=4)


def test_validate_transactions_rejects_incomplete_pull():
    awards, tx = _awards_and_tx()
    with pytest.raises(ing.ValidationError, match="incomplete"):
        ing.validate_transactions(tx, awards, expected=5)


def test_validate_transactions_rejects_duplicate_mod():
    awards, tx = _awards_and_tx()
    tx = pd.concat([tx, tx.iloc[[1]]], ignore_index=True)
    with pytest.raises(ing.ValidationError, match="duplicate"):
        ing.validate_transactions(tx, awards)


def test_validate_transactions_rejects_orphans():
    awards, tx = _awards_and_tx()
    with pytest.raises(ing.ValidationError, match="not in the awards pull"):
        ing.validate_transactions(tx, awards.iloc[:1])


# --- Award details ----------------------------------------------------------------------

def fake_details(keys):
    return us.to_detail_frame(us.to_detail_row(k, DETAIL_FIXTURE) for k in keys)


def test_fetch_award_detail_uses_get():
    class GetSession:
        def __init__(self):
            self.urls = []

        def get(self, url, timeout=None):
            self.urls.append(url)
            return FakeResponse(200, DETAIL_FIXTURE)

    session = GetSession()
    out = us.fetch_award_detail("CONT_AWD_X", session=session)
    assert session.urls == ["https://api.usaspending.gov/api/v2/awards/CONT_AWD_X/"]
    assert out["piid"] == "W912LA24P0011"


def test_detail_row_flattens_options_and_set_aside():
    df = fake_details(["K1"])
    row = df.iloc[0]
    assert row["current_end_date"] == dt.date(2026, 10, 30)
    assert row["potential_end_date"] == dt.date(2029, 10, 30)
    assert row["base_and_all_options_value"] == 6823956
    assert row["type_set_aside_description"] == "8A COMPETED"
    assert row["number_of_offers_received"] == 6


def test_detail_row_handles_missing_sections():
    df = us.to_detail_frame([us.to_detail_row("K1", {})])
    assert pd.isna(df.iloc[0]["potential_end_date"]) and pd.isna(df.iloc[0]["number_of_offers_received"])


def test_validate_details_requires_every_running_award():
    df = fake_details(["K1"])
    ing.validate_details(df, ["K1"])
    with pytest.raises(ing.ValidationError, match="no detail row"):
        ing.validate_details(df, ["K1", "K2"])


def test_validate_details_requires_potential_end_date():
    df = us.to_detail_frame([us.to_detail_row("K1", {})])
    with pytest.raises(ing.ValidationError, match="potential end date"):
        ing.validate_details(df, ["K1"])


# --- End to end (local) -----------------------------------------------------------------

def test_local_only_run_writes_both_files(tmp_path, monkeypatch):
    records = [dict(FIXTURE[0], generated_internal_id=f"K{i}") for i in range(60)]
    tx_records = [dict(TX_FIXTURE[0], generated_internal_id=f"K{i}") for i in range(60)]
    monkeypatch.setattr(ing, "fetch_awards", lambda search: records)
    monkeypatch.setattr(ing, "fetch_transactions", lambda search: tx_records)
    monkeypatch.setattr(ing, "expected_count", lambda search, kind: 60)
    monkeypatch.setattr(ing, "fetch_details", fake_details)
    monkeypatch.chdir(tmp_path)
    ing.run(dt.date(2026, 9, 24), local_only=True)
    folder = tmp_path / "data/raw/usaspending/snapshot_date=2026-09-24"
    awards = pd.read_parquet(folder / "awards.parquet")
    tx = pd.read_parquet(folder / "transactions.parquet")
    assert len(awards) == 60 and awards.columns[0] == "snapshot_date"
    assert len(tx) == 60 and tx.columns[0] == "snapshot_date"
    details = pd.read_parquet(folder / "award_details.parquet")
    assert len(details) == 60   # every fixture award ends in 2027, so all are still running


def test_nothing_written_when_transactions_fail(tmp_path, monkeypatch):
    records = [dict(FIXTURE[0], generated_internal_id=f"K{i}") for i in range(60)]
    monkeypatch.setattr(ing, "fetch_awards", lambda search: records)
    monkeypatch.setattr(ing, "fetch_transactions", lambda search: TX_FIXTURE)  # orphans
    monkeypatch.setattr(ing, "expected_count", lambda search, kind: 60 if kind == "awards" else 4)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ing.ValidationError):
        ing.run(dt.date(2026, 9, 24), local_only=True)
    assert not (tmp_path / "data").exists()
