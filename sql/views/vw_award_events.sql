-- vw_award_events: day-over-day changes derived from snapshot_daily (Definitions, section 9).
-- One row per award per change, comparing each snapshot with the one before it:
--   new             award appears (never reported on the first snapshot day: nothing to compare)
--   dropped         award was in the previous snapshot and is gone (left the niche or the window)
--   extended        end date moved later       shortened: end date moved earlier
--   amount_changed  obligated amount changed (includes de-obligations)
--   status_changed  e.g. expiring_12m -> expiring_6m, or -> recompeted
-- A view: always current with snapshot_daily, nothing to rebuild.

CREATE OR REPLACE VIEW {{ table('vw_award_events') }} AS
WITH days AS (
  SELECT snapshot_date, LAG(snapshot_date) OVER (ORDER BY snapshot_date) AS prev_snapshot_date
  FROM (SELECT DISTINCT snapshot_date FROM {{ table('snapshot_daily') }})
),

cur AS (
  SELECT s.*
  FROM {{ table('snapshot_daily') }} AS s
  JOIN days AS d USING (snapshot_date)
  WHERE d.prev_snapshot_date IS NOT NULL
),

prev AS (
  -- Each row of a snapshot, labelled with the snapshot that follows it.
  SELECT s.*, d.snapshot_date AS next_snapshot_date
  FROM {{ table('snapshot_daily') }} AS s
  JOIN days AS d ON s.snapshot_date = d.prev_snapshot_date
),

pairs AS (
  SELECT
    COALESCE(c.snapshot_date, p.next_snapshot_date)             AS snapshot_date,
    COALESCE(c.award_key, p.award_key)                          AS award_key,
    COALESCE(c.award_id, p.award_id)                            AS award_id,
    COALESCE(c.awarding_sub_agency, p.awarding_sub_agency)      AS awarding_sub_agency,
    COALESCE(c.recipient_name, p.recipient_name)                AS recipient_name,
    c.award_key IS NOT NULL                                     AS in_cur,
    p.award_key IS NOT NULL                                     AS in_prev,
    p.end_date      AS prev_end_date,  c.end_date      AS end_date,
    p.award_amount  AS prev_amount,    c.award_amount  AS award_amount,
    p.status        AS prev_status,    c.status        AS status
  FROM cur AS c
  FULL JOIN prev AS p
    ON p.next_snapshot_date = c.snapshot_date AND p.award_key = c.award_key
)

SELECT snapshot_date, award_key, award_id, awarding_sub_agency, recipient_name,
       'new' AS event, CAST(NULL AS STRING) AS old_value, status AS new_value
FROM pairs WHERE in_cur AND NOT in_prev
UNION ALL
SELECT snapshot_date, award_key, award_id, awarding_sub_agency, recipient_name,
       'dropped', prev_status, CAST(NULL AS STRING)
FROM pairs WHERE in_prev AND NOT in_cur
UNION ALL
SELECT snapshot_date, award_key, award_id, awarding_sub_agency, recipient_name,
       IF(end_date > prev_end_date, 'extended', 'shortened'),
       CAST(prev_end_date AS STRING), CAST(end_date AS STRING)
FROM pairs WHERE in_cur AND in_prev AND end_date != prev_end_date
UNION ALL
SELECT snapshot_date, award_key, award_id, awarding_sub_agency, recipient_name,
       'amount_changed', CAST(prev_amount AS STRING), CAST(award_amount AS STRING)
FROM pairs WHERE in_cur AND in_prev AND award_amount != prev_amount
UNION ALL
SELECT snapshot_date, award_key, award_id, awarding_sub_agency, recipient_name,
       'status_changed', prev_status, status
FROM pairs WHERE in_cur AND in_prev AND status != prev_status
