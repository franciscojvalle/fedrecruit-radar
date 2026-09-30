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
-- Options (fact from the award detail page, no inference):
--   has_options_remaining  final possible end date is more than 30 days after the current end
--                          date, so the agency can extend without a new competition.
-- v1 shows facts only. Labels such as "likely recompete" or "one-off search" were tried and
-- dropped (2026-09-29): they mislabelled real contracts (e.g. Heidrick's $17.25M BOND program).

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

SELECT * FROM flagged
