-- =============================================================================
-- 06_dashboard_tables.sql
-- The Power BI consumption layer: a small star schema.
--
--                          dim_date
--                              |
--   dim_product_bi  --  fact_sku_risk  --  dim_supplier_bi
--                              |
--                        dim_risk_level
--
--   fact_demand_daily_bi and fact_forecast_bi hang off dim_product_bi/dim_date.
--
-- Design rules followed here:
--  * Facts hold keys + measures only; all descriptive text lives in dimensions.
--  * One row per grain, stated in each table's comment.
--  * No calculated percentages baked in - ratios that must respond to slicers
--    are left to DAX measures (see dashboard/powerbi/measures.dax).
-- =============================================================================

-- -----------------------------------------------------------------------------
-- DIMENSIONS
-- -----------------------------------------------------------------------------

-- Grain: one row per product SKU in the operational universe.
CREATE OR REPLACE TABLE dim_product_bi AS
SELECT
    r.product_id,
    r.product_name,
    r.category,
    r.abc_class,
    r.selling_price,
    r.unit_cost,
    ROUND(r.selling_price - r.unit_cost, 2)                      AS unit_margin,
    ROUND((r.selling_price - r.unit_cost) / NULLIF(r.selling_price, 0), 4) AS gross_margin_pct,
    r.lead_time_days,
    r.selected_model                                             AS forecast_model,
    s.is_intermittent,
    s.zero_day_share,
    p.first_sale_date,
    p.last_sale_date
FROM sku_risk r
JOIN forecast_selection s USING (product_id)
JOIN dim_product p        USING (product_id);

-- Grain: one row per supplier.
CREATE OR REPLACE TABLE dim_supplier_bi AS
SELECT
    d.supplier_id,
    d.supplier_name,
    d.sourcing_region,
    d.supplier_lead_time_days,
    d.supplier_lead_time_sd_days,
    COUNT(r.product_id)                        AS n_skus
FROM dim_supplier d
LEFT JOIN sku_risk r ON r.supplier_name = d.supplier_name
GROUP BY 1, 2, 3, 4, 5;

-- Grain: one row per risk level. Carries the sort order and colour so the
-- dashboard legend is consistent everywhere instead of alphabetical.
CREATE OR REPLACE TABLE dim_risk_level AS
SELECT * FROM (VALUES
    ('Critical Stockout Risk', 1, 'Stockout',  '#C0392B', 'Inventory cannot cover demand over the lead time'),
    ('Stockout Risk',          2, 'Stockout',  '#EB6834', 'Inventory has fallen below the reorder point'),
    ('Healthy',                3, 'Healthy',   '#1BAF7A', 'Inventory is within the target operating range'),
    ('Overstock Risk',         4, 'Overstock', '#EDA100', 'Inventory exceeds 1.5x the 90-day requirement'),
    ('Severe Overstock',       5, 'Overstock', '#4A3AA7', 'Inventory exceeds 3x the 90-day requirement')
) AS t(risk_level, risk_sort_order, risk_family, risk_colour_hex, risk_definition);

-- Grain: one row per calendar day across the data window plus the forecast
-- horizon, so the forecast can be plotted on the same axis as history.
CREATE OR REPLACE TABLE dim_date AS
WITH bounds AS (
    SELECT MIN(invoice_date) AS d0, MAX(invoice_date) + INTERVAL 90 DAY AS d1
    FROM demand_daily
)
SELECT
    d::DATE                                   AS date,
    YEAR(d)                                   AS year,
    MONTH(d)                                  AS month_number,
    MONTHNAME(d)                              AS month_name,
    STRFTIME(d, '%Y-%m')                      AS year_month,
    QUARTER(d)                                AS quarter,
    DAYOFWEEK(d)                              AS day_of_week,
    DAYNAME(d)                                AS day_name,
    WEEKOFYEAR(d)                             AS week_of_year,
    CASE WHEN d > (SELECT MAX(invoice_date) FROM demand_daily)
         THEN TRUE ELSE FALSE END             AS is_forecast_period
FROM bounds, UNNEST(GENERATE_SERIES(d0, d1, INTERVAL 1 DAY)) AS g(d);

-- -----------------------------------------------------------------------------
-- FACTS
-- -----------------------------------------------------------------------------

-- Grain: one row per SKU. The dashboard's central fact - current position,
-- risk, financial exposure and the recommended action.
CREATE OR REPLACE TABLE fact_sku_risk AS
SELECT
    r.product_id,
    d.supplier_id,
    r.risk_level,
    r.risk_family,
    r.is_actionable,
    r.is_dead_stock,
    r.priority_score,
    r.priority_rank,
    -- position
    r.current_inventory,
    r.on_order,
    r.inventory_position,
    r.inventory_value,
    -- policy
    r.safety_stock,
    r.reorder_point,
    r.expected_requirement,
    r.cover_ratio,
    r.excess_ratio_capped                AS excess_ratio,
    r.days_of_cover_forecast             AS days_of_cover,
    -- demand
    r.forecast_units_7d,
    r.forecast_units_30d,
    r.forecast_units_90d,
    r.forecast_seasonal_scenario,
    r.avg_daily_demand,
    r.sd_daily_demand,
    r.coef_variation_90d,
    r.demand_trend_ratio,
    -- financial exposure (ESTIMATES - see reports/ASSUMPTIONS.md)
    r.estimated_shortfall_units,
    r.estimated_lost_revenue,
    r.estimated_lost_margin,
    r.excess_units,
    r.excess_value,
    ROUND(r.excess_units * (r.selling_price - r.unit_cost), 2) AS margin_at_risk,
    r.recommended_order_qty,
    r.order_cost,
    -- history
    r.historical_fill_rate,
    r.historical_lost_revenue,
    r.zero_stock_days,
    -- forecast quality
    r.backtest_mase,
    r.backtest_smape,
    -- action
    a.action_type,
    a.action_priority,
    a.action_detail,
    a.action_rationale,
    a.action_value_gbp
