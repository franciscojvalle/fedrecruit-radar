-- stg_award_details: one row per still-running award from the latest snapshot's detail pages.
-- Only awards with an end date on or after the snapshot date are fetched (~60 a day).

WITH latest AS (
  SELECT MAX(snapshot_date) AS as_of_date
  FROM {{ source('raw_award_details') }}
)

SELECT
  d.award_key,
  d.current_end_date,
  d.potential_end_date,
  d.obligated_amount,
  d.base_exercised_options_value,
  d.base_and_all_options_value,
  NULLIF(TRIM(d.type_set_aside), '')                                     AS type_set_aside,
  COALESCE(NULLIF(TRIM(d.type_set_aside_description), ''), 'NOT REPORTED') AS set_aside,
  NULLIF(TRIM(d.extent_competed_description), '')                        AS extent_competed,
  d.number_of_offers_received,
  NULLIF(TRIM(d.solicitation_identifier), '')                            AS solicitation_identifier
FROM {{ source('raw_award_details') }} AS d
JOIN latest ON d.snapshot_date = latest.as_of_date
WHERE TRUE
QUALIFY ROW_NUMBER() OVER (PARTITION BY d.award_key ORDER BY d.potential_end_date DESC) = 1
