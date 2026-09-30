-- recompetes: niche awards whose current period ends in the next 12 months.
-- One row per award; the dashboard's Recompetes page and the weekly brief read this.
-- has_options_remaining: the agency can extend to potential_end_date without a new competition.
-- Facts only; the reader judges what's a real opportunity.
-- set_aside tells a small firm whether it can bid at all ("NOT REPORTED" is common on orders
-- under a larger contract, where the set-aside sits on the parent).
-- notice_out stays 'unknown' until SAM.gov notices are ingested (Definitions, section 10).

SELECT
  award_key,
  award_id,
  awarding_agency,
  awarding_sub_agency,
  recipient_name      AS incumbent_name,
  recipient_uei       AS incumbent_uei,
  description,
  has_options_remaining,
  set_aside,
  extent_competed,
  number_of_offers_received,
  award_amount,
  ceiling_value,
  annualized_value,
  start_date,
  end_date,
  potential_end_date,
  days_to_expiry,
  in_act_now_window,
  status,
  'unknown'           AS notice_out,
  usaspending_url,
  as_of_date
FROM {{ ref('fact_awards') }}
WHERE in_recompete_window
