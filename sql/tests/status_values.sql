-- Status is one of the documented values.
SELECT award_key, status
FROM {{ ref('fact_awards') }}
WHERE status NOT IN ('recompeted', 'expired_no_successor', 'expired_pending',
                     'expiring_6m', 'expiring_12m', 'active')
