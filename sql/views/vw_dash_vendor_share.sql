-- vw_dash_vendor_share: Competition page, market share. One row per vendor (UEI).
-- share_24m = share of niche obligations in the last 24 months; share_rank 1 = largest.
-- Charts: top-5 share KPI (SUM(share_24m) WHERE is_top5), share bar for the top 15.
-- Masked recipients ("FOREIGN AWARDEES (UNDISCLOSED)") are flagged so they can be filtered out.

CREATE OR REPLACE VIEW {{ table('vw_dash_vendor_share') }} AS
SELECT
  recipient_uei,
  recipient_name,
  is_masked_recipient,
  niche_awards,
  active_awards,
  obligations_24m,
  share_24m,
  RANK() OVER (ORDER BY obligations_24m DESC)          AS share_rank,
  RANK() OVER (ORDER BY obligations_24m DESC) <= 5     AS is_top5,
  RANK() OVER (ORDER BY obligations_24m DESC) <= 15    AS is_top15,
  as_of_date
FROM {{ table('dim_vendors') }}
