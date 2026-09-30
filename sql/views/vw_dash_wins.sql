-- vw_dash_wins: Competition page, "wins by vendor x fiscal year". One row per niche award.
-- win_fiscal_year = fiscal year the award was made (base obligation date, else start date).
-- COUNT(*) = awards won, SUM(award_amount) = obligated to date on those awards.

CREATE OR REPLACE VIEW {{ table('vw_dash_wins') }} AS
SELECT
  award_id,
  recipient_uei,
  recipient_name,
  is_masked_recipient,
  award_fiscal_year        AS win_fiscal_year,
  award_date,
  award_amount,
  awarding_agency          AS awarding_department,
  awarding_sub_agency,
  as_of_date
FROM {{ table('fact_awards') }}
