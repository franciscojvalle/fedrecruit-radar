-- stg_awards: one row per award from the latest raw snapshot.
-- Cleans text and adds data-quality flags. No business rules (no niche filter) here.
-- as_of_date = the snapshot date; every "today" downstream uses it, so a rebuild of an
-- old snapshot gives the same answer it gave that day.

WITH latest AS (
  SELECT MAX(snapshot_date) AS as_of_date
  FROM {{ source('raw_awards') }}
)

SELECT
  r.award_key,
  r.award_id,
  TRIM(r.recipient_name)                          AS recipient_name,
  NULLIF(TRIM(r.recipient_uei), '')               AS recipient_uei,
  TRIM(r.awarding_agency)                         AS awarding_agency,
  TRIM(r.awarding_sub_agency)                     AS awarding_sub_agency,
  r.award_amount,
  r.start_date,
  r.end_date,
  r.naics_code,
  r.naics_description,
  r.psc_code,
  r.psc_description,
  TRIM(COALESCE(r.description, ''))               AS description,
  r.contract_award_type,
  r.last_modified_date,
  COALESCE(r.base_obligation_date, r.start_date)  AS award_date,

  -- Data-quality flags: kept as columns, never used to drop rows.
  r.award_amount = 0                                                        AS is_zero_amount,
  REGEXP_CONTAINS(UPPER(r.recipient_name), r'UNDISCLOSED|REDACTED|MULTIPLE RECIPIENTS') AS is_masked_recipient,
  DATE_DIFF(r.end_date, r.start_date, DAY) + 1                              AS duration_days,

  latest.as_of_date
FROM {{ source('raw_awards') }} AS r
JOIN latest ON r.snapshot_date = latest.as_of_date
WHERE TRUE
-- Ingest already guarantees one row per award; this keeps the model safe if that ever breaks.
QUALIFY ROW_NUMBER() OVER (PARTITION BY r.award_key ORDER BY r.last_modified_date DESC) = 1
