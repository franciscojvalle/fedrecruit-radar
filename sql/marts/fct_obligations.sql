-- fct_obligations: one row per contract action (modification), latest snapshot only.
--
-- Why this table exists: award_amount in raw_awards is cumulative over a contract's life,
-- so summing it by year puts multi-year money in the wrong fiscal year. Each transaction
-- carries its own action_date and obligation_amount, so SUM(obligation_amount) by
-- fiscal_year gives the money actually committed in each year.
--
-- Grain: award_key + modification_number (unique; enforced by the ingest validation).
-- Negative obligation_amount = de-obligation (money taken back). Kept on purpose:
-- dropping them would overstate spending.
-- Coverage: actions inside the lookback window only (FY2022 onward). Awards that started
-- earlier have their older actions outside the window, so their transaction sum is lower
-- than award_amount; that is expected, not an error.
-- Niche filter: not applied here. Join to the niche award list (int_niche_filter) in the
-- marts/views that need it, so the filter rule lives in one place.

-- No partitioning: the table is ~1k rows, far below where partitions pay off.
CREATE OR REPLACE TABLE fedrecruit.fct_obligations
OPTIONS (description = "One row per contract action (modification), latest snapshot. SUM(obligation_amount) by fiscal_year = money committed per year.")
AS
WITH latest AS (
  SELECT MAX(snapshot_date) AS snapshot_date
  FROM fedrecruit.raw_transactions
)
SELECT
  t.award_key,
  t.award_id,
  t.modification_number,
  t.modification_number = '0'                                   AS is_base_award,
  t.action_date,
  -- Federal fiscal year starts 1 Oct: FY = calendar year of (date + 3 months).
  EXTRACT(YEAR FROM DATE_ADD(t.action_date, INTERVAL 3 MONTH))   AS fiscal_year,
  t.action_type,
  t.obligation_amount,
  t.obligation_amount < 0                                        AS is_deobligation,
  t.awarding_agency,
  t.awarding_sub_agency,
  t.recipient_name,
  t.recipient_uei,
  t.contract_award_type,
  t.naics_code,
  t.psc_code,
  t.transaction_description,
  t.snapshot_date
FROM fedrecruit.raw_transactions AS t
JOIN latest USING (snapshot_date);
