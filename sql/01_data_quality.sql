-- =============================================================================
-- 01_data_quality.sql
-- Independent SQL re-verification of the Python cleaning pipeline.
--
-- The point of this script is NOT to clean the data again - it is to prove, in
-- a different engine and a different language, that the cleaned tables satisfy
-- the invariants the pipeline claims. If any assertion below returns a non-zero
-- FAILED count, a downstream number cannot be trusted.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Invariant assertions over the cleaned tables
-- -----------------------------------------------------------------------------
CREATE OR REPLACE TABLE dq_assertions AS
WITH checks AS (

    SELECT 'No negative quantities in the sales table' AS assertion,
           COUNT(*) AS violations
    FROM transactions_clean WHERE quantity <= 0

    UNION ALL
    SELECT 'No null product_id in the sales table',
           COUNT(*) FROM transactions_clean WHERE product_id IS NULL

    UNION ALL
    SELECT 'No null or future invoice_date',
           COUNT(*) FROM transactions_clean, params
           WHERE invoice_date IS NULL OR invoice_date > params.as_of_date

    UNION ALL
    SELECT 'product_id is always upper-case (case variants merged)',
           COUNT(*) FROM transactions_clean WHERE product_id <> UPPER(product_id)

    UNION ALL
    SELECT 'No exact duplicate transaction lines remain',
           COALESCE(SUM(n - 1), 0) FROM (
               SELECT COUNT(*) AS n FROM transactions_clean
               GROUP BY invoice, product_id, description, quantity,
                        invoice_datetime, unit_price, customer_id, country
               HAVING COUNT(*) > 1)

    UNION ALL
    -- The critical one: the two workbook sheets overlapped on 2010-12-01..09.
    -- After treatment, no invoice may appear under two different source sheets.
    SELECT 'No invoice appears in both workbook sheets',
           COUNT(*) FROM (
               SELECT invoice FROM transactions_clean
               GROUP BY invoice HAVING COUNT(DISTINCT source_sheet) > 1)

    UNION ALL
    SELECT 'Demand panel has no negative demand',
           COUNT(*) FROM demand_daily WHERE demand_units < 0

    UNION ALL
    SELECT 'Demand panel is a complete SKU x day grid (no gaps)',
           (SELECT COUNT(*) FROM demand_daily)
         - (SELECT COUNT(DISTINCT product_id) * COUNT(DISTINCT invoice_date)
            FROM demand_daily)

    UNION ALL
    SELECT 'Inventory ledger never goes negative',
           COUNT(*) FROM inventory_daily WHERE closing_inventory < 0

    UNION ALL
    SELECT 'Ledger flow identity: opening - fulfilled = closing',
           COUNT(*) FROM inventory_daily
           WHERE ABS(opening_inventory - units_fulfilled - closing_inventory) > 0.001

    UNION ALL
    SELECT 'Ledger demand identity: fulfilled + lost = demand',
           COUNT(*) FROM inventory_daily
           WHERE ABS(units_fulfilled + lost_units - demand_units) > 0.001

    UNION ALL
    SELECT 'Every universe SKU has supply attributes',
           COUNT(*) FROM dim_product p
           LEFT JOIN dim_product_supply s USING (product_id)
           WHERE p.in_universe AND s.product_id IS NULL

    UNION ALL
    SELECT 'unit_cost is always below selling_price',
           COUNT(*) FROM dim_product_supply WHERE unit_cost >= selling_price

    UNION ALL
    SELECT 'Every SKU has a positive lead time',
           COUNT(*) FROM dim_product_supply WHERE lead_time_days < 1
)
SELECT assertion,
       violations,
       CASE WHEN violations = 0 THEN 'PASS' ELSE 'FAILED' END AS status
FROM checks
ORDER BY violations DESC, assertion;

-- -----------------------------------------------------------------------------
-- Volume reconciliation: raw -> cleaned -> universe
-- -----------------------------------------------------------------------------
CREATE OR REPLACE TABLE dq_reconciliation AS
SELECT 'Cleaned sales lines'          AS stage, COUNT(*)::BIGINT AS records,
       SUM(quantity)::BIGINT          AS units,
       ROUND(SUM(revenue), 2)         AS revenue
FROM transactions_clean
UNION ALL
SELECT 'Customer returns', COUNT(*), SUM(quantity), ROUND(SUM(revenue), 2)
FROM returns
UNION ALL
SELECT 'Warehouse adjustments (excluded)', COUNT(*), SUM(quantity), 0
FROM adjustments
UNION ALL
SELECT 'Universe SKUs only', COUNT(*), SUM(t.quantity), ROUND(SUM(t.revenue), 2)
FROM transactions_clean t
JOIN dim_product p USING (product_id)
WHERE p.in_universe;

-- -----------------------------------------------------------------------------
-- Demand-series profile that justifies the forecasting strategy
-- -----------------------------------------------------------------------------
CREATE OR REPLACE TABLE dq_demand_profile AS
WITH per_sku AS (
    SELECT product_id,
           COUNT(*)                                        AS n_days,
           SUM(CASE WHEN demand_units = 0 THEN 1 ELSE 0 END) AS zero_days,
           AVG(demand_units)                               AS mean_daily_demand,
           STDDEV_SAMP(demand_units)                       AS sd_daily_demand
    FROM demand_daily
    GROUP BY product_id
)
SELECT
    COUNT(*)                                                       AS n_skus,
    ROUND(AVG(zero_days::DOUBLE / n_days), 4)                      AS avg_zero_day_share,
    SUM(CASE WHEN zero_days::DOUBLE / n_days > 0.70 THEN 1 ELSE 0 END) AS skus_intermittent,
    SUM(CASE WHEN zero_days::DOUBLE / n_days <= 0.30 THEN 1 ELSE 0 END) AS skus_dense,
    ROUND(AVG(sd_daily_demand / NULLIF(mean_daily_demand, 0)), 3)  AS avg_coef_variation
FROM per_sku;
