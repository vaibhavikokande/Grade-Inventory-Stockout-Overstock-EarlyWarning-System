# Power BI Build Guide

Everything needed to assemble the four-page report in Power BI Desktop.
Budget roughly 1.5–2 hours.

> **Why there is no `.pbix` in this repo.** `.pbix` is a proprietary compiled
> binary that cannot be authored outside Power BI Desktop. What ships instead
> is the complete semantic layer — the star-schema tables, every DAX measure,
> and the exact visual specification below. The analytical work is done; the
> assembly is the part you own.

---

## 1. Load the data

`Get Data → Text/CSV` → `dashboard/powerbi/data/` → load these nine files:

| File | Role | Rows |
| --- | --- | --- |
| `fact_sku_risk.csv` | Central fact — one row per SKU | 2,370 |
| `fact_demand_weekly_bi.csv` | SKU × week history | 220,410 |
| `fact_forecast_bi.csv` | SKU × horizon forecast | 11,850 |
| `fact_forecast_accuracy_bi.csv` | SKU × model backtest scores | 18,341 |
| `dim_product_bi.csv` | Product dimension | 2,370 |
| `dim_supplier_bi.csv` | Supplier dimension | 12 |
| `dim_risk_level.csv` | Risk level + colour + sort order | 5 |
| `dim_date.csv` | Date dimension | 829 |
| `kpi_executive.csv` | One-row KPI snapshot | 1 |

Optionally add the pre-aggregated rollups (`*_by_category`, `*_by_supplier`,
`stockout_episodes`, `dead_stock`, `dq_assertions`) — useful for quick visuals
but not required, since every one can be derived from the facts.

### Set data types on load
`inventory_value`, `excess_value`, `estimated_lost_revenue`, `order_cost`,
`selling_price`, `unit_cost` → **Fixed decimal number**, format `£#,##0`.
`cover_ratio`, `excess_ratio`, `backtest_mase` → **Decimal number**.
`week_start`, `date`, `as_of_date` → **Date**.

---

## 2. Build the model

Model view → create these relationships (all **single-direction**,
one-to-many from dimension to fact):

| From (1) | To (*) | Cardinality |
| --- | --- | --- |
| `dim_product_bi[product_id]` | `fact_sku_risk[product_id]` | 1:1 — set to **Both** filter direction |
| `dim_product_bi[product_id]` | `fact_demand_weekly_bi[product_id]` | 1:* |
| `dim_product_bi[product_id]` | `fact_forecast_bi[product_id]` | 1:* |
| `dim_product_bi[product_id]` | `fact_forecast_accuracy_bi[product_id]` | 1:* |
| `dim_supplier_bi[supplier_id]` | `fact_sku_risk[supplier_id]` | 1:* |
| `dim_risk_level[risk_level]` | `fact_sku_risk[risk_level]` | 1:* |
| `dim_date[date]` | `fact_demand_weekly_bi[week_start]` | 1:* |

Then:

1. `Table tools → Mark as date table` on **dim_date** (date column: `date`).
2. Select `dim_risk_level[risk_level]` → `Column tools → Sort by column` →
   `risk_sort_order`. **Do this before building any visual**, otherwise every
   legend renders alphabetically ("Critical" next to "Healthy") and the
   red→purple severity gradient reads as noise.
3. Hide from report view: all `product_id` / `supplier_id` key columns on the
   fact tables, and `risk_sort_order`.

## 3. Add the measures

Create a measures table: `Home → Enter data`, name it `_Measures`, one dummy
column, Load, then delete the column. Paste each measure from
[`measures.dax`](measures.dax) via `Modeling → New measure`.

Add the two **calculated columns** from section 8 of that file to
`dim_product_bi` and `fact_sku_risk` respectively (New column, not New measure).

---

## 4. Design system

Apply once so all four pages match.

| Element | Specification |
| --- | --- |
| Canvas | 1280 × 720, background `#F7F8FA` |
| Font | Segoe UI throughout |
| Page title | 20pt Semibold `#1A1A2E` |
| Visual title | 13pt Semibold `#2C3E50` |
| Body / labels | 10pt Regular `#5A6572` |
| KPI card value | 28pt Semibold |
| Cards | White fill, 8px corner radius, subtle shadow, 1px `#E4E7EB` border |
| Grid | 12-column, 16px gutters |

**Risk palette** — use these exact hex values everywhere (they match
`dim_risk_level[risk_colour_hex]`):

| Level | Hex |
| --- | --- |
| 🔴 Critical Stockout Risk | `#C0392B` |
| 🟠 Stockout Risk | `#EB6834` |
| 🟢 Healthy | `#1BAF7A` |
| 🟡 Overstock Risk | `#EDA100` |
| 🟣 Severe Overstock | `#4A3AA7` |

