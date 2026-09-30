-- vw_dash_market: Market page. One row per contract action (modification) on a niche award.
-- SUM(obligated_amount) by fiscal_year = money committed per year (negatives are de-obligations).
-- Charts: obligations by FY, YoY change, department x FY pivot (drill to sub-agency),
-- top sub-agencies in the last 24 months (in_last_24m), agency status (agency_status).
-- is_current_fy marks the fiscal year still in progress, so a partial year isn't read as a drop.

CREATE OR REPLACE VIEW {{ table('vw_dash_market') }} AS
SELECT
  o.award_id,
  o.modification_number,
  o.action_date,
  o.fiscal_year,
  o.fiscal_year = EXTRACT(YEAR FROM DATE_ADD(o.as_of_date, INTERVAL 3 MONTH))   AS is_current_fy,
  o.action_date > DATE_SUB(o.as_of_date, INTERVAL 24 MONTH)                     AS in_last_24m,
  o.obligation_amount                                                           AS obligated_amount,
  o.is_deobligation,
  o.awarding_agency                                                             AS awarding_department,
  o.awarding_sub_agency,
  a.agency_status,
  o.recipient_uei,
  o.recipient_name,
  o.as_of_date
FROM {{ table('fct_obligations') }} AS o
LEFT JOIN {{ table('dim_agencies') }} AS a USING (awarding_sub_agency)
WHERE o.in_niche
