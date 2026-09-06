# Portfolio Kit

Résumé lines, interview scripts and Q&A for this project.

**Every number below is traceable to a pipeline output.** Nothing is rounded up
for effect. Where a figure rests on the simulated supply layer it is labelled,
because an interviewer who catches you overstating one number will discount
everything else you say.

---

## 1 · Résumé project description (2–3 sentences)

> Built an end-to-end inventory stockout and overstock early-warning system over
> 1.07M retail transactions, combining SQL analytics, SKU-level demand
> forecasting and inventory theory (safety stock, reorder point) to classify
> 2,370 SKUs into five risk tiers with quantified financial impact. Benchmarked
> ten forecasting models with rolling-origin validation and delivered a
> prioritised action list plus a Power BI dashboard covering executive, stockout,
> overstock and forecast views.

---

## 2 · Résumé bullets (quantified, from actual results)

> - Identified **£979K of excess inventory across 950 SKUs (51% of inventory
>   value)** and isolated **265 dead-stock SKUs worth £242K** by building a
>   SKU-level demand forecasting and inventory-risk model over 1.07M
>   transactions, producing a prioritised action list of 939 SKUs.

> - Improved forecast accuracy to a **median MASE of 0.883 across 2,370 SKUs**
>   by benchmarking 10 models with 5-fold rolling-origin validation, routing 70%
>   of SKUs with intermittent demand to Croston/SBA methods and **rejecting
>   SARIMA on evidence** after it lost to a 28-day moving average at ~50× the
>   compute cost.

**Optional third bullet — the data-quality catch:**

> - Detected and corrected a **100% demand inflation across nine trading days**
>   caused by an undocumented overlap between two source worksheets, preventing
>   systematic bias in all downstream forecasts and safety-stock calculations.

---

## 3 · GitHub README summary (the one-paragraph hook)

> An end-to-end analytics system that tells a retail business which SKUs to
> reorder, which to mark down, and what each decision is worth. Built on 1.07M
> real transactions from a UK giftware wholesaler: SQL analytics in DuckDB,
> SKU-level demand forecasting with rolling-origin backtesting across 10 models,
> safety-stock and reorder-point calculation accounting for both demand and
> lead-time variability, a five-tier risk classification with transparent
> prioritisation scoring, and a Power BI dashboard. Demand data is real;
> inventory, cost, supplier and lead-time layers are simulated from documented
> assumptions and clearly labelled throughout.

---

## 4 · 60-second interview explanation

> Retailers fail at inventory in two directions at once — they stock out and lose
> sales, and they overstock and trap cash — and most systems only watch one side.
> I built an early-warning system that watches both.
>
> It runs on 1.07 million real transactions from a UK giftware wholesaler.
> I clean the data, forecast demand for every SKU, calculate a proper safety
> stock and reorder point, then classify all 2,370 active SKUs into five risk
> tiers and attach a pound value to each one.
>
> The output is an action list: 56 SKUs at critical stockout risk with specific
> reorder quantities, and £979,000 of excess inventory — just over half of
> inventory value — with 265 SKUs identified as genuinely dead stock that needs
> liquidating rather than reordering.
>
> The finding I like most is that demand *volatility* damages service more than
> supplier *lead time* does. Fill rate falls from 95% to 75% across volatility
> quartiles but only from 90% to 83% across sourcing regions. That changes the
> recommendation — you fix this by sizing safety stock to volatility, not by
> renegotiating lead times.
>
> One caveat I'd flag upfront: the demand data is real, but no public dataset
> pairs real demand with real inventory positions, so the inventory layer is
> simulated from documented assumptions. I reconstruct it by running a stated
> replenishment policy against the real demand — so the stockouts come from
> genuine demand volatility, not random noise.

---

## 5 · 3-minute interview explanation

**The problem (20s)**

> Inventory fails in two directions. Stock out and you lose the sale and
> sometimes the customer. Overstock and you've turned cash into boxes that cost
> money to store and eventually get marked down. A planner reviewing 2,370 SKUs
> a week can't hold both risks in their head. I built the system that does.

**The data and the honest caveat (30s)**

> I used UCI's Online Retail II — 1.07 million real transactions from a UK
> giftware wholesaler over two years. It has real demand but no inventory
> levels, no unit cost, no supplier and no lead time.
>
> Rather than fabricate inventory at random, I reconstructed the inventory ledger
> by running a documented periodic-review replenishment policy against the real
> demand series. So inventory is a deterministic function of real demand plus a
> stated policy. A stockout in my system means "this ordering rule couldn't cover
> the demand that genuinely occurred" — not "a random number was small." And
> because the policy is deliberately the *legacy* one — no safety stock — the
> project's job is to quantify where it fails.

