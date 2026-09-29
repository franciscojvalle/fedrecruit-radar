-- Guard against a broken filter: the niche has ~190 awards (Sep 2026). Far outside 100-600
-- means a rule or the source changed and a human should look before publishing.
SELECT COUNT(*) AS niche_awards
FROM {{ ref('fact_awards') }}
HAVING COUNT(*) NOT BETWEEN 100 AND 600
