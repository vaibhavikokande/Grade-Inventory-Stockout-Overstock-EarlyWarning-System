# Assumptions & Simulated Data Layer

> **Read this before interpreting any inventory number in this project.**
>
> Demand is **real**. Inventory, cost, supplier and lead time are **simulated**
> from the documented parameters below. Every chart, KPI and recommendation
> that depends on the simulated layer is labelled as an estimate.

---

## 1. Why a simulated supply layer exists

The business question — *which SKUs will stock out, which are overstocked, and
what does that cost?* — requires four fields that the source data does not
contain:

| Required field | In Online Retail II? |
| --- | --- |
| Daily inventory position | No |
| Unit cost | No |
| Supplier / vendor | No |
| Supplier lead time | No |

No public dataset pairs **real transaction-level demand** with **real inventory
positions**. The well-known "retail inventory" datasets on Kaggle are
themselves synthetic, and using one would trade real demand signal for
fabricated demand signal — a strictly worse deal.

The approach taken here keeps the real signal where it matters most:

```
REAL      Online Retail II transactions  ->  daily demand per SKU
                                              |
SIMULATED  cost / supplier / lead time  ------+
                                              |
DERIVED                                       v
           incumbent (R,S) replenishment policy run against REAL demand
                                              |
                                              v
                       daily inventory ledger, stockouts, lost sales
```

**The key property:** inventory is not sampled at random. It is a deterministic
function of real demand plus a stated policy. A stockout in this project means
*"the stated ordering rule could not cover the demand that genuinely occurred
on that date"* — not *"a random number generator produced a small number."*

**How this generalises.** Swap `data/processed/inventory_daily.parquet` for a
real inventory feed and every downstream module — safety stock, reorder point,
risk classification, financial impact, scoring, recommendations — runs
unchanged. The simulation is scaffolding, not the product.

---

## 2. Unit cost

```
unit_cost = selling_price x (1 - gross_margin)
```

