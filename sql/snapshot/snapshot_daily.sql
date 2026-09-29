-- snapshot_daily: one row per niche award per day, the state USAspending doesn't keep.
-- USAspending only shows each award's current version; this table records what it looked like
-- each day, so changes (new, extended, amount changed, recompeted) can be derived later.
--
-- Runs after publish. Replaces only the as_of_date partition, so a re-run on the same day
-- doesn't duplicate rows; earlier days are never touched.
-- reconstructed = FALSE for observed rows. Rows rebuilt for dates before go-live
-- (from start/end dates) will be flagged TRUE by a separate backfill step.

CREATE TABLE IF NOT EXISTS {{ table('snapshot_daily') }} (
  snapshot_date       DATE NOT NULL,
  award_key           STRING NOT NULL,
  award_id            STRING,
  awarding_agency     STRING,
  awarding_sub_agency STRING,
  recipient_uei       STRING,
  recipient_name      STRING,
  subtype             STRING,
  award_amount        FLOAT64,
  end_date            DATE,
  status              STRING,
  successor_award_key STRING,
  reconstructed       BOOL
)
PARTITION BY snapshot_date
OPTIONS (description = "One row per niche award per day. Replaced per day on re-run; history kept.");

DELETE FROM {{ table('snapshot_daily') }}
WHERE snapshot_date = (SELECT MAX(as_of_date) FROM {{ table('fact_awards') }});

INSERT INTO {{ table('snapshot_daily') }}
SELECT
  as_of_date AS snapshot_date,
  award_key,
  award_id,
  awarding_agency,
  awarding_sub_agency,
  recipient_uei,
  recipient_name,
  subtype,
  award_amount,
  end_date,
  status,
  CAST(NULL AS STRING) AS successor_award_key,   -- reserved for v2 successor matching
  FALSE AS reconstructed
FROM {{ table('fact_awards') }};
