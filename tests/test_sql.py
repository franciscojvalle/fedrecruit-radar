"""Run the real SQL models on small, hand-built data, locally, with no cloud.

The models are BigQuery SQL. sqlglot translates them to DuckDB, and DuckDB runs them in memory
on fixture tables built to exercise every rule (niche exclusions, successors, statuses,
masked vendors, an older snapshot that must be ignored). The same data tests that gate
publishing in BigQuery (sql/tests/*.sql) must pass here too.
"""

import datetime as dt

import pytest

duckdb = pytest.importorskip("duckdb")
sqlglot = pytest.importorskip("sqlglot")

from src import run as r  # noqa: E402

AS_OF = dt.date(2026, 9, 28)
D = dt.date


def award(key, *, sub="Washington Headquarters Services", agency="Department of Defense",
          uei="UEI000000001", name="VENDOR ONE", amount=100000.0, start=D(2025, 1, 1), end=D(2027, 6, 30),
          award_date=None, naics="561312", psc="R431", desc="EXECUTIVE SEARCH SERVICES", snapshot=AS_OF):
    return dict(snapshot_date=snapshot, award_key=key, award_id=key, recipient_name=name, recipient_uei=uei,
                awarding_agency=agency, awarding_sub_agency=sub, award_amount=amount, total_outlays=None,
                start_date=start, end_date=end, naics_code=naics, naics_description=None, psc_code=psc,
                psc_description=None, description=desc, contract_award_type="DELIVERY ORDER",
                last_modified_date=dt.datetime(2026, 9, 1), base_obligation_date=award_date or start)


AWARDS = [
    # Expires in ~9 months, no successor -> expiring_12m, on the recompete list.
    award("A1", end=D(2027, 6, 30)),
    # Ends in ~2 months -> expiring_6m (act now).
    award("A7", end=D(2026, 11, 30), sub="Department of State", agency="Department of State",
          uei="UEI000000007", name="VENDOR SEVEN", desc="FOREIGN SERVICE RECRUITMENT SERVICES"),
    # Ended 11 days ago, no successor yet -> expired_pending.
    award("A2", end=D(2026, 9, 17), start=D(2025, 4, 18)),
    # Ended Jan 2025; A4 (same sub-agency + subtype, other vendor) awarded 1 month later -> recompeted.
    award("A3", end=D(2025, 1, 1), start=D(2023, 1, 1), sub="Army", uei="UEI00000000A", name="OLD INCUMBENT",
          desc="RECRUITING SERVICES"),
    award("A4", end=D(2028, 1, 31), start=D(2025, 2, 1), sub="Army", uei="UEI00000000B", name="NEW WINNER",
          desc="RECRUITING SERVICES"),
    # Ended 2024, nothing after it -> expired_no_successor.
    award("A8", end=D(2024, 3, 1), start=D(2023, 3, 1), sub="Smithsonian", agency="Smithsonian Institution",
          uei="UEI000000008", name="VENDOR EIGHT", desc="SERVICES (OPS) DIRECTOR"),
    # Masked recipient and $0 amount: kept, flagged.
    award("A9", amount=0.0, sub="USAID", agency="Agency for International Development", uei="UEI000000009",
          name="FOREIGN AWARDEES (UNDISCLOSED)", desc="USPSC CONTRACT FOR THE DEPUTY MISSION DIRECTOR"),
    # IT words but clearly recruiting -> override keeps it.
    award("A10", desc="EXECUTIVE SEARCH FOR A SYSTEMS SUPPORT DIRECTOR", uei="UEI000000010", name="VENDOR TEN"),
    # IGF:: label with recruiting work -> kept (the IGF:: prefix is not an exclusion).
    award("A11", desc="IGF::CT::IGF MARKETING AND RECRUITING SUPPORT SERVICES", uei="UEI000000011",
          name="VENDOR ELEVEN", sub="Army Reserve"),
    # Exclusions.
    award("X1", desc="LINKEDIN LICENSES AND JOB POSTINGS", uei="UEI0000000X1", name="LINKEDIN"),
    award("X2", desc="IGF::OT::IGF SALESFORCE IT SUPPORT", uei="UEI0000000X2", name="IT CO"),
    award("X3", desc="BPA CALL 0008", uei="UEI0000000X3", name="CALL CO"),
    award("X4", psc="R701", desc="RECRUITING ADVERTISING CAMPAIGN", uei="UEI0000000X4", name="AD CO"),
    award("X5", desc="COMMERCIAL ADVERTISING FOR NAVFAC", psc="R699", uei="UEI0000000X5", name="AD CO 2"),
    # Older snapshot row for A1 with different values: must be ignored (latest snapshot only).
    award("A1", amount=1.0, snapshot=D(2026, 9, 27)),
]


