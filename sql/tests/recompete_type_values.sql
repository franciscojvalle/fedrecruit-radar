-- recompete_type is set exactly for awards in the 12-month window, with a documented value.
SELECT award_key, status, recompete_type
FROM {{ ref('fact_awards') }}
WHERE (in_recompete_window AND recompete_type NOT IN ('likely_recompete', 'option_decision', 'one_off_search'))
   OR (NOT in_recompete_window AND recompete_type IS NOT NULL)