**Data quality — the catch (30s)**

> The first thing I found was that the workbook's two sheets overlap on nine days
> in December 2010. The overlapping rows are an identical multiset. Concatenating
> them naively — which most published analyses of this dataset do — doubles
> demand for those nine days. That would corrupt every trailing average and
> safety-stock number that touches that period.
>
> I also separated two kinds of negative quantity that look alike: customer
> cancellations, which reduce net demand, and warehouse write-offs — rows priced
> at zero with notes like "damages" or "wrongly coded" — which are stock
> adjustments and shouldn't touch the demand series at all.

**Forecasting (45s)**

> The key fact is that 75% of SKU-days have zero demand. That drove everything.
>
> I rejected Prophet — not because it's bad, but because it models smooth trend
> and seasonality, and fitted to a series that's zero four days in five it
> produces smooth drift that's neither accurate nor interpretable.
>
> I compared ten models with rolling-origin backtesting, five folds of thirty
> days, never a random split. Intermittent SKUs went to Croston and SBA; dense
> ones to ETS. I benchmarked SARIMA on the hundred highest-revenue SKUs and it
> *lost* to a 28-day moving average at about fifty times the fitting cost, so I
> didn't roll it out. Median MASE came out at 0.883.
>
> I also selected on error in the thirty-day *total*, not the daily path —
> because a reorder quantity is built from a total, not a sequence.

**Inventory maths (30s)**

> Safety stock uses the combined form — demand variability *and* lead-time
> variability — because supplier lead-time standard deviation runs from 1 to 8
> days. That adds 11% to safety stock for Far-East SKUs versus 3% for UK
> domestic, so it protects exactly the SKUs most exposed. The protection interval
> is lead time *plus* review period, and every comparison uses inventory position
> including stock on order, which avoids double-ordering.

**Result and the limitation (35s)**

> £979,000 of excess inventory across 950 SKUs, 265 dead-stock SKUs worth £242k,
> 56 SKUs at critical stockout risk with specific order quantities, all ranked by
> a transparent weighted score that decomposes back into its components.
>
> The limitation I'd raise before you do: my as-of date sits on the Christmas
> peak, and the forecast window is the post-Christmas collapse. Every backtest
> fold falls in the ramp-up, so model selection has no evidence about post-peak
> behaviour — and with only two years of data I can't validate a seasonal model
> on that regime, because I'd need one year to learn the season and another to
> test on. So instead of silently trusting one number, I publish both scenarios.
> The gap between them is the real uncertainty.

---

## 6 · Ten likely interview questions

1. Your inventory data is simulated. Why should I trust any of this?
2. Why didn't you use Prophet? / Why not deep learning?
3. Walk me through your safety stock formula and its assumptions.
4. How did you validate the forecasts? How do you know there's no leakage?
5. Your sMAPE is over 150%. Isn't that a terrible forecast?
6. How did you decide what counts as "overstock"? The 90-day threshold looks arbitrary.
7. Explain your priority score. How do I know it isn't arbitrary weights?
8. Your estimated lost revenue is £63k but excess inventory is £979k. Isn't the stockout side trivial?
9. What would you do differently with real data?
10. How would you productionise this?

---

## 7 · Strong answers

**Q1 · Your inventory data is simulated. Why should I trust any of this?**

> You should trust the demand analysis completely and treat the inventory layer
> as a labelled assumption — that's exactly how I've documented it.
>
> No public dataset pairs real transaction-level demand with real inventory
> positions. The well-known "retail inventory" datasets on Kaggle are themselves
> synthetic, so using one would have traded real demand signal for fabricated
> demand signal — strictly worse.
>
> What I did instead matters: I didn't sample inventory randomly. I ran a
> documented periodic-review policy against the real demand series, so inventory
> is a deterministic function of real demand plus a stated policy. The proof that
> it behaves like real inventory is that fill rate degrades with lead time and
> with demand volatility — the two terms in the safety-stock formula — and I
> never told the simulation to do that. It emerges.
>
> And the system is decoupled: swap in a real inventory feed and every downstream
> module runs unchanged. The simulation is scaffolding, not the product.