FROM sku_risk r
LEFT JOIN recommendations a USING (product_id)
LEFT JOIN dim_supplier d    ON d.supplier_name = r.supplier_name;

-- Grain: one row per SKU per ISO week.
--
-- Weekly rather than daily deliberately. The daily grain is 1.54M rows / 84 MB
-- as CSV, which is too large to commit and needlessly heavy for a dashboard
-- whose smallest time axis is a week. Weekly keeps it at ~250k rows while
-- preserving every trend the report actually draws. The daily ledger remains
-- available in data/processed/inventory_daily.parquet for analysis.
CREATE OR REPLACE TABLE fact_demand_weekly_bi AS
SELECT
    l.product_id,
    DATE_TRUNC('week', l.invoice_date)  AS week_start,
    SUM(l.demand_units)                 AS demand_units,
    SUM(l.units_fulfilled)              AS units_fulfilled,
    SUM(l.lost_units)                   AS lost_units,
    ROUND(SUM(l.lost_revenue), 2)       AS lost_revenue,
    ROUND(AVG(l.closing_inventory), 1)  AS avg_inventory_units,
    ROUND(AVG(l.inventory_value), 2)    AS avg_inventory_value,
    SUM(CASE WHEN l.is_stockout THEN 1 ELSE 0 END)   AS stockout_days,
    SUM(CASE WHEN l.is_zero_stock THEN 1 ELSE 0 END) AS zero_stock_days
FROM inventory_daily l
WHERE NOT l.is_warmup
GROUP BY 1, 2;

-- Grain: one row per SKU per horizon. Powers the forecast page.
CREATE OR REPLACE TABLE fact_forecast_bi AS
SELECT
    f.product_id,
    f.horizon_days,
    f.forecast_units,
    f.forecast_daily_rate,
    f.forecast_units_seasonal_scenario,
    f.forecast_lower_80,
    f.forecast_upper_80,
    f.selected_model,
    s.backtest_mase,
    s.backtest_smape,
    s.backtest_horizon_abs_error,
    s.is_intermittent
FROM forecast_sku f
JOIN forecast_selection s USING (product_id);

-- Grain: one row per model per SKU. Powers the "how accurate are we" visual.
CREATE OR REPLACE TABLE fact_forecast_accuracy_bi AS
SELECT product_id, model, is_selected, is_intermittent,
       mae, rmse, smape, mase, horizon_abs_error, horizon_bias, n_folds
FROM forecast_accuracy;

-- -----------------------------------------------------------------------------
-- Executive KPI snapshot - one row, used for the header cards
-- -----------------------------------------------------------------------------
CREATE OR REPLACE TABLE kpi_executive AS
SELECT
    (SELECT COUNT(*) FROM sku_risk)                                          AS total_skus,
    (SELECT COUNT(*) FROM sku_risk WHERE risk_family = 'Stockout')           AS stockout_risk_skus,
    (SELECT COUNT(*) FROM sku_risk WHERE risk_level = 'Critical Stockout Risk') AS critical_stockout_skus,
    (SELECT COUNT(*) FROM sku_risk WHERE risk_family = 'Overstock')          AS overstock_risk_skus,
    (SELECT COUNT(*) FROM sku_risk WHERE is_dead_stock)                      AS dead_stock_skus,
    (SELECT COUNT(*) FROM sku_risk WHERE is_actionable)                      AS actionable_skus,
    (SELECT ROUND(SUM(estimated_lost_revenue), 2) FROM sku_risk)             AS estimated_lost_revenue,
    (SELECT ROUND(SUM(excess_value), 2) FROM sku_risk)                       AS excess_inventory_value,
    (SELECT ROUND(SUM(inventory_value), 2) FROM sku_risk)                    AS total_inventory_value,
    (SELECT ROUND(SUM(order_cost), 2) FROM sku_risk)                         AS recommended_order_value,
    (SELECT ROUND(SUM(cogs_365d) / NULLIF(SUM(avg_inventory_value), 0), 2)
       FROM inventory_turnover)                                              AS inventory_turns,
    (SELECT ROUND(365 / NULLIF(SUM(cogs_365d) / NULLIF(SUM(avg_inventory_value), 0), 0), 0)
       FROM inventory_turnover)                                              AS days_of_inventory,
    (SELECT ROUND(SUM(units_fulfilled) / NULLIF(SUM(demand_units), 0), 4)
       FROM stockout_summary)                                                AS historical_fill_rate,
    (SELECT as_of_date FROM params)                                          AS as_of_date;