Neutral accent for non-risk series: `#2A78D6`. Never use a second palette.

These five were chosen by running the CVD validator: worst adjacent
colour-blind separation is ΔE 19.1, comfortably above the 12 threshold.
Green and yellow sit below 3:1 contrast on a light surface, so every
visual using them must carry visible data labels — never colour alone.

**Deliberately excluded:** 3-D effects, gauges, donut charts with >4 slices,
pie charts, background images, gradient fills, and any visual that does not
change a decision.

---

## 5. Page 1 — Executive Inventory Overview

**Question: how exposed are we, and where?**

Dynamic title: bind Title → fx → `[Title Executive]`.

**KPI card row** (7 cards, full width, y=70, height 110):

| # | Measure | Format | Note |
| --- | --- | --- | --- |
| 1 | `[Total SKUs]` | `#,##0` | |
| 2 | `[Stockout Risk SKUs]` | `#,##0` | Red accent bar |
| 3 | `[Overstock Risk SKUs]` | `#,##0` | Purple accent bar |
| 4 | `[Est. Lost Revenue]` | `£#,##0` | Label must read "Est." |
| 5 | `[Excess Inventory Value]` | `£#,##0` | |
| 6 | `[Inventory Turns]` | `0.0"x"` | |
| 7 | `[Avg Days of Inventory]` | `#,##0" days"` | |

**Visuals:**

| Position | Visual | Fields | Why |
| --- | --- | --- | --- |
| Left, y=200, 5 cols | **Stacked bar — risk distribution** | Axis `dim_risk_level[risk_level]`; Value `[Total SKUs]`; Colour by `risk_colour_hex` (Format style → Field value) | The single "how is the portfolio split" answer |
| Right, y=200, 7 cols | **Two stacked line charts sharing an x-axis** — top: `[Demand Units]`; bottom: `[Avg Weekly Inventory Value]`. Axis `dim_date[year_month]` on both | Shows stock building ahead of the Christmas peak and failing to unwind after it. **Do not build this as a dual-axis combo chart** — two y-scales on one plot let you manufacture any apparent relationship by rescaling, and the reader cannot tell where the axes were pinned. Two aligned charts show the same comparison honestly |
| Left, y=420, 6 cols | **Bar — excess value by category** | Axis `dim_product_bi[category]`; Value `[Excess Inventory Value]`; sort desc; data labels on | Where the trapped capital sits |
| Right, y=420, 6 cols | **Scatter — risk map** | X `[Avg Days of Inventory]`; Y `[Fill Rate]`; Size `[Total Inventory Value]`; Legend `risk_level`; Details `category` | Two failure modes on one canvas: bottom-left = stocking out, right = overstocked |

**Slicers** (top strip, horizontal): `category`, `sourcing_region`, `abc_class`.
Set `Sync slicers` across all four pages.

---

## 6. Page 2 — Stockout Risk

**Question: which products do I reorder first?**

Title: `[Title Stockout Page]`.

Page-level filter: `fact_sku_risk[risk_family] = "Stockout"`.

**KPI row:** `[Critical Stockout SKUs]`, `[Stockout Risk SKUs]`,
`[Est. Lost Revenue]`, `[Recommended Order Value]`, `[Fill Rate]`.