**Q2 · Why didn't you use Prophet?**

> Because the data doesn't support it. 75% of SKU-days have zero demand, and
> 1,651 of 2,370 SKUs are zero on more than 70% of days.
>
> Prophet decomposes a series into smooth trend plus smooth seasonality. That's a
> good model for dense business-daily data with strong calendar structure. Fitted
> to a series that's zero four days in five, it produces smooth non-negative
> drift that neither matches the spiky reality nor tells you anything
> interpretable — and it costs 1–2 seconds per SKU, so about 40–80 minutes per
> backtest fold set.
>
> The right family for intermittent demand is Croston and its bias-corrected
> variant SBA, which model demand *size* and demand *interval* separately instead
> of pretending the zeros are observations of a smooth process. That's what I
> used for the intermittent SKUs.
>
> I'd add that I didn't just assert this — I benchmarked. And the honest result
> was that a 28-day moving average edged out Croston on 30-day totals for
> intermittent SKUs, which I report rather than hide.

**Q3 · Walk me through your safety stock formula.**

> `SS = z · √(PI · σ_d² + d̄² · σ_LT²)`
>
> Three deliberate choices. First, the protection interval is lead time *plus*
> review period, not lead time alone — under periodic review you can't reorder
> until the next review, so your exposure runs to the arrival of the *next*
> order. Skipping that under-protects by the length of a review cycle.
>
> Second, I include the lead-time variability term. The textbook `z·σ_d·√LT`
> assumes deterministic lead time; mine isn't — supplier lead-time SD runs 1 to 8
> days. Including it adds 11% to safety stock for Far-East SKUs versus 3% for UK
> domestic, so it protects precisely the SKUs most exposed.
>
> Third, `d̄` comes from the forecast, not a trailing average — the point of
> building a forecasting layer is to use it — while `σ_d` comes from the trailing
> 90 days, because the forecast gives me a level but not a dispersion.
>
> The assumptions: normally-distributed demand and lead time, and independence
> between them. Neither is exactly true for intermittent demand — the normal
> approximation is poor when demand is mostly zeros — which is why I'd move to
> quantiles of an empirical or Poisson-gamma demand distribution with real data.
> I verified the implementation by hand-calculating one SKU: 337.10 by hand,
> 337.00 from the module.

**Q4 · How did you validate the forecasts?**

> Rolling-origin backtesting — five folds, each a 30-day horizon, walking
> forward. Fold *k* trains on `demand[:origin]` and predicts the next 30 days.
> Nothing at or after the origin is visible.
>
> I never randomly split. Random splitting on time series lets the model see the
> future and train on it, which inflates accuracy and is the single most common
> way forecasting projects fool themselves.
>
> On leakage specifically: the inventory simulation has the same discipline — at
> every weekly review the order quantity uses only a strictly trailing 56-day
> demand window. The one exception is the opening stock position at t=0, which is
> a simulation initial condition, so I exclude the first 90 days from every
> reported statistic.
>
> I also started with 3 folds and moved to 5, because with 10 candidate models
> and only 3 folds, model selection was overfitting the validation set — median
> MASE actually got *worse* when I added candidates. Five folds fixed it.

**Q5 · Your sMAPE is over 150%. Isn't that terrible?**

> It looks terrible and I report it anyway, but it's the wrong metric here and
> that's the point of showing it.
>
> When actual demand is zero and you forecast anything non-zero, sMAPE scores
> 200% by construction. With 75% zero days, the median is dominated by that
> mechanic, not by forecast quality. MAPE is worse — it's undefined, dividing by
> zero.
>
> The metric I select on is MASE, which scales error by the in-sample naive
> forecast's error. It's defined when actuals are zero and comparable across SKUs
> of wildly different volume. Median MASE is 0.883, and 56% of SKUs beat a naive
> forecast — meaning for a bit over half the catalogue the model adds real value
> over "assume tomorrow looks like today."
>
> And for the actual decision, I select on error in the 30-day *total*, because
> that's what a reorder quantity is built from. A model can have poor day-to-day
> accuracy and still be right for inventory if its horizon total is unbiased.

**Q6 · The 90-day overstock threshold looks arbitrary.**

