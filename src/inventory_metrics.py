"""Safety stock, reorder point and coverage metrics.

FORMULAE AND WHY THESE ONES
---------------------------
**Protection interval.** Stock must cover demand not just over the lead time
but over the lead time *plus the review period* - after a review you cannot
order again until the next one, so exposure runs to the arrival of the order
placed at the NEXT review::

    PI = lead_time_days + review_period_days

**Safety stock.** The textbook form ``z * sigma_d * sqrt(LT)`` assumes lead
time is deterministic. It is not here: supplier lead-time SD ranges from 1.0 to
8.0 days, and the backtest of the incumbent policy showed service degrading
with sourcing region precisely because of late shipments. The combined form is
therefore used::

    SS = z * sqrt( PI * sigma_d^2  +  d_bar^2 * sigma_LT^2 )
                   \\_____________/    \\__________________/
                    demand variability   lead-time variability

Dropping the second term would understate safety stock most for exactly the
Far-East SKUs that need it most.

**Reorder point.**::

    ROP = d_bar * PI + SS

**Order-up-to level.** ``S = ROP`` under a periodic-review policy, so the
recommended order quantity is ``max(0, S - inventory_position)``.

**Demand rate.** ``d_bar`` is taken from the FORECAST (forward-looking), not
from a trailing average. The whole point of building a forecasting layer is to
use it. ``sigma_d`` is still estimated from the trailing 90-day window, because
the forecast provides a level but not a dispersion.

**Inventory position, not on-hand.** Every reorder comparison uses
``on_hand + on_order``. Comparing ROP to on-hand alone would re-order stock
that is already in transit - the classic double-ordering bug.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import norm

from .config import Config, get_paths, load_config


def z_for_service_level(service_level: float) -> float:
    """Inverse normal for a cycle service level (0.95 -> 1.645)."""
    return float(norm.ppf(service_level))


def compute_safety_stock(d_bar: pd.Series, sigma_d: pd.Series,
                         lead_time: pd.Series, sigma_lt: pd.Series,
                         review_period: int, z: float,
                         use_lead_time_variability: bool = True) -> pd.Series:
    """Safety stock with (optionally) both variability terms."""
    protection = lead_time + review_period
    demand_term = protection * sigma_d.fillna(0) ** 2
    if use_lead_time_variability:
        lt_term = (d_bar.fillna(0) ** 2) * (sigma_lt.fillna(0) ** 2)
    else:
        lt_term = 0.0
    return z * np.sqrt(demand_term + lt_term)


def build(cfg: Config | None = None) -> pd.DataFrame:
    """Assemble the SKU-level inventory-metrics table."""
    cfg = cfg or load_config()
    paths = get_paths()
    pol = cfg["inventory_policy"]

    from .database import connect, register_base_tables
    con = connect()
    register_base_tables(con, cfg)
    position = con.execute("SELECT * FROM inventory_position").fetchdf()
    abc = con.execute("SELECT product_id, abc_class, revenue_share FROM sku_abc").fetchdf()
    hist = con.execute(
        "SELECT product_id, fill_rate AS historical_fill_rate, zero_stock_days, "
        "lost_revenue AS historical_lost_revenue, demand_units AS historical_demand_units "
        "FROM stockout_summary"
    ).fetchdf()
    con.close()

    forecast = pd.read_parquet(paths.processed / "forecast_sku.parquet")
    selection = pd.read_parquet(paths.processed / "forecast_selection.parquet")

    horizon = pol["planning_horizon_days"]
    fh = forecast[forecast["horizon_days"] == horizon].set_index("product_id")
    if fh.empty:
        raise ValueError(
            f"No forecast rows for the {horizon}-day planning horizon. "
            f"Add {horizon} to forecasting.horizons in config/config.yaml "
            f"and re-run src.forecasting."
        )

    df = (position
          .merge(abc, on="product_id", how="left")
          .merge(hist, on="product_id", how="left")
          .merge(selection[["product_id", "selected_model", "is_intermittent",
                            "backtest_mase", "backtest_smape",
                            "backtest_horizon_abs_error"]],
                 on="product_id", how="left"))

    # Publish every configured horizon so the dashboard can show 7/14/30/60/90
    # without re-running the forecaster.
    for h in sorted(forecast["horizon_days"].unique()):
        sub = forecast[forecast["horizon_days"] == h].set_index("product_id")
        df[f"forecast_units_{h}d"] = df["product_id"].map(sub["forecast_units"]).fillna(0.0)

    df["forecast_planning_horizon_units"] = df["product_id"].map(fh["forecast_units"]).fillna(0.0)
    df["forecast_seasonal_scenario"] = df["product_id"].map(
        fh["forecast_units_seasonal_scenario"]).fillna(0.0)
    df["forecast_lower_80"] = df["product_id"].map(fh["forecast_lower_80"]).fillna(0.0)
    df["forecast_upper_80"] = df["product_id"].map(fh["forecast_upper_80"]).fillna(0.0)

    # --- demand rate and dispersion -----------------------------------------
    # Forward-looking level from the forecast; dispersion from recent history.
    df["avg_daily_demand"] = df["forecast_planning_horizon_units"] / horizon
    df["sd_daily_demand"] = df["sd_daily_demand_90d"].fillna(0.0)

    # --- policy parameters ---------------------------------------------------
    z = z_for_service_level(pol["service_level"])
    review = pol["review_period_days"]
    df["service_level"] = pol["service_level"]
    df["z_score"] = round(z, 4)
    df["review_period_days"] = review
    df["protection_interval_days"] = df["lead_time_days"] + review

    # --- the core inventory maths -------------------------------------------
    df["lead_time_demand"] = df["avg_daily_demand"] * df["protection_interval_days"]
    df["safety_stock"] = compute_safety_stock(
        df["avg_daily_demand"], df["sd_daily_demand"],
        df["lead_time_days"], df["lead_time_sd_days"],
        review, z, pol["use_lead_time_variability"],
    )
    df["safety_stock"] = df["safety_stock"].round(0)
    df["reorder_point"] = (df["lead_time_demand"] + df["safety_stock"]).round(0)

    # Expected requirement over the planning horizon, used for overstock.
    df["expected_requirement"] = (df["forecast_planning_horizon_units"] + df["safety_stock"]).round(0)

    # --- comparisons ---------------------------------------------------------
    df["inventory_position"] = df["current_inventory"] + df["on_order"]
    df["below_reorder_point"] = df["inventory_position"] < df["reorder_point"]

    # Scale-free ratios drive the risk thresholds so they work across SKUs of
    # any volume. Guarded against divide-by-zero for genuinely dead SKUs.
    df["cover_ratio"] = np.where(
        df["reorder_point"] > 0,
        df["inventory_position"] / df["reorder_point"].replace(0, np.nan),
        np.inf,
    )
    df["excess_ratio"] = np.where(
        df["expected_requirement"] > 0,
        df["inventory_position"] / df["expected_requirement"].replace(0, np.nan),
        np.inf,
    )

    df["days_of_cover_forecast"] = np.where(
        df["avg_daily_demand"] > 0,
        df["current_inventory"] / df["avg_daily_demand"].replace(0, np.nan),
        np.nan,
    )

    # --- recommended order quantity -----------------------------------------
    # Order-up-to level equals the reorder point under periodic review.
    df["order_up_to_level"] = df["reorder_point"]
    df["recommended_order_qty"] = np.ceil(
        (df["order_up_to_level"] - df["inventory_position"]).clip(lower=0)
    )
    df["order_cost"] = (df["recommended_order_qty"] * df["unit_cost"]).round(2)

    # --- excess -------------------------------------------------------------
    df["excess_units"] = (df["inventory_position"] - df["expected_requirement"]).clip(lower=0)
    df["excess_value"] = (df["excess_units"] * df["unit_cost"]).round(2)

    # --- estimated exposure if no action is taken ---------------------------
    # Units of demand that inventory position cannot cover over the protection
    # interval. Explicitly an ESTIMATE of unmet demand, not realised loss.
    df["estimated_shortfall_units"] = (
        df["lead_time_demand"] - df["inventory_position"]
    ).clip(lower=0).round(0)
    df["estimated_lost_revenue"] = (
        df["estimated_shortfall_units"] * df["selling_price"]
    ).round(2)
    df["estimated_lost_margin"] = (
        df["estimated_shortfall_units"] * (df["selling_price"] - df["unit_cost"])
    ).round(2)

    df["as_of_date"] = pd.Timestamp(cfg["project"]["as_of_date"])
    return df


def run(cfg: Config | None = None) -> pd.DataFrame:
    cfg = cfg or load_config()
    paths = get_paths()
    metrics = build(cfg)
    metrics.to_parquet(paths.processed / "inventory_metrics.parquet", index=False)
    metrics.to_csv(paths.risk_tables / "inventory_metrics.csv", index=False)
    return metrics


if __name__ == "__main__":  # pragma: no cover
    result = run()
    print(result.shape)
