-- =============================================================================
-- 04_stockout_analysis.sql
-- Historical stockout behaviour: frequency, duration, and financial cost.
--
-- This is BACKWARD-LOOKING. It measures what the incumbent replenishment policy
-- actually did. The forward-looking risk classification lives in Python
-- (src/risk_scoring.py) because it depends on the demand forecast.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Ledger restricted to each SKU's ACTIVE LIFE.
--
-- Why this matters: the ledger spans the full 739-day calendar for every SKU,
-- but many SKUs were introduced part-way through. Before a SKU's first sale it
-- sits at zero stock with zero demand. Counting those days as "stockout days"
-- produced 474-day phantom outages for products that simply did not exist yet.
-- A stockout is only meaningful once the SKU is actually being sold.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW ledger_active AS
SELECT l.*
FROM inventory_daily l
JOIN dim_product p USING (product_id)
WHERE NOT l.is_warmup
  AND l.invoice_date >= p.first_sale_date
  AND l.invoice_date <= p.last_sale_date;

-- -----------------------------------------------------------------------------
-- Per-SKU stockout summary (warm-up and pre-launch excluded)
-- -----------------------------------------------------------------------------
CREATE OR REPLACE TABLE stockout_summary AS
WITH base AS (
    SELECT * FROM ledger_active
),
agg AS (
    SELECT
        product_id,
        COUNT(*)                                              AS days_observed,
        SUM(CASE WHEN is_zero_stock THEN 1 ELSE 0 END)        AS zero_stock_days,
        -- A stockout only costs money when there was demand to lose.
        SUM(CASE WHEN is_stockout THEN 1 ELSE 0 END)          AS stockout_days_with_demand,
        SUM(demand_units)                                     AS demand_units,
        SUM(units_fulfilled)                                  AS units_fulfilled,
        SUM(lost_units)                                       AS lost_units,
        SUM(lost_revenue)                                     AS lost_revenue,
        MAX(lost_units)                                       AS worst_day_lost_units
    FROM base GROUP BY product_id
)
SELECT
    a.product_id,
    s.product_name,
    s.category,
    s.supplier_name,
    s.sourcing_region,
    s.lead_time_days,
    s.selling_price,
    a.days_observed,
    a.zero_stock_days,
    a.stockout_days_with_demand,
    ROUND(a.zero_stock_days::DOUBLE / a.days_observed, 4)     AS zero_stock_day_share,
    a.demand_units,
    a.units_fulfilled,
    a.lost_units,
    -- Fill rate (unit service level): the share of demanded units actually
    -- shipped. This is the metric the business feels.
    ROUND(a.units_fulfilled / NULLIF(a.demand_units, 0), 4)   AS fill_rate,
    ROUND(a.lost_revenue, 2)                                  AS lost_revenue,
    a.worst_day_lost_units,
    ROUND(a.lost_revenue / NULLIF(a.days_observed, 0) * 365, 2) AS annualised_lost_revenue
FROM agg a
JOIN sku_demand_stats s USING (product_id);

-- -----------------------------------------------------------------------------
-- Stockout EPISODES: consecutive runs of zero stock.
--
-- Counting stockout DAYS overstates the operational pain - one 30-day outage
-- and thirty scattered 1-day outages are very different problems. This uses the
-- classic "row_number minus row_number" gaps-and-islands trick to group
-- consecutive zero-stock days into episodes.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE TABLE stockout_episodes AS
WITH flagged AS (
    SELECT product_id, invoice_date, demand_units, lost_units, lost_revenue,
           is_zero_stock,
           ROW_NUMBER() OVER (PARTITION BY product_id ORDER BY invoice_date)
         - ROW_NUMBER() OVER (PARTITION BY product_id, is_zero_stock
                              ORDER BY invoice_date) AS island
    FROM ledger_active
),
episodes AS (
    SELECT product_id, island,
           MIN(invoice_date)  AS started_on,
           MAX(invoice_date)  AS ended_on,
           COUNT(*)           AS duration_days,
           SUM(lost_units)    AS lost_units,
           SUM(lost_revenue)  AS lost_revenue
    FROM flagged
    WHERE is_zero_stock
    GROUP BY product_id, island
)
SELECT e.product_id, s.product_name, s.category, s.supplier_name,
       s.lead_time_days,
       e.started_on, e.ended_on, e.duration_days,
       e.lost_units, ROUND(e.lost_revenue, 2) AS lost_revenue