def tx(key, mod, date, amount, uei="UEI000000001", agency="Department of Defense", sub="Washington Headquarters Services"):
    return dict(snapshot_date=AS_OF, award_key=key, award_id=key, modification_number=mod, action_date=date,
                action_type=None if mod == "0" else "B", obligation_amount=amount, transaction_description="",
                recipient_name="X", recipient_uei=uei, awarding_agency=agency, awarding_sub_agency=sub,
                contract_award_type="DELIVERY ORDER", naics_code="561312", psc_code="R431")


TRANSACTIONS = [
    tx("A1", "0", D(2025, 1, 15), 60000.0),
    tx("A1", "P00001", D(2025, 10, 5), 40000.0),       # FY2026
    tx("A2", "0", D(2025, 4, 20), 100000.0),
    tx("A4", "0", D(2025, 2, 1), 100000.0, uei="UEI00000000B", sub="Army"),
    tx("A4", "P00001", D(2026, 3, 1), -5000.0, uei="UEI00000000B", sub="Army"),  # de-obligation
    tx("X1", "0", D(2025, 6, 1), 100000.0, uei="UEI0000000X1"),
]


def detail(key, end, potential, set_aside=None, offers=None, ceiling=None):
    return dict(snapshot_date=AS_OF, award_key=key, current_end_date=end, potential_end_date=potential,
                obligated_amount=100000.0, base_exercised_options_value=100000.0,
                base_and_all_options_value=ceiling, type_set_aside=None,
                type_set_aside_description=set_aside, extent_competed_description=None,
                number_of_offers_received=offers, solicitation_identifier=None)


# Detail pages exist only for still-running awards (end date >= snapshot date), as in the ingest.
DETAILS = [
    detail("A1", D(2027, 6, 30), D(2029, 6, 30), set_aside="8A COMPETED", offers=6, ceiling=300000.0),  # options left
    detail("A7", D(2026, 11, 30), D(2026, 11, 30), set_aside="SMALL BUSINESS SET ASIDE - TOTAL"),       # none left
    detail("A4", D(2028, 1, 31), D(2030, 1, 31)),
    detail("A9", D(2027, 6, 30), D(2027, 7, 15)),     # 15 days of slack: not a real option
    detail("A10", D(2027, 6, 30), D(2027, 6, 30)),
    detail("A11", D(2027, 6, 30), D(2027, 6, 30)),
]


def to_duckdb(sql: str) -> str:
    sql = sql.replace("`p.d.", "").replace("`", "")
    return sqlglot.transpile(sql, read="bigquery", write="duckdb")[0]


def build_db():
    import pandas as pd

    con = duckdb.connect()
    for name, rows in (("raw_awards", AWARDS), ("raw_transactions", TRANSACTIONS), ("raw_award_details", DETAILS)):
        df = pd.DataFrame(rows)  # noqa: F841  (DuckDB reads the local variable)
        # Give all-empty text columns a text type (BigQuery has them as STRING).
        for col in df.columns:
            if df[col].isna().all():
                df[col] = df[col].astype("string")
        con.execute(f"CREATE TABLE {name} AS SELECT * FROM df")
    for path in r.MODELS:
        select = to_duckdb(r.render(r.read_sql(path), scratch=False, project="p", dataset="d"))
        con.execute(f"CREATE TABLE {r.model_name(path)} AS {select}")
    return con


@pytest.fixture(scope="module")
def db():
    return build_db()


def rows(con, sql):
    cur = con.execute(sql)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def by_key(con, table, key="award_key"):
    return {row[key]: row for row in rows(con, f"SELECT * FROM {table}")}


def test_staging_uses_latest_snapshot_only(db):
    stg = by_key(db, "stg_awards")
    assert len(stg) == 15 - 1
    assert stg["A1"]["award_amount"] == 100000.0


def test_niche_filter_reasons(db):
    n = by_key(db, "int_niche_filter")
    assert n["X1"]["exclusion_reason"] == "software_tool"
    assert n["X2"]["exclusion_reason"] == "software_tool"
    assert n["X3"]["exclusion_reason"] == "unlabeled_call"
    assert n["X4"]["exclusion_reason"] == "psc"
    assert n["X5"]["exclusion_reason"] == "advertising"
    assert n["A10"]["in_niche"] and n["A10"]["exclusion_reason"] is None   # recruiting override
    assert n["A11"]["in_niche"]                                            # IGF:: is not an exclusion


