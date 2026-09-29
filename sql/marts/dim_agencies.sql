-- dim_agencies: one row per awarding sub-agency that has bought in the niche.
-- Awarding sub-agency is the unit of analysis; awarding_agency (department) is the roll-up.
--
-- Status (Definitions, section 11), first match wins, dated by award_date:
--   new       first niche award in the last 12 months (none before it in the 5-year window)
--   active    last niche award in the last 12 months
--   lapsing   last niche award 12-24 months ago
--   lapsed    last niche award more than 24 months ago (outreach list for a new entrant)
-- Note: the definitions doc lists "active" as "last 24 months", which overlaps "lapsing";
-- here active = last 12 months so the four labels don't overlap.

WITH awards AS (
  SELECT * FROM {{ ref('fact_awards') }}
),

obligations_24m AS (
  SELECT
    awarding_sub_agency,
    SUM(obligation_amount) AS obligations_24m
  FROM {{ ref('fct_obligations') }}
  WHERE in_niche
    AND action_date > DATE_SUB(as_of_date, INTERVAL 24 MONTH)
  GROUP BY awarding_sub_agency
),

agg AS (
  SELECT
    awarding_sub_agency,
    ANY_VALUE(awarding_agency)                                       AS awarding_agency,
    COUNT(*)                                                         AS niche_awards,
    COUNTIF(is_active)                                               AS active_awards,
    COUNTIF(in_recompete_window)                                     AS awards_in_recompete_window,
    SUM(award_amount)                                                AS niche_award_amount,
    SUM(IF(is_active, award_amount, 0))                              AS active_award_amount,
    MIN(award_date)                                                  AS first_award_date,
    MAX(award_date)                                                  AS last_award_date,
    COUNT(DISTINCT recipient_uei)                                    AS vendors,
    STRING_AGG(DISTINCT IF(is_active, recipient_name, NULL), '; ' ORDER BY IF(is_active, recipient_name, NULL)) AS incumbents,
    ANY_VALUE(as_of_date)                                            AS as_of_date
  FROM awards
  GROUP BY awarding_sub_agency
)

SELECT
  agg.*,
  COALESCE(o.obligations_24m, 0) AS obligations_24m,
  CASE
    WHEN agg.first_award_date > DATE_SUB(agg.as_of_date, INTERVAL 12 MONTH) THEN 'new'
    WHEN agg.last_award_date  > DATE_SUB(agg.as_of_date, INTERVAL 12 MONTH) THEN 'active'
    WHEN agg.last_award_date  > DATE_SUB(agg.as_of_date, INTERVAL 24 MONTH) THEN 'lapsing'
    ELSE 'lapsed'
  END AS agency_status
FROM agg
LEFT JOIN obligations_24m AS o USING (awarding_sub_agency)
