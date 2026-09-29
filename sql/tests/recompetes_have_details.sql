-- Every award on the recompete list has its detail page (options, set-aside). The ingest fetches
-- details for every still-running award, so a gap means the join or the pull broke.
SELECT award_key
FROM {{ ref('recompetes') }}
WHERE potential_end_date IS NULL OR recompete_type IS NULL
