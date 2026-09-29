-- Status is one of the documented values.
SELECT award_key, status
FROM {{ ref('fact_awards') }}
WHERE status NOT IN ('expired', 'expiring_6m', 'expiring_12m', 'active')