FROM episodes e
JOIN sku_demand_stats s USING (product_id)
ORDER BY e.lost_revenue DESC;

-- -----------------------------------------------------------------------------
-- Episode statistics per SKU: is this SKU chronically short, or occasionally?
-- -----------------------------------------------------------------------------
CREATE OR REPLACE TABLE stockout_episode_stats AS
SELECT
    product_id,
    COUNT(*)                        AS n_episodes,
    ROUND(AVG(duration_days), 1)    AS avg_episode_days,
    MAX(duration_days)              AS longest_episode_days,
    SUM(duration_days)              AS total_zero_stock_days,
    ROUND(SUM(lost_revenue), 2)     AS lost_revenue
FROM stockout_episodes
GROUP BY product_id;

-- -----------------------------------------------------------------------------
-- Where does the lost revenue actually sit? (category / supplier rollups)
-- -----------------------------------------------------------------------------
CREATE OR REPLACE TABLE stockout_by_category AS
SELECT category,
       COUNT(*)                                                  AS n_skus,
       SUM(CASE WHEN lost_units > 0 THEN 1 ELSE 0 END)           AS n_skus_with_stockouts,
       SUM(demand_units)                                         AS demand_units,
       SUM(lost_units)                                           AS lost_units,
       ROUND(SUM(units_fulfilled) / NULLIF(SUM(demand_units), 0), 4) AS fill_rate,
       ROUND(SUM(lost_revenue), 2)                               AS lost_revenue,
       ROUND(100 * SUM(lost_revenue) / SUM(SUM(lost_revenue)) OVER (), 2)
                                                                 AS pct_of_lost_revenue
FROM stockout_summary
GROUP BY category ORDER BY lost_revenue DESC;

CREATE OR REPLACE TABLE stockout_by_supplier AS
SELECT supplier_name, sourcing_region,
       COUNT(*)                                                  AS n_skus,
       ROUND(AVG(lead_time_days), 1)                             AS avg_lead_time_days,
       SUM(demand_units)                                         AS demand_units,
       SUM(lost_units)                                           AS lost_units,
       ROUND(SUM(units_fulfilled) / NULLIF(SUM(demand_units), 0), 4) AS fill_rate,
       ROUND(SUM(lost_revenue), 2)                               AS lost_revenue
FROM stockout_summary
GROUP BY supplier_name, sourcing_region ORDER BY lost_revenue DESC;

-- -----------------------------------------------------------------------------
-- Does service degrade with lead time and demand volatility, as theory says?
-- This is the empirical justification for the safety-stock formula used later.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE TABLE stockout_drivers AS
WITH banded AS (
    SELECT
        s.product_id,
        s.fill_rate,
        s.lead_time_days,
        d.coef_variation_90d,
        CASE WHEN s.lead_time_days <= 10 THEN '1: <=10 days'
             WHEN s.lead_time_days <= 20 THEN '2: 11-20 days'
             WHEN s.lead_time_days <= 30 THEN '3: 21-30 days'
             ELSE                              '4: >30 days' END AS lead_time_band,
        NTILE(4) OVER (ORDER BY d.coef_variation_90d)            AS volatility_quartile
    FROM stockout_summary s
    JOIN sku_demand_stats d USING (product_id)
    WHERE d.coef_variation_90d IS NOT NULL
)
SELECT lead_time_band,
       volatility_quartile,
       COUNT(*)                     AS n_skus,
       ROUND(AVG(fill_rate), 4)     AS avg_fill_rate
FROM banded
GROUP BY lead_time_band, volatility_quartile
ORDER BY lead_time_band, volatility_quartile;
