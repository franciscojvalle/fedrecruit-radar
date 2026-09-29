-- Every niche award names its agency, sub-agency and vendor (name and UEI).
SELECT award_key
FROM {{ ref('fact_awards') }}
WHERE awarding_agency IS NULL OR awarding_sub_agency IS NULL
   OR recipient_name IS NULL OR recipient_uei IS NULL
