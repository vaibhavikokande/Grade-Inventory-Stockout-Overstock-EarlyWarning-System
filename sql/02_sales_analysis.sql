-- =============================================================================
-- 02_sales_analysis.sql
-- Product and category performance: velocity, variability, ABC classification.
--
-- These tables answer "what is actually selling, how predictably, and how much
-- does it matter" - the inputs to every prioritisation decision later.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Per-SKU demand statistics over the trailing 90 days AND full history.
-- The 90-day window is what feeds safety stock; the full history gives context.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE TABLE sku_demand_stats AS
WITH windowed AS (
    SELECT d.product_id,
           d.demand_units,
           d.revenue,
           d.invoice_date,
           DATE_DIFF('day', d.invoice_date, p.as_of_date) AS days_ago
    FROM demand_daily d CROSS JOIN params p
),
full_hist AS (
    SELECT product_id,
           COUNT(*)                                          AS days_observed,
           SUM(demand_units)                                 AS total_units,
           SUM(revenue)                                      AS total_revenue,
           AVG(demand_units)                                 AS avg_daily_demand,
           STDDEV_SAMP(demand_units)                         AS sd_daily_demand,
           MAX(demand_units)                                 AS max_daily_demand,
           SUM(CASE WHEN demand_units > 0 THEN 1 ELSE 0 END) AS days_with_demand
    FROM windowed GROUP BY product_id
),
w90 AS (
    SELECT product_id,
           AVG(demand_units)                                 AS avg_daily_demand_90d,
           STDDEV_SAMP(demand_units)                         AS sd_daily_demand_90d,
           SUM(demand_units)                                 AS units_90d,
           SUM(revenue)                                      AS revenue_90d
    FROM windowed WHERE days_ago < 90 GROUP BY product_id
),
w30 AS (
    SELECT product_id,
           SUM(demand_units) AS units_30d,
           SUM(revenue)      AS revenue_30d
    FROM windowed WHERE days_ago < 30 GROUP BY product_id
),
prev90 AS (  -- the 90 days before the trailing 90, for trend detection
    SELECT product_id, SUM(demand_units) AS units_prev_90d
    FROM windowed WHERE days_ago >= 90 AND days_ago < 180 GROUP BY product_id
)
SELECT
    f.product_id,
    p.product_name,
    p.category,
    s.supplier_id,
    s.supplier_name,
    s.sourcing_region,
    s.lead_time_days,
    s.lead_time_sd_days,
    s.selling_price,
    s.unit_cost,
    f.days_observed,
    f.days_with_demand,
    ROUND(f.days_with_demand::DOUBLE / f.days_observed, 4) AS demand_frequency,
    f.total_units,
    ROUND(f.total_revenue, 2)                             AS total_revenue,
    ROUND(f.avg_daily_demand, 4)                          AS avg_daily_demand,
    ROUND(f.sd_daily_demand, 4)                           AS sd_daily_demand,
    f.max_daily_demand,
    ROUND(w.avg_daily_demand_90d, 4)                      AS avg_daily_demand_90d,
    ROUND(w.sd_daily_demand_90d, 4)                       AS sd_daily_demand_90d,
    -- Coefficient of variation is the scale-free measure of demand
    -- predictability and is the single best predictor of stockout risk.
    ROUND(w.sd_daily_demand_90d / NULLIF(w.avg_daily_demand_90d, 0), 3)
                                                          AS coef_variation_90d,
    COALESCE(w.units_90d, 0)                              AS units_90d,
    ROUND(COALESCE(w.revenue_90d, 0), 2)                  AS revenue_90d,
    COALESCE(t.units_30d, 0)                              AS units_30d,
    ROUND(COALESCE(t.revenue_30d, 0), 2)                  AS revenue_30d,
    COALESCE(v.units_prev_90d, 0)                         AS units_prev_90d,
    -- Demand trend: >1 means growing, <1 means declining. Drives the
    -- distinction between "overstocked but selling" and "dead stock".
    ROUND(COALESCE(w.units_90d, 0)::DOUBLE
          / NULLIF(v.units_prev_90d, 0), 3)               AS demand_trend_ratio
FROM full_hist f
JOIN dim_product p          USING (product_id)
JOIN dim_product_supply s   USING (product_id)
LEFT JOIN w90 w             USING (product_id)
LEFT JOIN w30 t             USING (product_id)
LEFT JOIN prev90 v          USING (product_id);

