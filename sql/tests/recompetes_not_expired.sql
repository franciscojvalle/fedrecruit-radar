-- No award on the recompete list has already ended.
SELECT award_key, end_date, as_of_date
FROM {{ ref('recompetes') }}
WHERE end_date < as_of_date
