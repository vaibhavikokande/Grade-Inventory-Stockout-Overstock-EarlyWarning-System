# Data Dictionary

Every field is tagged with its provenance:

| Tag | Meaning |
| --- | --- |
| **SOURCE** | Present verbatim in the raw Online Retail II workbook |
| **DERIVED** | Computed from source fields by documented logic |
| **SIMULATED** | Not available in any public source; generated from stated assumptions (see `reports/ASSUMPTIONS.md`) |

> The distinction matters. Every conclusion about *demand* rests on SOURCE data.
> Every conclusion about *inventory position, cost and supplier* rests on a
> SIMULATED layer, and is labelled as such throughout the project.

---

## 1. Source dataset

**Online Retail II** — UCI Machine Learning Repository.
A UK-based online giftware wholesaler. Two Excel sheets covering
2009-12-01 → 2011-12-09.

| Property | Value |
| --- | --- |
| Raw rows | 1,067,371 |
| Raw columns | 8 |
| Distinct stock codes (raw) | 5,304 |
| Date range | 2009-12-01 → 2011-12-09 (739 calendar days) |
| Trading days | 604 (the business does not trade Saturdays) |
| Countries | 43 (91.9% of rows United Kingdom) |

### Raw schema

| Column | Type | Provenance | Notes |
| --- | --- | --- | --- |
| `Invoice` | text | SOURCE | 6-digit number. `C` prefix = cancellation, `A` prefix = bad-debt adjustment |
| `StockCode` | text | SOURCE | 4–5 digits + optional letter variant. Also holds non-product service codes |
| `Description` | text | SOURCE | Product name; also carries warehouse annotations. 4,382 nulls; 4 cells hold a numeric code |
| `Quantity` | integer | SOURCE | Negative on returns and write-offs |
| `InvoiceDate` | datetime | SOURCE | Timestamped to the minute |
| `Price` | float | SOURCE | Unit price in GBP. Zero on giveaways, negative on bad-debt rows |
| `Customer ID` | float | SOURCE | 22.8% null |
| `Country` | text | SOURCE | Includes `Unspecified` and `European Community` buckets |

### Fields the source does **not** contain

`inventory_level`, `unit_cost`, `supplier`, `lead_time_days`, `category`,
`inventory_value`, `reorder_point`, `stockout_flag`.

Category is DERIVED. The remainder are SIMULATED — no public dataset pairs
real transaction-level demand with real inventory positions.

---

## 2. `data/processed/transactions_clean.parquet`

Line-level saleable demand. 1,005,780 rows.

| Column | Type | Provenance | Definition |
| --- | --- | --- | --- |
| `invoice` | text | SOURCE | Invoice number, whitespace-trimmed |
| `product_id` | text | DERIVED | `StockCode`, trimmed and upper-cased to merge case variants |
| `description` | text | DERIVED | Null values backfilled from the SKU's modal description |
| `quantity` | int | SOURCE | Units on the line (always positive in this table) |
| `invoice_datetime` | datetime | SOURCE | Original timestamp |
| `invoice_date` | date | DERIVED | `invoice_datetime` truncated to day |
| `unit_price` | float | SOURCE | GBP per unit |
| `customer_id` | float | SOURCE | Retained with nulls |
| `country` | text | SOURCE | |
| `revenue` | float | DERIVED | `quantity × unit_price`, zero when `is_revenue_line` is false |
| `is_revenue_line` | bool | DERIVED | False for zero-price giveaways and prices above the £500 ceiling |
| `is_bulk_order` | bool | DERIVED | Quantity above the 99.9th percentile (504 units). Retained, not deleted |
| `source_sheet` | text | SOURCE | Workbook sheet of origin |

## 3. `data/processed/returns.parquet`

Customer cancellations and returns (invoice prefix `C`). 17,914 rows.
Same schema; `quantity` is negative.

## 4. `data/processed/adjustments.parquet`

Warehouse write-offs — negative quantities *not* on a `C` invoice, carrying a
zero price and a free-text note ("lost", "damages", "wrongly coded",
"printing smudges/thrown away"). 3,399 rows.
**Excluded from the demand series**: these are stock corrections, not demand.

## 5. `data/processed/dim_product.parquet`

One row per product SKU. 4,773 rows, of which 2,370 enter the operational system.