def test_subtypes(db):
    n = by_key(db, "int_niche_filter")
    assert n["A1"]["subtype"] == "executive_search"
    assert n["A3"]["subtype"] == "recruiting"
    assert n["A9"]["subtype"] == "staffing"


def test_statuses_by_end_date_only(db):
    f = by_key(db, "fact_awards")
    assert set(f) == {"A1", "A2", "A3", "A4", "A7", "A8", "A9", "A10", "A11"}
    assert f["A1"]["status"] == "expiring_12m"
    assert f["A7"]["status"] == "expiring_6m" and f["A7"]["in_act_now_window"]
    assert f["A2"]["status"] == "expired"
    # A newer award at the same office does NOT hide A3: status follows end dates only.
    assert f["A3"]["status"] == "expired"
    assert f["A4"]["status"] == "active"
    assert f["A8"]["status"] == "expired"
    assert "successor_award_key" not in f["A3"]
    assert f["A9"]["is_masked_recipient"] and f["A9"]["is_zero_amount"]


def test_annualized_value(db):
    f = by_key(db, "fact_awards")
    # A7: 2025-01-01 to 2026-11-30 = 699 days -> 100000 / (699 / 365.25)
    assert f["A7"]["annualized_value"] == pytest.approx(100000 / (699 / 365.25))
    # Short awards count as one year.
    short = by_key(db, "int_recompete_flags")["A1"]
    assert short["duration_days"] > 365


def test_recompetes_list(db):
    rc = by_key(db, "recompetes")
    assert set(rc) == {"A1", "A7", "A9", "A10", "A11"}
    assert all(row["notice_out"] == "unknown" for row in rc.values())


def test_recompete_type_from_options(db):
    rc = by_key(db, "recompetes")
    assert rc["A1"]["recompete_type"] == "option_decision"      # can run to 2029
    assert rc["A1"]["ceiling_value"] == 300000.0 and rc["A1"]["number_of_offers_received"] == 6
    assert rc["A7"]["recompete_type"] == "likely_recompete"     # recruiting, no options left
    assert rc["A9"]["recompete_type"] == "likely_recompete"     # 15 days of slack is not an option
    assert rc["A10"]["recompete_type"] == "one_off_search"      # executive search, no options left
    assert rc["A7"]["set_aside"] == "SMALL BUSINESS SET ASIDE - TOTAL"
    assert rc["A10"]["set_aside"] == "NOT REPORTED"
    f = by_key(db, "fact_awards")
    assert f["A4"]["recompete_type"] is None                    # not in the window
    assert f["A4"]["has_options_remaining"]


def test_obligations(db):
    o = rows(db, "SELECT * FROM fct_obligations ORDER BY award_key, modification_number")
    fy = {(x["award_key"], x["modification_number"]): x["fiscal_year"] for x in o}
    assert fy[("A1", "0")] == 2025 and fy[("A1", "P00001")] == 2026
    niche = {x["award_key"]: x["in_niche"] for x in o}
    assert niche["A1"] and not niche["X1"]
    assert any(x["is_deobligation"] for x in o)


def test_vendors(db):
    v = by_key(db, "dim_vendors", key="recipient_uei")
    assert v["UEI00000000A"]["niche_awards"] == 1
    assert sum(x["share_24m"] or 0 for x in v.values()) == pytest.approx(1.0)
    assert v["UEI000000009"]["is_masked_recipient"]


def test_agencies(db):
    a = by_key(db, "dim_agencies", key="awarding_sub_agency")
    assert a["Smithsonian"]["agency_status"] == "lapsed"
    assert a["Army"]["agency_status"] in ("active", "lapsing")
    assert "VENDOR ONE" in a["Washington Headquarters Services"]["incumbents"]


# niche_size_in_range guards production volumes (100-600 awards); the fixture has 9.
@pytest.mark.parametrize("test_file", [f for f in r.test_files() if f.stem != "niche_size_in_range"],
                         ids=lambda p: p.stem)
def test_data_tests_pass(db, test_file):
    sql = to_duckdb(r.render(test_file.read_text(), scratch=False, project="p", dataset="d"))
    assert db.execute(sql).fetchall() == []


def test_reconcile_within_and_outside_tolerance():
    assert r.reconcile(AS_OF, {"DoD": 100.5}, {"DoD": 100.0, "State": 5.0}) == []
    assert r.reconcile(AS_OF, {"DoD": 90.0}, {"DoD": 100.0}) != []