**Main table — "Reorder priority list"** (the page's reason to exist).
Sort by `priority_score` descending.

| Column | Source | Format |
| --- | --- | --- |
| Priority | `priority_rank` | `#0` |
| Product | `dim_product_bi[product_name]` | |
| Category | `dim_product_bi[category]` | |
| Supplier | `dim_supplier_bi[supplier_name]` | |
| Risk | `risk_level` | Colour by `[Risk Colour]` |
| On hand | `current_inventory` | `#,##0` |
| On order | `on_order` | `#,##0` |
| Reorder point | `reorder_point` | `#,##0` |
| Cover ratio | `cover_ratio` | `0.00`, data bars, colour `[Cover Ratio Colour]` |
| Lead time | `lead_time_days` | `#0" d"` |
| Forecast 30d | `[Forecast Units 30d]` | `#,##0` |
| Est. lost revenue | `[Est. Lost Revenue]` | `£#,##0` |
| **Order qty** | `recommended_order_qty` | `#,##0`, bold |
| Score | `priority_score` | `#0`, data bars |

Conditional formatting: background colour on `cover_ratio` via
`[Cover Ratio Colour]`; data bars on `priority_score`.

**Supporting visuals:**
- **Bar — est. lost revenue by supplier**, sorted desc. Answers "which supplier
  is the replenishment bottleneck".
- **Scatter — lead time vs cover ratio**, size = est. lost revenue. The
  top-right quadrant (long lead time, low cover) is where to escalate.

**Tooltip page** (`Page information → Tooltip = On`, canvas 320×240): show
`product_name`, `action_detail`, `action_rationale`, `safety_stock`,
`backtest_mase`. Assign it as the tooltip on the main table.

**Drill-through:** create a "SKU Detail" page with drill-through field
`dim_product_bi[product_id]` — see Page 4.

---

## 7. Page 3 — Overstock Risk

**Question: where is capital unnecessarily tied up?**

Title: `[Title Overstock Page]`.
Page-level filter: `fact_sku_risk[risk_family] = "Overstock"`.

**KPI row:** `[Excess Inventory Value]`, `[Severe Overstock SKUs]`,
`[Dead Stock SKUs]`, `[Dead Stock Value]`, `[% Inventory Excess]`,
`[Margin At Risk]`.

**Main table — "Capital release opportunities"**, sorted by `excess_value` desc:

| Column | Source | Format |
| --- | --- | --- |
| Product / Category / Supplier | dimensions | |
| Risk | `risk_level` | `[Risk Colour]` |
| Dead stock? | `is_dead_stock` | ✓ icon |
| On hand | `current_inventory` | `#,##0` |
| 90-day forecast | `[Forecast Units 90d]` | `#,##0` |
| Requirement | `expected_requirement` | `#,##0` |
| Excess units | `excess_units` | `#,##0` |
| **Excess value** | `[Excess Inventory Value]` | `£#,##0`, bold, data bars |
| Excess ratio | `excess_ratio` | `0.0"x"` |
| Days of cover | `days_of_cover` | `#,##0` |
| Action | `action_type` | |

**Supporting visuals:**
- **Treemap — excess value**, group `category`, detail `product_name`. Size
  makes the concentration obvious at a glance.
- **Column — SKU count by `Cover Band`** (the calculated column). Shows the
  long tail of stock with >365 days of cover.
- **Bar — dead stock value by category**, filtered `is_dead_stock = True`.

> Label the dead-stock visuals clearly. Dead stock is a *different* problem
> from over-ordering — the action is liquidation, not a smaller reorder — and
> the report should not blur them.

---

## 8. Page 4 — Forecast & Demand

**Question: what do we expect, and can we trust it?**

**Hierarchy slicer:** `category` → `product_name`, enabling
Category → Product → Forecast drill-down.

**Visuals:**

| Visual | Fields | Note |
| --- | --- | --- |
| **Line — history vs forecast** | Axis `dim_date[date]`; Line 1 `[Demand Units]`; Line 2 `[Forecast Units 30d]` | Use `is_forecast_period` to style the forecast segment dashed |
| **Shaded band — 80% interval** | Area between `forecast_lower_80` and `forecast_upper_80` | Empirical interval from backtest errors, not a model-implied CI |
| **Column — scenario comparison** | `[Forecast Units 90d]` vs `[Forecast Seasonal Scenario]` | **Do not omit this.** The as-of date is the Christmas peak; the two scenarios differ by ~40% and the gap is the honest uncertainty |
| **Bar — model selection counts** | Axis `dim_product_bi[forecast_model]`; Value `[Total SKUs]` | Shows the routing between intermittent and dense families |
| **Scatter — accuracy** | X `coef_variation_90d`; Y `backtest_mase`; reference line at y=1 | Below the line = beats naive. Makes the volatility/accuracy relationship visible |
| **Card row** | `[Median MASE]`, `[% SKUs Beating Naive]`, `[Median sMAPE]` | Honest accuracy reporting |

**SKU Detail drill-through page** (hidden; drill-through field `product_id`):
demand sparkline, current position vs reorder point vs requirement, the full
`action_detail` and `action_rationale` text, and the model comparison table
from `fact_forecast_accuracy_bi`.

---

## 9. Final checks

- [ ] `dim_risk_level` sorted by `risk_sort_order` — legends read red → purple
- [ ] Every currency measure formatted `£#,##0`; no raw decimals on canvas
- [ ] Slicers synced across pages
- [ ] Estimate-based cards carry the "Est." label
- [ ] A visible footnote: *"Demand is real (UCI Online Retail II). Inventory,
      cost, supplier and lead time are simulated — see reports/ASSUMPTIONS.md."*
- [ ] Tab order set for accessibility; alt text on each visual
- [ ] Test with all slicers cleared **and** with a single category selected —
      confirm `[No Data Message]` appears rather than an empty visual
