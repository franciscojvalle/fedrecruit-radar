-- fact_awards: one row per niche award (current state), with expiry status and value measures.
-- award_amount = obligated to date ("approved so far"), not the ceiling. Awards still running
-- can grow as options are exercised.

SELECT
  award_key,
  award_id,
  recipient_uei,
  recipient_name,
  is_masked_recipient,
  awarding_agency,
  awarding_sub_agency,
  description,
  naics_code,
  psc_code,
  contract_award_type,
  award_date,
  start_date,
  end_date,
  potential_end_date,
  has_options_remaining,
  EXTRACT(YEAR FROM DATE_ADD(award_date, INTERVAL 3 MONTH))            AS award_fiscal_year,
  duration_days,
  award_amount,
  base_and_all_options_value                                          AS ceiling_value,
  is_zero_amount,
  -- Per-year value, so a 3-month order and a 5-year contract compare fairly.
  -- Awards shorter than a year count as one year.
  award_amount / GREATEST(duration_days / 365.25, 1)                  AS annualized_value,
  days_to_expiry,
  status,
  set_aside,
  extent_competed,
  number_of_offers_received,
  solicitation_identifier,
  status IN ('expiring_6m', 'expiring_12m')                           AS in_recompete_window,
  status = 'expiring_6m'                                              AS in_act_now_window,
  end_date >= as_of_date                                              AS is_active,
  CONCAT('https://www.usaspending.gov/award/', award_key)             AS usaspending_url,
  last_modified_date,
  as_of_date
FROM {{ ref('int_recompete_flags') }}