`selling_price` is the **median observed transaction price** per SKU (robust to
this wholesaler's multiple price tiers and promotional prices).

`gross_margin` is drawn per SKU as `N(category_mean, 0.03)` clipped to
[0.22, 0.62]. Category means:

| Category | Gross margin | Category | Gross margin |
| --- | --- | --- | --- |
| Jewellery & Accessories | 48% | Vintage & Retro | 44% |
| Candles & Fragrance | 46% | Toys & Games | 43% |
| Christmas & Seasonal | 45% | Bags | 42% |
| Party & Celebration | 45% | Other | 42% |
| Home Decor | 44% | Stationery & Craft | 41% |
| Kitchen & Dining | 40% | Lighting | 40% |
| Storage & Organisation | 39% | Garden & Outdoor | 38% |

**Basis.** UK giftware/homeware wholesale gross margins typically sit in the
35–50% band. Higher-margin categories are those with more design/brand content
(jewellery, fragrance, seasonal); lower-margin categories are more commoditised
(garden, storage).

**Realised blended gross margin across the SKU universe: 41.8%.**

**What this affects.** Excess inventory value and working capital tied up scale
*directly* with unit cost. A 5-percentage-point margin error moves excess
inventory value by roughly 8–9%. Rank ordering of overstocked SKUs is largely
insensitive to this, because the margin varies little relative to the variation
in excess units.

---

## 3. Suppliers

12 synthetic suppliers across 3 sourcing regions:

| Region | Suppliers | Lead-time mean range | Lead-time SD range |
| --- | --- | --- | --- |
| UK Domestic | 4 | 5–12 days | 1.0–2.5 days |
| EU Import | 4 | 12–25 days | 2.0–4.5 days |
| Far East Import | 4 | 28–45 days | 5.0–8.0 days |

**Basis.** Typical UK giftware sourcing: domestic restock in 1–2 weeks,
European suppliers in 2–4 weeks, Far-East container freight in 4–7 weeks with
materially higher variability.

**Assignment.** Suppliers specialise by category — each category has a primary
and a secondary supplier, and SKUs fall to the primary 70% of the time. This is
deliberate: uniform random assignment would make "which supplier carries the
most risk" a meaningless question, since every supplier would look identical.

**Realised distribution:** 759 SKUs UK Domestic, 739 EU Import, 872 Far East
Import.

## 4. Lead time

```
lead_time_days = supplier_mean + Uniform{-3..+3}, floored at 1
lead_time_sd_days = supplier SD
```

Realised across the universe: median 22 days, 5th percentile 4 days, 95th
percentile 42 days.

At order time the *actual* realised lead time is drawn
`N(lead_time_days, lead_time_sd_days)`, rounded, floored at 1. This is what
creates late shipments — and therefore stockouts the planner did not expect.

---

## 5. The incumbent replenishment policy

The reconstructed history assumes the business currently runs a **periodic
review, order-up-to (R, S)** policy:

| Parameter | Value | Meaning |
| --- | --- | --- |
| `review_period_days` | 7 | Planner reviews stock weekly |
| `cover_weeks` | 8 | Orders up to 8 weeks of cover |
| `trailing_demand_window` | 56 days | Window for the demand average |
| Lead time used | Supplier's **quoted mean** | No buffer for variability |
| Safety stock | **None** | The core weakness |
| `initial_cover_weeks` | 8 | Opening position at t=0 |
| `warmup_days` | 90 | Burn-in excluded from all statistics |

At each review:

```
avg          = mean(demand[t-56 : t])        # strictly trailing - no lookahead
target       = avg x 8 x 7
position     = on_hand + on_order - avg x quoted_lead_time
order_qty    = max(0, target - position)
```

**Why this policy and not an optimal one.** The project's purpose is to find
where the current way of working fails. Simulating an already-optimal policy
would produce a system with nothing to report. This policy is *right on
average* and wrong precisely when demand spikes or a shipment runs late —
which is the realistic legacy failure mode, and exactly what safety stock
exists to absorb.

**No lookahead.** Every ordering decision uses `demand[t-56 : t]`. Nothing at
or after day `t` is visible. The single exception is the opening stock position
at t=0, which is a simulation initial condition; the first 90 days are
therefore excluded from every reported statistic.

### Realised behaviour (post-warm-up, 2010-03-01 → 2011-12-09)

| Metric | Value |
| --- | --- |
| Unit fill rate | **90.6%** |
| Unit fill rate excluding extreme demand spikes (>760 units/SKU-day, 0.03% of rows) | 92.4% |
| Total demand | 8,755,394 units |
| Unmet demand | 823,924 units |
| Estimated lost revenue | **£1.61M** |
| SKU-days at zero stock | 13.9% |
| SKU-days stocked out *with demand present* | 1.4% |
| Inventory turnover (last 365 days) | 2.21x (165 days of inventory) |
| SKUs never stocking out | 559 of 2,370 |
| SKUs below 95% fill rate | 1,434 of 2,370 |
| SKUs below 80% fill rate | 625 of 2,370 |

### Validation: the policy fails where inventory theory says it must

| Sourcing region | SKUs | Mean fill rate | Median lead time | Lost units |
| --- | --- | --- | --- | --- |
| UK Domestic | 759 | 89.6% | 8 days | 215,153 |
| EU Import | 739 | 87.4% | 21 days | 234,733 |
| Far East Import | 872 | 83.1% | 38 days | 374,038 |

Correlation between lead time and fill rate: **−0.19**.

| Demand-volatility quartile | SKUs | Mean fill rate |
| --- | --- | --- |
| Q1 (most stable) | 593 | 94.8% |
| Q2 | 592 | 89.4% |
| Q3 | 592 | 86.7% |
| Q4 (most volatile) | 593 | 75.3% |

Service degrades monotonically with both lead time and demand variability —
the two terms in the safety-stock formula. This is not something the simulation
was told to do; it emerges from running the policy against real demand, and it
is the empirical justification for the safety-stock model in
`src/inventory_metrics.py`.

Note that **demand variability is the stronger driver** (94.8% → 75.3% across
volatility quartiles) than lead time (89.6% → 83.1% across regions). That is
expected: the incumbent policy already covers the supplier's quoted lead time,
so lead time only hurts through its *variability* and through the longer
exposure window, whereas demand variability is completely unbuffered. It is
also the practical finding — the largest service gains come from sizing safety
stock to demand volatility, not from chasing shorter lead times.

**Lost demand is broad-based, not an artifact.** The single largest
lost-demand event accounts for 1.4% of unmet units, and the top 100 SKU-days
account for 26%. The problem is systemic across the catalogue rather than
driven by a handful of freak orders.

---

## 6. Analytical assumptions (independent of the simulation)

| # | Assumption | Rationale | Risk if wrong |
| --- | --- | --- | --- |
| 1 | Observed transactions represent **demand**, not constrained sales | The source has no stockout flag, so unmet demand is unobservable in the raw data | If the real business stocked out historically, true demand is understated and all reorder points are biased low |
| 2 | Unmet demand is **lost**, not backordered | Consistent with an online giftware wholesaler where customers substitute | Overstates lost revenue if customers actually wait |
| 3 | Lost revenue is an **upper-bound estimate** | It assumes every unmet unit would have converted at the median price | Real recovery is lower; some customers substitute onto other SKUs in the catalogue |
| 4 | Demand statistics computed on **calendar days** | Lead times are quoted in calendar days, so both must share a clock | The 135 structural non-trading days inflate day-level CV, which makes safety stock slightly conservative |
| 5 | Category is **derived from description text** | No category field exists | Measured 93% accurate on a 60-SKU audit; category-level conclusions carry roughly that error rate |
| 6 | Returns reduce demand on the day they are **booked** | Return dates are known, original order dates are not linkable | 5,089 SKU-days needed a floor at zero |
| 7 | No promotion, price-elasticity or marketing data exists | Not in the source | Demand spikes caused by promotions are modelled as random variability, inflating safety stock for promoted SKUs |
| 8 | Service level target of **95%** applies uniformly | A single documented starting point | Real businesses differentiate by ABC class; `config/config.yaml` makes this a one-line change |

---

## 7. Reproducibility

Every random draw is seeded from `project.random_seed` (42) in
`config/config.yaml`:

| Component | Seed |
| --- | --- |
| Supplier master | `seed` |
| Cost, margin, supplier assignment, lead time | `seed + 1` |
| Per-SKU lead-time realisations | `seed + 1000 + sku_index` |

Re-running `python -m src.supply_simulation` reproduces the ledger byte for
byte. Changing the seed changes the simulated layer but not the real demand
series, and the qualitative conclusions (service falls with lead time and
volatility) are seed-independent.
