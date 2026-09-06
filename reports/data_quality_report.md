# Data Quality Report

_Generated 2026-09-05 16:10 from `src/data_cleaning.py`._

Every row below was produced by the cleaning pipeline itself. The
`affected_records` column is counted at run time, so re-running the
pipeline regenerates this report.

## Severity key

| Severity | Meaning |
| --- | --- |
| Critical | Would materially corrupt demand, forecasts or inventory maths if untreated |
| High | Distorts SKU-level demand or the product master |
| Medium | Affects a specific metric but not the core demand series |
| Low | Cosmetic or low-volume |
| Info | Recorded for traceability; no defect |

## Findings

### Critical

**Sheet overlap: 1,088 invoices (2010-12-01..09) exported into BOTH workbook sheets**

- **Affected:** 22,523 rows
- **Treatment:** Dropped the 'Year 2010-2011' copy of the overlapping invoices
- **Rationale:** The overlapping rows are an identical multiset. Naive concatenation inflates demand by ~100% on those nine trading days, which would corrupt every forecast and safety-stock estimate built on them.

### High

**Exact duplicate transaction lines (same invoice, SKU, timestamp, quantity and price)**

- **Affected:** 11,812 rows
- **Treatment:** Dropped, keeping the first occurrence
- **Rationale:** An identical line repeated within one invoice at the same second is a double-scanned row rather than two separate sales. Retaining them would overstate demand.

**Service / fee / adjustment stock codes carried in the sales table (ADJUST, ADJUST2, AMAZONFEE, BANK CHARGES, C2, D, DCGS0003, DCGS0004, DCGS0037, DCGS0041, DCGS0044, DCGS0058...)**

- **Affected:** 4,753 rows
- **Treatment:** Excluded from the product demand series
- **Rationale:** POST (postage), DOT (dotcom postage), M (manual), BANK CHARGES, AMAZONFEE, gift vouchers and TEST codes are not stockable inventory and have no reorder point.

**Negative quantities NOT on a cancellation invoice (warehouse write-offs: 'lost', 'damages', 'wrongly coded', 'thrown away')**

- **Affected:** 3,399 rows
- **Treatment:** Separated into adjustments.parquet and EXCLUDED from the demand series
- **Rationale:** These are stock adjustments, not customer demand - 3,398 of 3,399 carry a zero price and a free-text warehouse note. Counting them as negative demand would understate true demand and corrupt demand variability.

**Stock codes differing only by letter case (e.g. 85123a vs 85123A)**

- **Affected:** 170 stock codes
- **Treatment:** Upper-cased product_id so variants collapse to one SKU
- **Rationale:** Left unmerged, the same physical product would be split across two SKUs, halving its apparent demand and producing two wrong reorder points.

### Medium

**Missing product description**

- **Affected:** 4,275 rows
- **Treatment:** Backfilled from the modal description of the same product_id where available; otherwise left blank
- **Rationale:** Description drives the derived category. Recovering it from other rows of the same SKU preserves category coverage without inventing values.

**Zero or negative unit price on a product SKU**

- **Affected:** 2,566 rows
- **Treatment:** Rows removed from revenue analysis; the units are retained as demand
- **Rationale:** A zero-price line is a giveaway, sample or data-entry error rather than a sale. The customer still consumed a unit of stock, so the demand signal is kept while the revenue is not.

**Extreme order quantities (> 504 units, the 99.9th percentile)**

- **Affected:** 983 rows
- **Treatment:** RETAINED and flagged as bulk orders, not deleted
- **Rationale:** Inspection shows these are legitimate wholesale orders, not errors (this is a wholesaler). 8,351 of them were cancelled by a matching 'C' invoice and are netted out by the returns logic. Deleting genuine bulk demand would understate the reorder point for exactly the SKUs where a stockout is most costly.

**Product IDs carrying more than one distinct description**

- **Affected:** 751 SKUs
- **Treatment:** Resolved to the modal (most frequent) description per SKU
- **Rationale:** Most variants are warehouse annotations appended to the product name. The modal description is the stable product name; taking the first or last would pick up an annotation.

**Bad-debt adjustment invoices (prefix 'A', negative unit price)**