> It would be if I'd picked it and moved on. I picked it, tested it, and
> published the sensitivity.
>
> Here's the reasoning: this business turns inventory 2.2 times a year — 165 days
> of stock. If I judge excess against a 30-day requirement, I flag 78% of SKUs.
> That's not an alert list, that's noise. The horizon has to reflect the
> business's actual operating cycle.
>
> At 90 days with a 1.5× ratio threshold I flag 40% of SKUs and £979k. I ran the
> full grid — 30/60/90 days crossed with 1.5/2/3× — and shipped it as a CSV in
> the repo, so anyone can see how the headline number moves with the choice. At
> 90 days and 3× it's £787k; at 30 days and 1.5× it's £1.55M.
>
> With a real client I'd set this from their actual markdown policy and holding
> cost rather than from turnover — the right threshold is where holding cost
> exceeds expected markdown loss.

**Q7 · Explain your priority score.**

> It's a weighted sum of five percentile-ranked components, and every weight is
> declared in a YAML config file, not buried in code.
>
> For stockout risk: financial exposure 40%, severity 25%, demand velocity 15%,
> lead-time pressure 10%, revenue contribution 10%.
>
> Two design choices worth defending. I use percentile ranks rather than raw
> values because the distributions are extremely skewed — one SKU with £50k of
> exposure would otherwise dominate the score for everything else. And I keep
> every component's contribution as its own column, so any score decomposes back
> into its parts. My top stockout SKU scores 91.52, and I can show you that's
> 40.00 financial + 25.00 severity + 15.00 velocity + 9.89 revenue + 1.63 lead
> time. It sums exactly.
>
> The weights themselves are a business judgement, not a fitted parameter — and
> that's the honest framing. I'd tune them with the planning team. What I won't
> do is fit them to something and pretend that makes them objective, because
> there's no ground-truth label for "correct priority."

**Q8 · £63k of lost revenue vs £979k of excess. Isn't the stockout side trivial?**

> Right now, yes — and that's a real seasonal finding, not a modelling artefact.
>
> The as-of date is 9 December. The business has just stocked up for Christmas.
> So almost nothing is short and almost everything is deep. If I ran the same
> system in September, the balance would invert.
>
> Two caveats though. First, £63k is a 30-day forward estimate against £979k of
> standing capital — different time bases, so the raw comparison flatters the
> overstock side. Annualised, the historical service failure was £1.61M of lost
> revenue over the reconstructed period at a 90.6% fill rate.
>
> Second, the two aren't symmetric in consequence. Excess inventory is
> recoverable — you mark it down and take a margin hit. A stockout on an A-class
> SKU can cost the customer relationship, which doesn't show up in either number.
> That's an argument for differentiating service level by ABC class, which is my
> fifth recommendation.

**Q9 · What would you do differently with real data?**

> Four things.
>
> The simulated layer disappears — real inventory snapshots, real POs with
> promise dates and actual receipt dates. That last one is the big unlock:
> I could learn each supplier's *actual* lead-time distribution instead of
> assuming normality, and model reliability per supplier.
>
> I'd get promotion and pricing data. Right now every promotional spike is
> modelled as random variability, which inflates safety stock for promoted SKUs —
> the system is paying for uncertainty that was actually a planned campaign.
>
> I'd revisit the lost-sales assumption. I assume unmet demand is lost. In a
> business with backorders or substitution that's wrong, and the substitution
> case is measurable — you can see demand shift to the sibling SKU.
>
> And I'd validate against realised outcomes. Right now I can tell you the
> forecast's MASE; I can't tell you whether acting on the recommendations
> improved service, because there's no feedback loop. I'd want to run the
> recommendation list as an A/B against the incumbent policy.

**Q10 · How would you productionise this?**

> The structure is already most of the way there — modular `src/` package,
> YAML-driven config, seeded reproducibility, a single `run_pipeline.py`
> entrypoint, and SQL assertions that fail loudly if an invariant breaks.
>
> To productionise: schedule it weekly to match the review cycle, on Airflow or
> similar. Swap DuckDB for the warehouse — the SQL is standard enough to port.
> Add data-quality gates that halt the run rather than warn, since a silent bad
> forecast is worse than no forecast.
>
> On the modelling side I'd add forecast monitoring — track realised MASE per SKU
> and alert when a model degrades, then re-run selection on a schedule rather
> than once. Model selection isn't a one-time decision.
>
> Delivery-wise, the dashboard is for the weekly review, but the P1 list should
> be pushed — email or Slack to the planning team — because an alert nobody opens
> isn't an alert. And I'd log every recommendation and whether it was actioned,
> which gives you the feedback loop for question 9.

---

## 8 · Technical concepts to be ready to explain

