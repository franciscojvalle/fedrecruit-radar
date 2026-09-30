-- fct_obligations: one row per contract action (modification), with the award's niche label.
--
-- Why this table exists: award_amount is cumulative over a contract's life, so summing it by
-- year puts multi-year money in the wrong fiscal year. Each transaction has its own
-- action_date and obligation_amount, so SUM(obligation_amount) by fiscal_year gives the money
-- actually committed in each year.
--
-- Grain: award_key + modification_number (unique; enforced by ingest validation and a test).
-- Negative obligation_amount = de-obligation. Kept: dropping them would overstate spending.
-- Coverage: actions inside the lookback window only (FY2022 onward). Awards that started
-- earlier have older actions outside the window, so their sum is lower than award_amount.
-- Filter on in_niche for niche totals; all NAICS-matched actions are kept for reconciliation.

SELECT
  t.award_key,
  t.award_id,
  t.modification_number,
  t.is_base_award,
  t.action_date,
  t.fiscal_year,
  t.action_type,
  t.obligation_amount,
  t.is_deobligation,
  t.awarding_agency,
  t.awarding_sub_agency,
  t.recipient_name,
  t.recipient_uei,
  t.contract_award_type,
  t.naics_code,
  t.psc_code,
  t.transaction_description,
  COALESCE(n.in_niche, FALSE) AS in_niche,
  t.as_of_date
FROM {{ ref('stg_transactions') }} AS t
LEFT JOIN {{ ref('int_niche_filter') }} AS n USING (award_key)