-- -----------------------------------------------------------------------------
-- ABC classification by revenue contribution (Pareto).
-- A = top 80% of revenue, B = next 15%, C = final 5%.
-- Service-level policy should differ by class; this table makes that possible.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE TABLE sku_abc AS
WITH ranked AS (
    SELECT product_id, product_name, category, total_revenue, total_units,
           SUM(total_revenue) OVER ()                                    AS grand_total,
           SUM(total_revenue) OVER (ORDER BY total_revenue DESC
                                    ROWS BETWEEN UNBOUNDED PRECEDING
                                             AND CURRENT ROW)            AS running_revenue,
           ROW_NUMBER() OVER (ORDER BY total_revenue DESC)               AS revenue_rank
    FROM sku_demand_stats
)
SELECT product_id, product_name, category, total_revenue, total_units, revenue_rank,
       ROUND(running_revenue / grand_total, 5) AS cumulative_revenue_share,
       ROUND(total_revenue / grand_total, 6)   AS revenue_share,
       CASE WHEN running_revenue / grand_total <= 0.80 THEN 'A'
            WHEN running_revenue / grand_total <= 0.95 THEN 'B'
            ELSE 'C' END                       AS abc_class
FROM ranked;

-- -----------------------------------------------------------------------------
-- Category performance summary
-- -----------------------------------------------------------------------------
CREATE OR REPLACE TABLE category_performance AS
SELECT
    s.category,
    COUNT(*)                                            AS n_skus,
    SUM(s.total_units)                                  AS total_units,
    ROUND(SUM(s.total_revenue), 2)                      AS total_revenue,
    ROUND(100 * SUM(s.total_revenue)
          / SUM(SUM(s.total_revenue)) OVER (), 2)       AS pct_of_revenue,
    ROUND(AVG(s.selling_price), 2)                      AS avg_selling_price,
    ROUND(AVG(s.coef_variation_90d), 3)                 AS avg_coef_variation,
    ROUND(AVG(s.lead_time_days), 1)                     AS avg_lead_time_days,
    ROUND(SUM(s.revenue_90d), 2)                        AS revenue_90d,
    SUM(CASE WHEN a.abc_class = 'A' THEN 1 ELSE 0 END)  AS n_class_a_skus
FROM sku_demand_stats s
JOIN sku_abc a USING (product_id)
GROUP BY s.category
ORDER BY total_revenue DESC;

-- -----------------------------------------------------------------------------
-- Business-level demand trend (used for the executive trend chart)
-- -----------------------------------------------------------------------------
CREATE OR REPLACE TABLE demand_monthly AS
SELECT DATE_TRUNC('month', invoice_date)      AS month,
       SUM(demand_units)                      AS units,
       ROUND(SUM(revenue), 2)                 AS revenue,
       COUNT(DISTINCT product_id)             AS active_skus
FROM demand_daily
GROUP BY 1 ORDER BY 1;

-- -----------------------------------------------------------------------------
-- Rolling demand views used by the forecasting diagnostics
-- -----------------------------------------------------------------------------
CREATE OR REPLACE TABLE demand_rolling AS
SELECT
    product_id,
    invoice_date,
    demand_units,
    AVG(demand_units) OVER (PARTITION BY product_id ORDER BY invoice_date
                            ROWS BETWEEN 6 PRECEDING AND CURRENT ROW)  AS rolling_7d_avg,
    SUM(demand_units) OVER (PARTITION BY product_id ORDER BY invoice_date
                            ROWS BETWEEN 6 PRECEDING AND CURRENT ROW)  AS rolling_7d_sum,
    AVG(demand_units) OVER (PARTITION BY product_id ORDER BY invoice_date
                            ROWS BETWEEN 29 PRECEDING AND CURRENT ROW) AS rolling_30d_avg,
    SUM(demand_units) OVER (PARTITION BY product_id ORDER BY invoice_date
                            ROWS BETWEEN 29 PRECEDING AND CURRENT ROW) AS rolling_30d_sum,
    STDDEV_SAMP(demand_units) OVER (PARTITION BY product_id ORDER BY invoice_date
                            ROWS BETWEEN 29 PRECEDING AND CURRENT ROW) AS rolling_30d_sd
FROM demand_daily;
