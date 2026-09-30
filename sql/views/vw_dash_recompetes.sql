-- vw_dash_recompetes: Recompetes page. One row per niche award whose current period ends in the
-- next 12 months. Facts only (v1): the reader judges what's a real opportunity.
-- Charts: KPIs (count and $ in the 12m and 6m windows, set-aside share), table sorted by end_date,
-- bar of $ expiring per month (expiry_month). Filters: time_window, set_aside, has_options_remaining.

CREATE OR REPLACE VIEW {{ table('vw_dash_recompetes') }} AS
SELECT
  award_id,
  usaspending_url,
  awarding_agency                                            AS awarding_department,
  awarding_sub_agency,
  incumbent_name,
  incumbent_uei,
  description,
  award_amount,
  ceiling_value,
  start_date,
  end_date,
  DATE_TRUNC(end_date, MONTH)                                AS expiry_month,
  days_to_expiry,
  IF(in_act_now_window, 'Next 6 months', '6-12 months')     AS time_window,
  in_act_now_window                                          AS in_6m_window,
  has_options_remaining,
  potential_end_date,
  set_aside,
  set_aside != 'NOT REPORTED'                                AS set_aside_reported,
  extent_competed,
  number_of_offers_received,
  notice_out,
  CAST(NULL AS STRING)                                       AS notice_id,   -- filled once SAM.gov is ingested
  as_of_date
FROM {{ table('recompetes') }}
