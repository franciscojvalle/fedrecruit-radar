-- fct_obligations has one row per (award, modification).
SELECT award_key, modification_number, COUNT(*) AS n
FROM {{ ref('fct_obligations') }}
GROUP BY award_key, modification_number
HAVING COUNT(*) > 1
