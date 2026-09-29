-- int_recompete_flags: niche awards with expiry and recompete status (Definitions, section 4).
--
-- Successor: a newer niche award at the same awarding sub-agency, same subtype, whose
-- award date falls within 90 days either side of this award's end date. If one exists,
-- this award is 'recompeted' (with the new recipient), not expiring.
-- Status, first match wins:
--   recompeted            successor found
--   expired_no_successor  ended more than 90 days ago, no successor
--   expired_pending       ended in the last 90 days, a successor may still appear
--   expiring_6m           ends within 6 months   (act-now window)
--   expiring_12m          ends within 12 months  (recompete window)
--   active                ends later than that
-- Known limit (open item C): DFC places many small staffing orders at one sub-agency, so
-- an unrelated order can look like a successor. To be validated against 10 known recompetes.
-- Set-aside type is not in the award search API; left for a later enrichment step.

WITH niche AS (
  SELECT * FROM {{ ref('int_niche_filter') }} WHERE in_niche
),

successors AS (
  SELECT
    prv.award_key,
    nxt.award_key      AS successor_award_key,
    nxt.award_id       AS successor_award_id,
    nxt.recipient_uei  AS successor_recipient_uei,
    nxt.recipient_name AS successor_recipient_name,
    nxt.award_date     AS successor_award_date
  FROM niche AS prv   -- the award that may be expiring
  JOIN niche AS nxt   -- a later award that may replace it
    ON  nxt.awarding_sub_agency = prv.awarding_sub_agency
    AND nxt.subtype = prv.subtype
    AND nxt.award_key != prv.award_key
    AND nxt.award_date > prv.award_date
    AND nxt.award_date BETWEEN DATE_SUB(prv.end_date, INTERVAL 90 DAY)
                           AND DATE_ADD(prv.end_date, INTERVAL 90 DAY)
  WHERE TRUE
  -- The earliest qualifying award is the successor.
  QUALIFY ROW_NUMBER() OVER (PARTITION BY prv.award_key ORDER BY nxt.award_date, nxt.award_key) = 1
)

SELECT
  n.*,
  DATE_DIFF(n.end_date, n.as_of_date, DAY) AS days_to_expiry,
  s.successor_award_key,
  s.successor_award_id,
  s.successor_recipient_uei,
  s.successor_recipient_name,
  s.successor_award_date,
  s.successor_award_key IS NOT NULL
    AND COALESCE(s.successor_recipient_uei != n.recipient_uei, FALSE) AS incumbent_changed,
  CASE
    WHEN s.successor_award_key IS NOT NULL                              THEN 'recompeted'
    WHEN n.end_date < DATE_SUB(n.as_of_date, INTERVAL 90 DAY)           THEN 'expired_no_successor'
    WHEN n.end_date < n.as_of_date                                      THEN 'expired_pending'
    WHEN n.end_date <= DATE_ADD(n.as_of_date, INTERVAL 6 MONTH)         THEN 'expiring_6m'
    WHEN n.end_date <= DATE_ADD(n.as_of_date, INTERVAL 12 MONTH)        THEN 'expiring_12m'
    ELSE 'active'
  END AS status
FROM niche AS n
LEFT JOIN successors AS s USING (award_key)
