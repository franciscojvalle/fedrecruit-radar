-- int_recompete_flags: niche awards with expiry status, by end date only (Definitions, section 4).
--
-- Status, first match wins:
--   expired        end date has passed
--   expiring_6m    ends within 6 months   (act-now window)
--   expiring_12m   ends within 12 months  (recompete window)
--   active         ends later than that
--
-- Decision 2026-09-29 (Francisco): no successor matching in v1. The earlier rule (same office +
-- same subtype + awarded within 90 days of the end date) was wrong in 6 of 10 hand-checked pairs
-- and hid live leads (e.g. a DFC analyst contract ending Dec 2026). A missed lead costs a user
-- more than an extra one to skip, so the list follows end dates. Won/lost analysis is v2, with
-- description matching and review.
--
-- Recompete type (only for awards in the 12-month window), first match wins:
--   option_decision   option years remain (final possible end date > 30 days after the current
--                     end date, from the award detail page): the agency will most likely extend.
--                     Worth watching, not bidding yet.
--   one_off_search    no options left and it's an executive search: the job ends when the role is
--                     filled, nothing gets re-bid. Shows which agencies use search firms.
--   likely_recompete  no options left and the need is ongoing (staffing, recruiting, HR, veteran
--                     programs): the agency has to buy again (new competition, bridge or direct
--                     award). This is the lead list.
-- Checked 2026-09-29 against 10 detail pages: 7 of 10 listed awards still had options; 2 of the
-- other 3 were one-off searches (PBGC CFO, Navy provost).

WITH flagged AS (
  SELECT
    n.*,
    d.potential_end_date,
    d.base_and_all_options_value,
    d.set_aside,
    d.extent_competed,
    d.number_of_offers_received,
    d.solicitation_identifier,
    COALESCE(d.potential_end_date > DATE_ADD(n.end_date, INTERVAL 30 DAY), FALSE) AS has_options_remaining,
    DATE_DIFF(n.end_date, n.as_of_date, DAY) AS days_to_expiry,
    CASE
      WHEN n.end_date < n.as_of_date                                 THEN 'expired'
      WHEN n.end_date <= DATE_ADD(n.as_of_date, INTERVAL 6 MONTH)    THEN 'expiring_6m'
      WHEN n.end_date <= DATE_ADD(n.as_of_date, INTERVAL 12 MONTH)   THEN 'expiring_12m'
      ELSE 'active'
    END AS status
  FROM {{ ref('int_niche_filter') }} AS n
  LEFT JOIN {{ ref('stg_award_details') }} AS d USING (award_key)
  WHERE n.in_niche
)

SELECT
  *,
  CASE
    WHEN status NOT IN ('expiring_6m', 'expiring_12m') THEN NULL
    WHEN has_options_remaining                         THEN 'option_decision'
    WHEN subtype = 'executive_search'                  THEN 'one_off_search'
    ELSE 'likely_recompete'
  END AS recompete_type
FROM flagged
