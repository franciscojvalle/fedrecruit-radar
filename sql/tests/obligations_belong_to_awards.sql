-- Every transaction belongs to an award in the same snapshot.
SELECT t.award_key
FROM {{ ref('fct_obligations') }} AS t
LEFT JOIN {{ ref('stg_awards') }} AS a USING (award_key)
WHERE a.award_key IS NULL