def test_render_scratch_vs_published():
    sql = "SELECT * FROM {{ ref('fact_awards') }} JOIN {{ source('raw_awards') }}"
    assert "`p.d.wap__fact_awards`" in r.render(sql, scratch=True, project="p", dataset="d")
    assert "`p.d.fact_awards`" in r.render(sql, scratch=False, project="p", dataset="d")
    assert "`p.d.raw_awards`" in r.render(sql, scratch=True, project="p", dataset="d")


def run_script(con, path):
    sql = r.render(r.read_sql(path), scratch=False, project="p", dataset="d").replace("`p.d.", "").replace("`", "")
    for stmt in sqlglot.transpile(sql, read="bigquery", write="duckdb"):
        con.execute(stmt)


def test_snapshot_is_idempotent_and_events_are_derived():
    con = build_db()
    run_script(con, "snapshot/snapshot_daily")                 # day 1

    # Day 2: A1 extended, A7 amount changed, A2 gone, A12 new.
    con.execute("UPDATE fact_awards SET as_of_date = DATE '2026-09-29'")
    con.execute("UPDATE fact_awards SET end_date = DATE '2027-12-31', status = 'active' WHERE award_key = 'A1'")
    con.execute("UPDATE fact_awards SET award_amount = 150000 WHERE award_key = 'A7'")
    con.execute("DELETE FROM fact_awards WHERE award_key = 'A2'")
    con.execute("INSERT INTO fact_awards SELECT * REPLACE ('A12' AS award_key, 'A12' AS award_id) "
                "FROM fact_awards WHERE award_key = 'A4'")
    run_script(con, "snapshot/snapshot_daily")                 # day 2
    run_script(con, "snapshot/snapshot_daily")                 # day 2 again: must not duplicate

    counts = dict(con.execute("SELECT snapshot_date, COUNT(*) FROM snapshot_daily GROUP BY 1").fetchall())
    assert counts == {D(2026, 9, 28): 9, D(2026, 9, 29): 9}

    run_script(con, "views/vw_award_events")
    events = {(k, e) for k, e in con.execute("SELECT award_key, event FROM vw_award_events").fetchall()}
    assert events == {("A1", "extended"), ("A1", "status_changed"), ("A7", "amount_changed"),
                      ("A2", "dropped"), ("A12", "new")}


# BigQuery reserved keywords (GoogleSQL). DuckDB accepts some of these as names, so the local run
# can't catch them; BigQuery rejects them ("Unexpected '.'"). Checked on every SQL file.
BQ_RESERVED = set("""
ALL AND ANY ARRAY AS ASC ASSERT_ROWS_MODIFIED AT BETWEEN BY CASE CAST COLLATE CONTAINS CREATE CROSS
CUBE CURRENT DEFAULT DEFINE DESC DISTINCT ELSE END ENUM ESCAPE EXCEPT EXCLUDE EXISTS EXTRACT FALSE
FETCH FOLLOWING FOR FROM FULL GROUP GROUPING GROUPS HASH HAVING IF IGNORE IN INNER INTERSECT INTERVAL
INTO IS JOIN LATERAL LEFT LIKE LIMIT LOOKUP MERGE NATURAL NEW NO NOT NULL NULLS OF ON OR ORDER OUTER
OVER PARTITION PRECEDING PROTO QUALIFY RANGE RECURSIVE RESPECT RIGHT ROLLUP ROWS SELECT SET SOME
STRUCT TABLESAMPLE THEN TO TREAT TRUE UNBOUNDED UNION UNNEST USING WHEN WHERE WINDOW WITH WITHIN
""".split())

ALL_SQL = sorted(r.SQL_DIR.rglob("*.sql"))


@pytest.mark.parametrize("path", ALL_SQL, ids=lambda p: str(p.relative_to(r.SQL_DIR)))
def test_no_reserved_words_as_names(path):
    import re

    code = re.sub(r"--[^\n]*", "", path.read_text())            # drop comments
    code = re.sub(r"'(?:[^'\\]|\\.)*'", "''", code)             # drop string literals
    aliases = re.findall(r"\bAS\s+([A-Za-z_]\w*)\b(?!\s*\()", code, flags=re.I)
    qualifiers = re.findall(r"\b([A-Za-z_]\w*)\.(?=[A-Za-z_*])", code)
    # CAST(x AS <type>) is not an alias.
    # CREATE ... AS SELECT / AS WITH starts a query body, not an alias.
    types = {"STRING", "DATE", "INT64", "FLOAT64", "NUMERIC", "BOOL", "TIMESTAMP", "DATETIME", "SELECT", "WITH"}
    bad = {w for w in aliases + qualifiers if w.upper() in BQ_RESERVED and w.upper() not in types}
    assert not bad, f"reserved words used as names: {sorted(bad)}"