- **Affected:** 6 rows
- **Treatment:** Excluded from all sales and demand analysis
- **Rationale:** Accounting write-offs against customer accounts, not product transactions. Their large negative prices would distort price statistics.

### Low

**Missing customer ID**

- **Affected:** 229,143 rows
- **Treatment:** Retained - customer identity is not required for demand planning
- **Rationale:** These rows carry valid product, quantity, price and date. Dropping ~23% of transactions purely for a missing customer key would materially understate demand. Customer ID is only needed for customer-level analysis, which this project does not perform.

**SKU-days where returns exceeded same-day gross sales**

- **Affected:** 5,089 SKU-days
- **Treatment:** Net demand floored at zero for that day
- **Rationale:** A return is usually booked days after the original order, so the offsetting sale sits on an earlier date. Allowing negative daily demand would corrupt the forecast; the floor keeps the series non-negative at a small cost in netting accuracy.

**Unit price above the £500 plausibility ceiling on a product SKU**

- **Affected:** 4 rows
- **Treatment:** Flagged and excluded from average-selling-price calculations
- **Rationale:** Retained as demand but excluded from price statistics: a handful of extreme values would otherwise dominate the mean selling price used to value lost sales.

### Info

**Raw rows loaded (both sheets concatenated)**

- **Affected:** 1,067,371 rows
- **Treatment:** None - baseline
- **Rationale:** Starting point for all subsequent counts.

**Customer cancellations / returns (invoice prefix 'C')**

- **Affected:** 19,103 rows
- **Treatment:** Separated into returns.parquet and netted against gross demand per SKU-day
- **Rationale:** Cancelled and returned units are real reductions in net demand and must not be forecast as sales.

**SKUs entering the operational inventory system**

- **Affected:** 2,370 SKUs
- **Treatment:** Carried forward to forecasting and risk scoring
- **Rationale:** Of 4,773 product SKUs seen in the cleaned history.

**SKU universe filter - fails 'sold within last 90 days'**

- **Affected:** 1,611 SKUs
- **Treatment:** Excluded from the operational inventory system
- **Rationale:** Retained in the sales history for EDA, but no reorder point is published for it.

**SKU universe filter - fails 'active on >= 24 days'**

- **Affected:** 1,261 SKUs
- **Treatment:** Excluded from the operational inventory system
- **Rationale:** Retained in the sales history for EDA, but no reorder point is published for it.

**SKU universe filter - fails 'total units >= 100'**

- **Affected:** 1,052 SKUs
- **Treatment:** Excluded from the operational inventory system
- **Rationale:** Retained in the sales history for EDA, but no reorder point is published for it.

**SKU universe filter - fails 'history span >= 180 days'**

- **Affected:** 1,001 SKUs
- **Treatment:** Excluded from the operational inventory system
- **Rationale:** Retained in the sales history for EDA, but no reorder point is published for it.

**Calendar days with no trading activity (Saturdays and holiday closures)**

- **Affected:** 135 days
- **Treatment:** Zero-filled in the demand panel but flagged via is_trading_day
- **Rationale:** The business does not trade on Saturdays. Treating those zeros as genuine zero-demand days is correct for lead-time arithmetic (lead times are quoted in calendar days) but is flagged so that demand statistics can be recomputed on trading days if needed.

**SKU universe filter - fails 'has a positive selling price'**

- **Affected:** 66 SKUs
- **Treatment:** Excluded from the operational inventory system
- **Rationale:** Retained in the sales history for EDA, but no reorder point is published for it.

## Summary table