| Column | Type | Provenance | Definition |
| --- | --- | --- | --- |
| `product_id` | text | DERIVED | Upper-cased stock code |
| `product_name` | text | DERIVED | Modal description across the SKU's transactions |
| `category` | category | DERIVED | Keyword ruleset over `product_name` (`src/categorization.py`). Audited at 93% on a 60-SKU random sample |
| `total_units` | int | DERIVED | Lifetime units sold |
| `total_revenue` | float | DERIVED | Lifetime revenue on qualifying revenue lines |
| `n_transactions` | int | DERIVED | Count of sales lines |
| `first_sale_date` / `last_sale_date` | date | DERIVED | Bounds of observed demand |
| `active_days` | int | DERIVED | Distinct days with a sale |
| `history_days` | int | DERIVED | `last_sale_date − first_sale_date` |
| `median_unit_price` | float | DERIVED | Median observed transaction price |
| `selling_price` | float | DERIVED | = `median_unit_price`. Median is used because this wholesaler runs multiple price tiers; the mean is dragged by outliers |
| `min/max/mean_unit_price` | float | DERIVED | Price spread diagnostics |
| `in_universe` | bool | DERIVED | Passes all SKU-universe filters (see below) |

### SKU universe filters

A SKU enters the operational early-warning system only if **all** hold:

| Filter | Threshold | Reason |
| --- | --- | --- |
| History span | ≥ 180 days | Need enough history to estimate demand variability |
| Active days | ≥ 24 days | A SKU sold on 3 days has no estimable demand distribution |
| Recency | sold within last 90 days | Discontinued SKUs should not generate reorder alerts |
| Volume | ≥ 100 lifetime units | Excludes negligible long-tail SKUs |
| Price | `selling_price > 0` | Cannot value risk without a price |

**Result: 2,370 SKUs — 49.7% of the catalogue but 86.1% of revenue.**

## 6. `data/processed/demand_daily.parquet`

The SKU × calendar-day demand panel. 2,370 SKUs × 739 days = 1,751,430 rows.
This is the forecasting input.

| Column | Type | Provenance | Definition |
| --- | --- | --- | --- |
| `product_id` | text | DERIVED | |
| `invoice_date` | date | DERIVED | Every calendar day in range, zero-filled |
| `gross_units` | int | DERIVED | Units on positive sales lines that day |
| `return_units` | int | DERIVED | Units cancelled/returned that day |
| `demand_units` | int | DERIVED | `max(gross_units − return_units, 0)` — **the forecasting target** |
| `revenue` | float | DERIVED | Revenue on qualifying lines |
| `n_orders` | int | DERIVED | Distinct invoices touching the SKU |
| `is_trading_day` | bool | DERIVED | Whether the business traded at all that day |

**Why calendar days, not trading days.** Supplier lead times are quoted in
calendar days, so demand statistics and lead-time arithmetic must share a
clock. The trade-off is that the 135 structural non-trading days inflate the
day-level coefficient of variation; `is_trading_day` is retained so the
statistics can be recomputed on trading days for sensitivity analysis.

**Intermittency.** 74.9% of SKU-days have zero demand; 1,651 of 2,370 SKUs
exceed 70% zero-days. This drives the model-selection strategy in
`src/forecasting.py` and is the reason Prophet was rejected.

---

## 7. Simulated supply layer

See `reports/ASSUMPTIONS.md` for the full derivation and parameter table.
Summary of provenance:

| Field | Provenance | Basis |
| --- | --- | --- |
| `unit_cost` | SIMULATED | `selling_price × (1 − gross_margin)`, margin set per category with SKU-level jitter |
| `supplier_id` / `supplier_name` | SIMULATED | 12 suppliers assigned by category affinity |
| `lead_time_days` | SIMULATED | Per-supplier mean (5–45 days) plus per-SKU jitter |
| `lead_time_sd_days` | SIMULATED | Per-supplier lead-time variability (1–8 days) |
| `inventory_level` | SIMULATED | Output of an incumbent (R,S) replenishment policy run against **real** observed demand |
| `units_fulfilled` / `lost_units` | SIMULATED | Consequence of the above policy meeting real demand |

The critical property: **inventory is not random**. It is a deterministic
function of real demand plus a stated policy, so historical stockouts emerge
from genuine demand volatility rather than injected noise.
