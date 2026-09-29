-- fact_awards has exactly one row per award.
SELECT award_key, COUNT(*) AS n
FROM {{ ref('fact_awards') }}
GROUP BY award_key
HAVING COUNT(*) > 1