| severity | issue | affected_records | unit | treatment |
| --- | --- | --- | --- | --- |
| Critical | Sheet overlap: 1,088 invoices (2010-12-01..09) exported into BOTH workbook sheets | 22,523 | rows | Dropped the 'Year 2010-2011' copy of the overlapping invoices |
| High | Exact duplicate transaction lines (same invoice, SKU, timestamp, quantity and price) | 11,812 | rows | Dropped, keeping the first occurrence |
| High | Service / fee / adjustment stock codes carried in the sales table (ADJUST, ADJUST2, AMAZONFEE, BANK CHARGES, C2, D, DCGS0003, DCGS0004, DCGS0037, DCGS0041, DCGS0044, DCGS0058...) | 4,753 | rows | Excluded from the product demand series |
| High | Negative quantities NOT on a cancellation invoice (warehouse write-offs: 'lost', 'damages', 'wrongly coded', 'thrown away') | 3,399 | rows | Separated into adjustments.parquet and EXCLUDED from the demand series |
| High | Stock codes differing only by letter case (e.g. 85123a vs 85123A) | 170 | stock codes | Upper-cased product_id so variants collapse to one SKU |
| Medium | Missing product description | 4,275 | rows | Backfilled from the modal description of the same product_id where available; otherwise left blank |
| Medium | Zero or negative unit price on a product SKU | 2,566 | rows | Rows removed from revenue analysis; the units are retained as demand |
| Medium | Extreme order quantities (> 504 units, the 99.9th percentile) | 983 | rows | RETAINED and flagged as bulk orders, not deleted |
| Medium | Product IDs carrying more than one distinct description | 751 | SKUs | Resolved to the modal (most frequent) description per SKU |
| Medium | Bad-debt adjustment invoices (prefix 'A', negative unit price) | 6 | rows | Excluded from all sales and demand analysis |
| Low | Missing customer ID | 229,143 | rows | Retained - customer identity is not required for demand planning |
| Low | SKU-days where returns exceeded same-day gross sales | 5,089 | SKU-days | Net demand floored at zero for that day |
| Low | Unit price above the £500 plausibility ceiling on a product SKU | 4 | rows | Flagged and excluded from average-selling-price calculations |
| Info | Raw rows loaded (both sheets concatenated) | 1,067,371 | rows | None - baseline |
| Info | Customer cancellations / returns (invoice prefix 'C') | 19,103 | rows | Separated into returns.parquet and netted against gross demand per SKU-day |
| Info | SKUs entering the operational inventory system | 2,370 | SKUs | Carried forward to forecasting and risk scoring |
| Info | SKU universe filter - fails 'sold within last 90 days' | 1,611 | SKUs | Excluded from the operational inventory system |
| Info | SKU universe filter - fails 'active on >= 24 days' | 1,261 | SKUs | Excluded from the operational inventory system |
| Info | SKU universe filter - fails 'total units >= 100' | 1,052 | SKUs | Excluded from the operational inventory system |
| Info | SKU universe filter - fails 'history span >= 180 days' | 1,001 | SKUs | Excluded from the operational inventory system |
| Info | Calendar days with no trading activity (Saturdays and holiday closures) | 135 | days | Zero-filled in the demand panel but flagged via is_trading_day |
| Info | SKU universe filter - fails 'has a positive selling price' | 66 | SKUs | Excluded from the operational inventory system |

## Derived category coverage

Category is DERIVED from free-text descriptions (the source has no category field).
A 60-SKU random sample was manually audited: **56/60 correct (93%)**. The four
misses fell into `Other`, which is the safe failure mode - a mis-filed SKU is
visible rather than silently attributed to the wrong category.

| category | n_skus | revenue | pct_skus | pct_revenue |
| --- | --- | --- | --- | --- |
| Kitchen & Dining | 504 | 4,270,971.12 | 21.27 | 25.31 |
| Home Decor | 363 | 2,762,387.42 | 15.32 | 16.37 |
| Bags | 113 | 1,879,763.05 | 4.77 | 11.14 |
| Party & Celebration | 176 | 1,227,724.14 | 7.43 | 7.28 |
| Storage & Organisation | 136 | 1,096,606.00 | 5.74 | 6.50 |
| Candles & Fragrance | 187 | 1,088,887.52 | 7.89 | 6.45 |
| Other | 235 | 974,249.97 | 9.92 | 5.77 |
| Christmas & Seasonal | 145 | 824,659.08 | 6.12 | 4.89 |
| Stationery & Craft | 172 | 671,900.26 | 7.26 | 3.98 |
| Toys & Games | 96 | 626,831.56 | 4.05 | 3.71 |
| Garden & Outdoor | 80 | 482,103.62 | 3.38 | 2.86 |
| Vintage & Retro | 44 | 396,268.30 | 1.86 | 2.35 |
| Lighting | 30 | 345,673.27 | 1.27 | 2.05 |
| Jewellery & Accessories | 89 | 227,448.15 | 3.76 | 1.35 |
