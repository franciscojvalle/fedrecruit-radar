-- stg_transactions: one row per contract action (modification) from the latest raw snapshot.
-- obligation_amount can be negative (de-obligation: money taken back). Kept on purpose.

WITH latest AS (
  SELECT MAX(snapshot_date) AS as_of_date
  FROM {{ source('raw_transactions') }}
)

SELECT
  t.award_key,
  t.award_id,
  t.modification_number,
  t.modification_number = '0'                                    AS is_base_award,
  t.action_date,
  -- Federal fiscal year starts 1 Oct: FY = calendar year of (date + 3 months).
  EXTRACT(YEAR FROM DATE_ADD(t.action_date, INTERVAL 3 MONTH))   AS fiscal_year,
  t.action_type,
  t.obligation_amount,
  t.obligation_amount < 0                                        AS is_deobligation,
  TRIM(t.awarding_agency)                                        AS awarding_agency,
  TRIM(t.awarding_sub_agency)                                    AS awarding_sub_agency,
  TRIM(t.recipient_name)                                         AS recipient_name,
  NULLIF(TRIM(t.recipient_uei), '')                              AS recipient_uei,
  t.contract_award_type,
  t.naics_code,
  t.psc_code,
  TRIM(COALESCE(t.transaction_description, ''))                  AS transaction_description,
  latest.as_of_date
FROM {{ source('raw_transactions') }} AS t
JOIN latest ON t.snapshot_date = latest.as_of_date
