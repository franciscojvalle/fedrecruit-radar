-- recompetes: niche awards ending in the next 12 months with no successor yet.
-- One row per award; the dashboard's Recompetes page and the weekly brief read this.
-- notice_out stays 'unknown' until SAM.gov notices are ingested (Definitions, section 10).

SELECT
  award_key,
  award_id,
  awarding_agency,
  awarding_sub_agency,
  recipient_name      AS incumbent_name,
  recipient_uei       AS incumbent_uei,
  subtype,
  description,
  award_amount,
  annualized_value,
  start_date,
  end_date,
  days_to_expiry,
  in_act_now_window,
  status,
  'unknown'           AS notice_out,
  usaspending_url,
  as_of_date
FROM {{ ref('fact_awards') }}
WHERE in_recompete_window
