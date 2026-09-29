-- dim_vendors: one row per vendor (recipient UEI) with niche awards.
-- Identity by UEI, not name (Definitions, section 7); the most recent award's name is shown.
-- share_24m = vendor's share of niche obligations in the last 24 months (concentration).
-- Lost recompete: vendor held an award whose successor went to a different UEI.
-- Masked recipients ("FOREIGN AWARDEES (UNDISCLOSED)") share a placeholder UEI; flagged.

WITH awards AS (
  SELECT * FROM {{ ref('fact_awards') }} WHERE recipient_uei IS NOT NULL
),

names AS (
  SELECT recipient_uei, recipient_name
  FROM awards
  WHERE TRUE
  QUALIFY ROW_NUMBER() OVER (PARTITION BY recipient_uei ORDER BY award_date DESC, award_key) = 1
),

obligations_24m AS (
  SELECT
    recipient_uei,
    SUM(obligation_amount) AS obligations_24m
  FROM {{ ref('fct_obligations') }}
  WHERE in_niche
    AND recipient_uei IS NOT NULL
    AND action_date > DATE_SUB(as_of_date, INTERVAL 24 MONTH)
  GROUP BY recipient_uei
),

wins AS (
  SELECT successor_recipient_uei AS recipient_uei, COUNT(*) AS recompetes_won
  FROM awards
  WHERE incumbent_changed
  GROUP BY successor_recipient_uei
),

agg AS (
  SELECT
    recipient_uei,
    LOGICAL_OR(is_masked_recipient)                  AS is_masked_recipient,
    COUNT(*)                                         AS niche_awards,
    COUNTIF(is_active)                               AS active_awards,
    COUNTIF(in_recompete_window)                     AS awards_in_recompete_window,
    SUM(award_amount)                                AS niche_award_amount,
    COUNT(DISTINCT awarding_sub_agency)              AS sub_agencies_served,
    COUNTIF(incumbent_changed)                       AS recompetes_lost,
    MIN(award_date)                                  AS first_award_date,
    MAX(award_date)                                  AS last_award_date,
    ANY_VALUE(as_of_date)                            AS as_of_date
  FROM awards
  GROUP BY recipient_uei
)

SELECT
  agg.recipient_uei,
  names.recipient_name,
  agg.* EXCEPT (recipient_uei),
  COALESCE(w.recompetes_won, 0)                                        AS recompetes_won,
  COALESCE(o.obligations_24m, 0)                                       AS obligations_24m,
  SAFE_DIVIDE(COALESCE(o.obligations_24m, 0),
              SUM(COALESCE(o.obligations_24m, 0)) OVER ())             AS share_24m
FROM agg
JOIN names USING (recipient_uei)
LEFT JOIN obligations_24m AS o USING (recipient_uei)
LEFT JOIN wins AS w USING (recipient_uei)
