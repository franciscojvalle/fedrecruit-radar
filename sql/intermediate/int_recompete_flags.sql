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
-- Set-aside type is not in the award search API; left for a later enrichment step.

SELECT
  n.*,
  DATE_DIFF(n.end_date, n.as_of_date, DAY) AS days_to_expiry,
  CASE
    WHEN n.end_date < n.as_of_date                                 THEN 'expired'
    WHEN n.end_date <= DATE_ADD(n.as_of_date, INTERVAL 6 MONTH)    THEN 'expiring_6m'
    WHEN n.end_date <= DATE_ADD(n.as_of_date, INTERVAL 12 MONTH)   THEN 'expiring_12m'
    ELSE 'active'
  END AS status
FROM {{ ref('int_niche_filter') }} AS n
WHERE n.in_niche
