-- int_niche_filter: every award from stg_awards, labelled in or out of the niche, with the reason.
-- Rows are never dropped here; downstream models filter on in_niche. That keeps every
-- exclusion visible and countable (see docs/cleaning_rules.md).
--
-- Niche rule (docs: Definitions, section 2). All three must hold:
--   1. NAICS in {561311, 561312}
--   2. PSC in {R431, R499, R408, R699, R497}
--   3. Description not excluded by the v1 keyword rules below.
-- Order matters: the first matching exclusion is the one reported.
-- "Override" rules are skipped when the description clearly describes recruiting work
-- (e.g. "EXECUTIVE SEARCH FIRM ... SYSTEM" stays in).

WITH base AS (
  SELECT
    a.*,
    UPPER(a.description) AS d
  FROM {{ ref('stg_awards') }} AS a
),

labelled AS (
  SELECT
    base.*,
    COALESCE(naics_code IN ('561311', '561312'), FALSE)                    AS naics_ok,
    COALESCE(psc_code IN ('R431', 'R499', 'R408', 'R699', 'R497'), FALSE)   AS psc_ok,
    REGEXP_CONTAINS(d, r'RECRUIT|SEARCH|HEAD ?HUNT|STAFFING|PLACEMENT|HIRING') AS has_recruiting_terms,

    CASE
      -- Tools a search firm can't bid on (job boards, recruiting/HR software).
      WHEN REGEXP_CONTAINS(d, r'LINKEDIN|HIREEZ|POLIHIRE|\bAVUE\b|SALESFORCE|SOFTWARE|LICEN[CS]E|SUBSCRIPTION|HOLOLENS')
        THEN 'software_tool'
      -- IT systems support coded as professional services (override applies).
      WHEN REGEXP_CONTAINS(d, r'\bIT SUPPORT|SYSTEMS? SUPPORT|\bATRRS\b|\bEFLOW\b|\bERADS\b|ELECTRONIC CASE|\bORACLE\b|\bPRISM\b')
       AND NOT REGEXP_CONTAINS(d, r'RECRUIT|SEARCH|HEAD ?HUNT|STAFFING|PLACEMENT|HIRING')
        THEN 'it_system'
      -- Audio-visual and equipment work (override applies).
      WHEN REGEXP_CONTAINS(d, r'\bAV\b|AUDIO.?VISUAL|LIGHTING|EQUIPMENT')
       AND NOT REGEXP_CONTAINS(d, r'RECRUIT|SEARCH|HEAD ?HUNT|STAFFING|PLACEMENT|HIRING')
        THEN 'av_equipment'
      -- Advertising-only campaigns (override applies).
      WHEN REGEXP_CONTAINS(d, r'ADVERTIS')
       AND NOT REGEXP_CONTAINS(d, r'RECRUIT|SEARCH|HEAD ?HUNT|STAFFING|PLACEMENT|HIRING')
        THEN 'advertising'
      -- Bare order numbers with no description ("BPA CALL 0008"): can't tell what was bought.
      WHEN REGEXP_CONTAINS(d, r'^\s*(BPA\s+)?CALL\s*#?\s*\d+\s*$')
        THEN 'unlabeled_call'
    END AS description_exclusion,

    -- Subtype (Definitions, section 3). First match wins; default is staffing
    -- (individual positions filled through a vendor, incl. personal services contracts).
    CASE
      WHEN REGEXP_CONTAINS(d, r'EXECUTIVE SEARCH|SEARCH FOR|\(SES\)|PRIVATE SECTOR LEADERSHIP|HEAD ?HUNT|SELECTION BOARD')
        THEN 'executive_search'
      WHEN REGEXP_CONTAINS(d, r'WARRIOR|YELLOW RIBBON|VETERAN')
        THEN 'veteran_employment'
      WHEN REGEXP_CONTAINS(d, r'RECRUIT|OUTREACH|HIRING|JOB FAIR|JOB POSTING|TALENT ACQUISITION')
        THEN 'recruiting'
      WHEN REGEXP_CONTAINS(d, r'HUMAN RESOURCE|\bHR\b|HUMAN CAPITAL|\bEEO\b|CLASSIFICATION')
        THEN 'hr_support'
      ELSE 'staffing'
    END AS subtype
  FROM base
)

SELECT
  * EXCEPT (d),
  CASE
    WHEN NOT naics_ok THEN 'naics'
    WHEN NOT psc_ok THEN 'psc'
    ELSE description_exclusion
  END AS exclusion_reason,
  naics_ok AND psc_ok AND description_exclusion IS NULL AS in_niche
FROM labelled
