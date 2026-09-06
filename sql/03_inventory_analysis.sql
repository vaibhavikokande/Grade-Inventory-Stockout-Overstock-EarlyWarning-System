-- =============================================================================
-- 03_inventory_analysis.sql
-- Inventory position, value, coverage and turnover.
--
-- NOTE ON PROVENANCE: every table in this script reads the SIMULATED inventory
-- ledger (see reports/ASSUMPTIONS.md). Demand is real; inventory is the output
-- of a documented replenishment policy run against that real demand.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Current position per SKU as of the as-of date
-- -----------------------------------------------------------------------------
CREATE OR REPLACE TABLE inventory_position AS
SELECT
    s.product_id,
    s.product_name,
    s.category,
    s.supplier_id,
    s.supplier_name,
    s.sourcing_region,
    s.lead_time_days,
    s.lead_time_sd_days,
    s.selling_price,
    s.unit_cost,
    s.total_units,
    s.total_revenue,
    s.avg_daily_demand,
    s.avg_daily_demand_90d,
    s.sd_daily_demand_90d,
    s.coef_variation_90d,
    s.demand_trend_ratio,
    s.units_90d,
    s.units_prev_90d,
    i.current_inventory,
    i.on_order,
    -- Inventory POSITION is what a planner actually decides on: stock you can
    -- sell plus stock already paid for and in transit.
    i.current_inventory + i.on_order                        AS inventory_position,
    ROUND(i.current_inventory * s.unit_cost, 2)             AS inventory_value,
    ROUND((i.current_inventory + i.on_order) * s.unit_cost, 2) AS inventory_position_value,
    -- Days of cover: how long current stock lasts at the recent demand rate.
    -- NULL (not zero) when demand is zero - a SKU with no demand has infinite
    -- cover, and coding that as 0 would wrongly flag it as a stockout risk.
    ROUND(i.current_inventory / NULLIF(s.avg_daily_demand_90d, 0), 1)
                                                            AS days_of_cover,
    CASE WHEN i.current_inventory <= 0 THEN TRUE ELSE FALSE END AS is_out_of_stock
FROM sku_demand_stats s
JOIN inventory_snapshot i USING (product_id);

-- -----------------------------------------------------------------------------
-- Inventory turnover and average inventory, trailing 365 days.
--
-- Turnover = COGS / average inventory value. Using COGS (not revenue) in the
-- numerator and cost-valued inventory in the denominator keeps both sides on a
-- cost basis - mixing the two is the most common way this metric is reported
-- wrongly.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE TABLE inventory_turnover AS
WITH trailing_365 AS (
    SELECT l.*, s.category, s.supplier_name
    FROM inventory_daily l
    JOIN sku_demand_stats s USING (product_id)
    CROSS JOIN params p
    WHERE l.invoice_date > p.as_of_date - INTERVAL 365 DAY
),
per_sku AS (
    SELECT product_id, category, supplier_name,
           SUM(units_fulfilled * unit_cost) AS cogs_365d,
           AVG(inventory_value)             AS avg_inventory_value,
           AVG(closing_inventory)           AS avg_inventory_units,
           SUM(units_fulfilled)             AS units_sold_365d
    FROM trailing_365 GROUP BY 1, 2, 3
)
SELECT product_id, category, supplier_name,
       ROUND(cogs_365d, 2)                                        AS cogs_365d,
       ROUND(avg_inventory_value, 2)                              AS avg_inventory_value,
       ROUND(avg_inventory_units, 1)                              AS avg_inventory_units,
       units_sold_365d,
       ROUND(cogs_365d / NULLIF(avg_inventory_value, 0), 2)       AS inventory_turns,
       ROUND(365 / NULLIF(cogs_365d / NULLIF(avg_inventory_value, 0), 0), 0)
                                                                  AS days_inventory_outstanding
FROM per_sku;

-- -----------------------------------------------------------------------------
-- Business-level inventory trend (executive page)
-- -----------------------------------------------------------------------------
CREATE OR REPLACE TABLE inventory_monthly AS
SELECT
    DATE_TRUNC('month', invoice_date)                AS month,
    ROUND(AVG(closing_inventory), 1)                 AS avg_inventory_units,
    ROUND(SUM(inventory_value) / COUNT(DISTINCT invoice_date), 2) AS avg_inventory_value,
    SUM(demand_units)                                AS demand_units,
    SUM(units_fulfilled)                             AS units_fulfilled,
    SUM(lost_units)                                  AS lost_units,
    ROUND(SUM(lost_revenue), 2)                      AS lost_revenue,
    ROUND(SUM(units_fulfilled) / NULLIF(SUM(demand_units), 0), 4) AS fill_rate
FROM inventory_daily
WHERE NOT is_warmup
GROUP BY 1 ORDER BY 1;

-- -----------------------------------------------------------------------------
-- Category-level inventory summary
-- -----------------------------------------------------------------------------
CREATE OR REPLACE TABLE inventory_by_category AS
SELECT
    category,
    COUNT(*)                                    AS n_skus,
    SUM(current_inventory)                      AS total_units,
    ROUND(SUM(inventory_value), 2)              AS total_inventory_value,
    ROUND(100 * SUM(inventory_value)
          / SUM(SUM(inventory_value)) OVER (), 2) AS pct_of_inventory_value,
    ROUND(MEDIAN(days_of_cover), 1)             AS median_days_of_cover,
    SUM(CASE WHEN is_out_of_stock THEN 1 ELSE 0 END) AS skus_out_of_stock
FROM inventory_position
GROUP BY category ORDER BY total_inventory_value DESC;

-- -----------------------------------------------------------------------------
-- Supplier-level inventory and service summary
-- -----------------------------------------------------------------------------
CREATE OR REPLACE TABLE inventory_by_supplier AS
WITH service AS (
    SELECT s.supplier_id,
           SUM(l.demand_units)    AS demand_units,
           SUM(l.units_fulfilled) AS units_fulfilled,
           SUM(l.lost_units)      AS lost_units,
           SUM(l.lost_revenue)    AS lost_revenue
    FROM inventory_daily l
    JOIN dim_product_supply s USING (product_id)
    WHERE NOT l.is_warmup
    GROUP BY 1
)
SELECT
    d.supplier_id,
    d.supplier_name,
    d.sourcing_region,
    d.supplier_lead_time_days,
    d.supplier_lead_time_sd_days,
    COUNT(p.product_id)                          AS n_skus,
    ROUND(SUM(p.inventory_value), 2)             AS inventory_value,
    ROUND(SUM(p.avg_daily_demand_90d * p.selling_price), 2) AS daily_revenue_exposure,
    ROUND(v.units_fulfilled / NULLIF(v.demand_units, 0), 4) AS historical_fill_rate,
    ROUND(v.lost_revenue, 2)                     AS historical_lost_revenue
FROM dim_supplier d
LEFT JOIN inventory_position p ON p.supplier_id = d.supplier_id
LEFT JOIN service v            ON v.supplier_id = d.supplier_id
GROUP BY 1,2,3,4,5, v.units_fulfilled, v.demand_units, v.lost_revenue
ORDER BY inventory_value DESC;
