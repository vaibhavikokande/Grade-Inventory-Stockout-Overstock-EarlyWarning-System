"""Risk classification, financial impact and the prioritisation score.

CLASSIFICATION
--------------
Five mutually exclusive levels, driven by two scale-free ratios so that a
£0.40 sticker and a £12 cakestand are judged on the same footing::

    cover_ratio  = inventory_position / reorder_point
    excess_ratio = inventory_position / expected_requirement_90d

| Level                    | Rule                    |
|--------------------------|-------------------------|
| Critical Stockout Risk   | cover_ratio  < 0.50     |
| Stockout Risk            | cover_ratio  < 1.00     |
| Severe Overstock         | excess_ratio > 3.00     |
| Overstock Risk           | excess_ratio > 1.50     |
| Healthy                  | everything else         |

Stockout tests run first: a SKU cannot be simultaneously below its reorder
point and overstocked, but if the ratios ever disagreed, running out of stock
is the more urgent condition.

MATERIALITY
-----------
A ratio breach alone does not create an action. A SKU flagged as overstocked
with £4 of excess stock is noise. Each risk carries a materiality floor
(``min_excess_value_gbp``, ``min_lost_revenue_gbp``); SKUs below it keep their
risk level for reporting but are marked ``is_actionable = False`` and are
excluded from the action list. This keeps the alert list short enough to be
used, and the rule is explicit rather than hidden in a filter.

PRIORITISATION SCORE
--------------------
A transparent weighted sum, 0-100, NOT a black box. Every component is
converted to a percentile rank (0-1) *within its risk group* and multiplied by
a weight declared in ``config/config.yaml``. Percentile ranks are used instead
of raw values because the underlying distributions are extremely skewed - one
SKU with £50k of exposure would otherwise dominate the score for everything
else. Each SKU's score can be decomposed back into its weighted parts, and
those contributions are written out alongside the score.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import Config, get_paths, load_config

CRITICAL_STOCKOUT = "Critical Stockout Risk"
STOCKOUT = "Stockout Risk"
HEALTHY = "Healthy"
OVERSTOCK = "Overstock Risk"
SEVERE_OVERSTOCK = "Severe Overstock"

RISK_ORDER = [CRITICAL_STOCKOUT, STOCKOUT, HEALTHY, OVERSTOCK, SEVERE_OVERSTOCK]
RISK_EMOJI = {
    CRITICAL_STOCKOUT: "🔴", STOCKOUT: "🟠", HEALTHY: "🟢",
    OVERSTOCK: "🟡", SEVERE_OVERSTOCK: "🟣",
}
STOCKOUT_LEVELS = {CRITICAL_STOCKOUT, STOCKOUT}
OVERSTOCK_LEVELS = {OVERSTOCK, SEVERE_OVERSTOCK}


def classify(metrics: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """Assign each SKU exactly one risk level plus an actionability flag."""
    t = cfg["risk_thresholds"]
    df = metrics.copy()

    cover = df["cover_ratio"]
    excess = df["excess_ratio"]

    conditions = [
        cover < t["critical_stockout_cover_ratio"],
        cover < t["stockout_cover_ratio"],
        excess > t["severe_overstock_excess_ratio"],
        excess > t["overstock_excess_ratio"],
    ]
    choices = [CRITICAL_STOCKOUT, STOCKOUT, SEVERE_OVERSTOCK, OVERSTOCK]
    df["risk_level"] = np.select(conditions, choices, default=HEALTHY)
    df["risk_emoji"] = df["risk_level"].map(RISK_EMOJI)
    df["risk_family"] = np.where(
        df["risk_level"].isin(STOCKOUT_LEVELS), "Stockout",
        np.where(df["risk_level"].isin(OVERSTOCK_LEVELS), "Overstock", "Healthy"),
    )

    # Materiality: does acting on this SKU move a number the business cares about?
    is_stockout = df["risk_level"].isin(STOCKOUT_LEVELS)
    is_overstock = df["risk_level"].isin(OVERSTOCK_LEVELS)
    df["is_actionable"] = (
        (is_stockout & (df["estimated_lost_revenue"] >= t["min_lost_revenue_gbp"]))
        | (is_overstock & (df["excess_value"] >= t["min_excess_value_gbp"]))
    )

    # DEAD STOCK is a distinct condition, not just extreme overstock. A SKU
    # holding 4,406 units against a 90-day forecast of 4 units is not
    # "over-ordered" - it has stopped selling. The commercial response is
    # liquidation, not a smaller reorder, so it is flagged separately and gets
    # its own recommendation.
    df["is_dead_stock"] = (
        (df["forecast_units_90d"] < t.get("dead_stock_max_forecast_units", 1.0))
        & (df["current_inventory"] > 0)
        & (df["inventory_value"] >= t["min_excess_value_gbp"])
    )

    # Raw excess_ratio is unbounded (it divides by a near-zero requirement) and
    # is useless on a chart axis. Keep the raw value for audit, publish a
    # capped version for display and scoring.
    df["excess_ratio_capped"] = df["excess_ratio"].replace(np.inf, np.nan).clip(upper=100)
    return df


def _pct_rank(series: pd.Series, ascending: bool = True) -> pd.Series:
    """Percentile rank in [0, 1]; robust to the heavy skew in these metrics."""
    if series.notna().sum() <= 1:
        return pd.Series(0.5, index=series.index)
    return series.rank(pct=True, ascending=ascending, na_option="bottom")


def score_stockout(df: pd.DataFrame, weights: dict) -> pd.DataFrame:
    """Priority score for SKUs at risk of running out."""
    parts = pd.DataFrame(index=df.index)
    parts["financial_exposure"] = _pct_rank(df["estimated_lost_revenue"])
    # Severity: how far below the reorder point, capped at "completely empty".
    parts["severity"] = (1 - df["cover_ratio"].clip(0, 1)).fillna(1.0)
    parts["velocity"] = _pct_rank(df["avg_daily_demand"])
    # Long lead time = slower to recover once short, so it raises urgency.
    parts["lead_time_pressure"] = _pct_rank(df["lead_time_days"])
    parts["revenue_contribution"] = _pct_rank(df["revenue_share"].fillna(0))
    return _weighted(parts, weights)


def score_overstock(df: pd.DataFrame, weights: dict) -> pd.DataFrame:
    """Priority score for SKUs tying up capital."""
    parts = pd.DataFrame(index=df.index)
    parts["capital_tied_up"] = _pct_rank(df["excess_value"])
    # Excess ratio capped at 10x so a dead SKU with infinite ratio does not
    # swamp genuinely large-value excess.
    parts["severity"] = (df["excess_ratio"].replace(np.inf, 10).clip(0, 10) / 10).fillna(1.0)
    # Slowness combines absolute cover with the demand trend: stock that is
    # both deep AND declining is the worst kind.
    cover_rank = _pct_rank(df["days_of_cover_forecast"].replace(np.inf, np.nan))
    decline = (1 - df["demand_trend_ratio"].fillna(1.0).clip(0, 2) / 2)
    parts["slowness"] = (0.6 * cover_rank.fillna(1.0) + 0.4 * decline)
    # Markdown exposure: the gross margin at risk if this has to be discounted.
    parts["margin_risk"] = _pct_rank(df["excess_units"] * (df["selling_price"] - df["unit_cost"]))
    return _weighted(parts, weights)


def _weighted(parts: pd.DataFrame, weights: dict) -> pd.DataFrame:
    """Combine components into a 0-100 score, retaining each contribution."""
    out = pd.DataFrame(index=parts.index)
    total = 0.0
    for name, weight in weights.items():
        component = parts[name].fillna(0).clip(0, 1)
        out[f"score_part_{name}"] = (100 * weight * component).round(2)
        total = total + weight * component
    out["priority_score"] = (100 * total).round(2)
    return out


def build(cfg: Config | None = None) -> pd.DataFrame:
    """Classify, score and rank every SKU."""
    cfg = cfg or load_config()
    paths = get_paths()
    metrics = pd.read_parquet(paths.processed / "inventory_metrics.parquet")

    df = classify(metrics, cfg)
    weights = cfg["priority_score"]

    stockout_mask = df["risk_level"].isin(STOCKOUT_LEVELS)
    overstock_mask = df["risk_level"].isin(OVERSTOCK_LEVELS)

    score_cols = [f"score_part_{k}" for k in weights["stockout_weights"]]
    score_cols += [f"score_part_{k}" for k in weights["overstock_weights"]]
    for col in set(score_cols) | {"priority_score"}:
        df[col] = np.nan

    if stockout_mask.any():
        s = score_stockout(df[stockout_mask], dict(weights["stockout_weights"]))
        df.loc[stockout_mask, s.columns] = s
    if overstock_mask.any():
        o = score_overstock(df[overstock_mask], dict(weights["overstock_weights"]))
        df.loc[overstock_mask, o.columns] = o

    df["priority_score"] = df["priority_score"].fillna(0.0)

    # Ranks are computed within each risk family: "the #1 reorder" and "the #1
    # markdown" are different questions and should not share a leaderboard.
    df["priority_rank"] = (
        df[df["is_actionable"]]
        .groupby("risk_family")["priority_score"]
        .rank(ascending=False, method="first")
    )
    df["as_of_date"] = pd.Timestamp(cfg["project"]["as_of_date"])
    return df.sort_values(["risk_family", "priority_score"], ascending=[True, False])


def summarise(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Roll the SKU-level risk table up for the dashboard and the README."""
    by_risk = df.groupby("risk_level", observed=False).agg(
        n_skus=("product_id", "size"),
        n_actionable=("is_actionable", "sum"),
        inventory_value=("inventory_value", "sum"),
        excess_value=("excess_value", "sum"),
        estimated_lost_revenue=("estimated_lost_revenue", "sum"),
        recommended_order_value=("order_cost", "sum"),
    ).reindex(RISK_ORDER).fillna(0).round(2)
    by_risk["pct_of_skus"] = (100 * by_risk.n_skus / by_risk.n_skus.sum()).round(1)

    def rollup(key: str) -> pd.DataFrame:
        g = df.groupby(key, observed=False).agg(
            n_skus=("product_id", "size"),
            inventory_value=("inventory_value", "sum"),
            excess_value=("excess_value", "sum"),
            estimated_lost_revenue=("estimated_lost_revenue", "sum"),
            n_stockout_risk=("risk_family", lambda s: (s == "Stockout").sum()),
            n_overstock_risk=("risk_family", lambda s: (s == "Overstock").sum()),
            avg_priority=("priority_score", "mean"),
        ).round(2)
        return g.sort_values("excess_value", ascending=False)

    return {
        "risk_summary": by_risk.reset_index(),
        "risk_by_category": rollup("category").reset_index(),
        "risk_by_supplier": rollup("supplier_name").reset_index(),
        "risk_by_abc": rollup("abc_class").reset_index(),
    }


def run(cfg: Config | None = None) -> dict[str, pd.DataFrame]:
    cfg = cfg or load_config()
    paths = get_paths()
    risk = build(cfg)
    risk.to_parquet(paths.processed / "sku_risk.parquet", index=False)
    risk.to_csv(paths.risk_tables / "sku_risk.csv", index=False)

    outputs = {"sku_risk": risk, **summarise(risk)}
    for name, frame in outputs.items():
        if name != "sku_risk":
            frame.to_csv(paths.risk_tables / f"{name}.csv", index=False)
    return outputs


if __name__ == "__main__":  # pragma: no cover
    for key, frame in run().items():
        print(f"{key:22s} {frame.shape}")