**Inventory management**
- Safety stock; service level vs. fill rate (they are not the same); cycle
  service level vs. unit fill rate
- Reorder point, order-up-to level, protection interval
- Periodic review (R,S) vs. continuous review (s,Q)
- Inventory position (on-hand + on-order) vs. on-hand — and why using on-hand
  causes double-ordering
- Inventory turnover, days of inventory outstanding, why COGS (not revenue) goes
  in the numerator
- ABC classification and differentiated service levels
- Bullwhip effect; lead-time variability vs. demand variability
- Dead stock vs. overstock — different diagnosis, different action

**Forecasting**
- Intermittent demand; Croston's method and the SBA bias correction
- Exponential smoothing / Holt-Winters; ARIMA and SARIMA; why a constant term
  matters (without `trend="c"` a SARIMA forecast decays to zero)
- Rolling-origin / walk-forward validation; why random splits leak
- MAE, RMSE, MAPE, sMAPE, MASE — and when each breaks
- Forecasting the horizon *total* vs. the daily path
- Seasonal decomposition; ratio-to-moving-average seasonal indices
- Bias vs. variance in a forecast; over-forecasting a trough vs. under-forecasting a peak

**Statistics**
- Coefficient of variation as a scale-free volatility measure
- Normal approximation and z-scores; why it is weak for intermittent demand
- Percentile ranking for skewed distributions
- Empirical prediction intervals from backtest residuals vs. model-implied CIs

**Data & engineering**
- Star schema, fact vs. dimension, declaring grain
- Window functions; gaps-and-islands for consecutive-run detection
- Slowly changing dimensions (relevant if productionised)
- Idempotent, seeded pipelines; config-driven thresholds
- Invariant assertions as data tests

**Visualisation**
- Why dual-axis charts are misleading
- Colour-vision-deficiency safe palettes; why colour must never carry meaning alone

---

## 9 · Assumptions an interviewer will challenge

Be the first to raise these. Volunteering a limitation reads as rigour;
being caught omitting one reads as either sloppiness or spin.

| # | Assumption | The challenge | Your response |
| --- | --- | --- | --- |
| 1 | Inventory, cost, supplier, lead time are simulated | "So none of this is real" | Demand is real; inventory is a deterministic function of real demand plus a stated policy. The system logic is what's demonstrated and it runs unchanged on a real feed |
| 2 | Observed transactions = demand | "If they stocked out, you never saw that demand" | Correct, and it biases every reorder point *low*. Unobservable without a stockout flag. With real data I'd censor-correct |
| 3 | Unmet demand is lost, not backordered | "Wholesale customers often wait" | It overstates lost revenue. It's an upper bound and labelled as an estimate |
| 4 | Lost revenue at median selling price with no substitution | "Some of that demand moves to another SKU" | Agreed — this is an upper bound. Substitution is measurable with real data |
| 5 | Category derived from text at 93% accuracy | "So category conclusions are 7% wrong" | Roughly, yes. Measured on a 60-SKU hand audit rather than assumed. Misses land in "Other", the safe failure mode |
| 6 | 95% service level applied uniformly | "Nobody runs one service level across the catalogue" | Correct, and it's my own recommendation #5. It's a one-line config change |
| 7 | 90-day overstock horizon | "Arbitrary" | Set from the 2.2× turnover; full sensitivity grid published |
| 8 | Normal demand distribution in the safety-stock formula | "Your demand is 75% zeros, that's not normal" | The sharpest technical challenge here. It's the standard approximation and it's weak for intermittent SKUs. The fix is quantiles of an empirical or Poisson-gamma distribution |
| 9 | Calendar-day demand statistics | "Saturdays inflate your variance" | Yes, slightly — makes safety stock marginally conservative. Chosen so demand stats and lead times share one clock. `is_trading_day` is retained for sensitivity |
| 10 | Forecast can't see the post-Christmas regime | "Your headline forecast is 69% above the seasonal scenario" | Known and published as two scenarios. Two years of data can't validate a seasonal model on a regime it was never tested on |
| 11 | Priority-score weights are judgement | "You made those up" | Yes — they're business judgement, declared in config, and fully decomposable. There's no ground-truth label to fit them to, and pretending otherwise would be worse |
| 12 | Data is 2009–2011 | "This is 15 years old" | Methods transfer; figures are historical. It's one of the few large public retail transaction datasets with genuine data-quality problems worth solving |
