-- =============================================================================
-- 05_overstock_analysis.sql
-- Where is working capital tied up, and how badly?
--
-- Depends on the Python risk layer (src/inventory_metrics.py, src/risk_scoring.py)
-- because excess is defined against a FORECAST requirement, not a trailing
-- average. Run after `python -m src.risk_scoring`.
--
-- DEFINITION (documented in reports/ASSUMPTIONS.md):
--   expected_requirement = forecast_demand(90d) + safety_stock
--   excess_units         = max(0, inventory_position - expected_requirement)
--   excess_ratio         = inventory_position / expected_requirement
--   Overstock        when excess_ratio > 1.5
--   Severe Overstock when excess_ratio > 3.0
--
-- The 90-day horizon is not arbitrary: this business turns inventory 2.2x a
-- year (165 days of stock), so a 30-day yardstick would flag 78% of SKUs and
-- produce an unusable alert list. Sensitivity at 30/60/90 days and at 1.5/2/3x
-- is published in outputs/business_insights/overstock_threshold_sensitivity.csv
-- =============================================================================

-- -----------------------------------------------------------------------------
-- SKU-level overstock detail - the "where is capital tied up" table
-- -----------------------------------------------------------------------------
CREATE OR REPLACE TABLE overstock_detail AS
SELECT
    r.product_id,
    r.product_name,
    r.category,
    r.supplier_name,
    r.sourcing_region,
    r.abc_class,
    r.risk_level,
    r.is_dead_stock,
    r.priority_score,
    r.current_inventory,
    r.on_order,
    r.inventory_position,
    r.inventory_value,
    r.forecast_units_90d,
    r.safety_stock,
    r.expected_requirement,
    r.excess_units,
    r.excess_value,
    r.excess_ratio_capped                                    AS excess_ratio,
    r.days_of_cover_forecast,
    r.unit_cost,
    r.selling_price,
    -- Markdown exposure: the gross margin that would be given away if this
    -- stock has to be discounted to clear.
    ROUND(r.excess_units * (r.selling_price - r.unit_cost), 2) AS margin_at_risk,
    -- Share of this SKU's own inventory value that is excess.
    ROUND(r.excess_value / NULLIF(r.inventory_value, 0), 3)   AS pct_value_excess
FROM sku_risk r
WHERE r.risk_family = 'Overstock'
ORDER BY r.excess_value DESC;

-- -----------------------------------------------------------------------------
-- Capital tied up, rolled up the ways a buyer would ask for it
-- -----------------------------------------------------------------------------
CREATE OR REPLACE TABLE overstock_by_category AS
SELECT
    category,
    COUNT(*)                                        AS n_overstocked_skus,
    SUM(CASE WHEN is_dead_stock THEN 1 ELSE 0 END)  AS n_dead_stock_skus,
    SUM(excess_units)                               AS excess_units,
    ROUND(SUM(excess_value), 2)                     AS excess_value,
    ROUND(SUM(margin_at_risk), 2)                   AS margin_at_risk,
    ROUND(100 * SUM(excess_value) / SUM(SUM(excess_value)) OVER (), 2) AS pct_of_excess_value,
    ROUND(MEDIAN(excess_ratio), 2)                  AS median_excess_ratio
FROM overstock_detail
GROUP BY category
ORDER BY excess_value DESC;

CREATE OR REPLACE TABLE overstock_by_supplier AS
SELECT
    supplier_name,
    sourcing_region,
    COUNT(*)                                        AS n_overstocked_skus,
    SUM(CASE WHEN is_dead_stock THEN 1 ELSE 0 END)  AS n_dead_stock_skus,
    ROUND(SUM(excess_value), 2)                     AS excess_value,
    ROUND(SUM(margin_at_risk), 2)                   AS margin_at_risk,
    ROUND(MEDIAN(excess_ratio), 2)                  AS median_excess_ratio
FROM overstock_detail
GROUP BY supplier_name, sourcing_region
ORDER BY excess_value DESC;

-- -----------------------------------------------------------------------------
-- Dead stock: forecast demand has effectively gone to zero.
-- A different commercial problem from over-ordering, and a different action.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE TABLE dead_stock AS
SELECT
    product_id, product_name, category, supplier_name, abc_class,
    current_inventory, inventory_value, forecast_units_90d,
    unit_cost, selling_price,
    ROUND(current_inventory * (selling_price - unit_cost), 2) AS margin_at_risk
FROM overstock_detail
WHERE is_dead_stock
ORDER BY inventory_value DESC;

-- -----------------------------------------------------------------------------
-- Working-capital summary: the single number for the executive page
-- -----------------------------------------------------------------------------
CREATE OR REPLACE TABLE working_capital_summary AS
SELECT
    (SELECT ROUND(SUM(inventory_value), 2) FROM sku_risk)                AS total_inventory_value,
    (SELECT ROUND(SUM(excess_value), 2) FROM overstock_detail)           AS total_excess_value,
    (SELECT ROUND(SUM(inventory_value), 2) FROM dead_stock)              AS dead_stock_value,
    (SELECT COUNT(*) FROM overstock_detail)                              AS n_overstocked_skus,
    (SELECT COUNT(*) FROM dead_stock)                                    AS n_dead_stock_skus,
    ROUND(100.0 * (SELECT SUM(excess_value) FROM overstock_detail)
                / (SELECT SUM(inventory_value) FROM sku_risk), 2)        AS pct_inventory_excess;
