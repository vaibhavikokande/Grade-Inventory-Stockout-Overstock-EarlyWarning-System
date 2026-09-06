"""SKU-level demand forecasting with rolling-origin backtesting.

MODEL STRATEGY
--------------
74.9% of SKU-days in this dataset have zero demand, and 1,651 of 2,370 SKUs are
zero on more than 70% of days. That single fact drives every modelling choice:

* **Prophet was rejected.** Prophet models a continuous trend plus smooth
  seasonalities. It is designed for dense, business-daily series with strong
  calendar structure. Fitted to a series that is zero four days in five it
  produces smooth non-negative drift that is neither accurate nor interpretable,
  at roughly 1-2 s per SKU (~40-80 min per backtest fold set). Popularity is not
  a modelling argument.
* **Intermittent SKUs are routed to Croston/SBA**, the standard family for spare
  parts and slow-moving retail, which model demand SIZE and demand INTERVAL
  separately instead of pretending the zeros are observations of a smooth process.
* **Dense SKUs get ETS/Holt-Winters**, which handles the weekly seasonality
  created by this business not trading on Saturdays.
* **SARIMA is evaluated on a sample**, not on all 2,370 SKUs - see
  ``sarima_sample_size``. If it does not beat far cheaper methods on the highest-
  value dense SKUs, fitting it 7,110 times is not defensible engineering.

WHAT IS ACTUALLY BEING PREDICTED
--------------------------------
Inventory decisions do not need tomorrow's demand. They need **total demand over
the protection interval** (lead time + review period). Model selection is
therefore driven by error on the *horizon total*, not on the daily path. A model
can have poor day-to-day accuracy and still be the right choice if its 30-day
total is unbiased - and for intermittent demand that is usually exactly the case.

VALIDATION
----------
Rolling-origin (walk-forward) backtesting with 3 folds. Fold k trains on
everything strictly before its origin and predicts the following 30 days.
Time-series data is NEVER randomly split. There is no leakage: at every origin
the model sees only ``demand[:origin]``.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

from .config import Config, get_paths, load_config

warnings.filterwarnings("ignore")

ForecastFn = Callable[[np.ndarray, int], np.ndarray]


# ---------------------------------------------------------------------------
# Baseline and intermittent-demand models
# ---------------------------------------------------------------------------
def naive_last(y: np.ndarray, h: int) -> np.ndarray:
    """Repeat the final observation. The weakest defensible baseline."""
    return np.full(h, y[-1] if len(y) else 0.0, dtype=float)


def seasonal_naive(y: np.ndarray, h: int, period: int = 7) -> np.ndarray:
    """Repeat the last full weekly cycle - captures the no-Saturday pattern."""
    if len(y) < period:
        return naive_last(y, h)
    cycle = y[-period:]
    return np.array([cycle[i % period] for i in range(h)], dtype=float)


def moving_average(y: np.ndarray, h: int, window: int = 28) -> np.ndarray:
    """Flat forecast at the trailing mean. A strong baseline for noisy demand."""
    if len(y) == 0:
        return np.zeros(h)
    return np.full(h, float(y[-min(window, len(y)):].mean()))


# Annual lag of 364 days = 52 whole weeks, so the day-of-week alignment is
# preserved (important here: the business never trades on Saturdays).
ANNUAL_LAG = 364


def seasonal_naive_annual(y: np.ndarray, h: int, lag: int = ANNUAL_LAG) -> np.ndarray:
    """Replay the same calendar window one year earlier.

    Added after the first backtest showed every trailing-window model
    under-forecasting the Christmas ramp by ~11%. Trailing means cannot
    anticipate an annual peak; last year's actuals can.
    """
    if len(y) < lag + h:
        return moving_average(y, h)
    start = len(y) - lag
    return np.clip(y[start:start + h].astype(float), 0, None)


def seasonal_naive_annual_scaled(y: np.ndarray, h: int, lag: int = ANNUAL_LAG,
                                 window: int = 90) -> np.ndarray:
    """Last year's same window, rescaled by the SKU's year-on-year growth.

    Corrects the pure annual replay for SKUs whose overall demand level has
    shifted. The ratio is clipped to [0.25, 4.0] so a SKU that barely sold last
    year cannot produce an explosive forecast.
    """
    if len(y) < lag + h or len(y) < lag + window:
        return moving_average(y, h)
    recent = y[-window:].sum()
    year_ago = y[-window - lag:-lag].sum()
    if year_ago <= 0:
        return seasonal_naive_annual(y, h, lag)
    ratio = float(np.clip(recent / year_ago, 0.25, 4.0))
    return np.clip(seasonal_naive_annual(y, h, lag) * ratio, 0, None)


def _croston_rate(y: np.ndarray, alpha: float = 0.1, bias_correction: float = 1.0) -> float:
    """Core of Croston's method: exponentially smooth demand SIZE and INTERVAL.

    Returns a per-period demand rate = smoothed_size / smoothed_interval,
    optionally scaled by a bias-correction factor.

    Croston's original estimator is biased upward; the Syntetos-Boylan
    Approximation (SBA) multiplies the rate by (1 - alpha/2) to correct it.
    """
    nz = np.flatnonzero(y)
    if nz.size == 0:
        return 0.0
    if nz.size == 1:
        return float(y[nz[0]] / max(len(y), 1))

    size = float(y[nz[0]])
    interval = float(nz[0] + 1)
    prev = nz[0]
    for idx in nz[1:]:
        size += alpha * (y[idx] - size)
        interval += alpha * ((idx - prev) - interval)
        prev = idx
    return float(size / interval) * bias_correction if interval > 0 else 0.0


def croston(y: np.ndarray, h: int, alpha: float = 0.1) -> np.ndarray:
    return np.full(h, _croston_rate(y, alpha, 1.0))


def croston_sba(y: np.ndarray, h: int, alpha: float = 0.1) -> np.ndarray:
    """Syntetos-Boylan Approximation: Croston with the bias correction applied."""
    return np.full(h, _croston_rate(y, alpha, 1.0 - alpha / 2.0))


def seasonal_index_forecast(y: np.ndarray, h: int, lag: int = ANNUAL_LAG,
                            window: int = 28) -> np.ndarray:
    """Current demand level x last year's relative seasonal movement.

    Classic ratio-to-moving-average decomposition, and the model that matters
    most here. The as-of date (2011-12-09) sits on the Christmas peak, and the
    30-day operational window is the post-Christmas collapse - demand fell to
    34.9% of peak over the equivalent window a year earlier.

    A trailing-mean model cannot see that cliff and would over-forecast the
    window by ~2x, inflating every reorder point and pushing the business to
    over-order into a demand trough. This model anchors on the CURRENT level
    (so it tracks growth) but applies LAST YEAR's shape (so it sees the cliff).
    """
    if len(y) < lag + h or len(y) < lag + window:
        return moving_average(y, h, window)

    level = float(y[-window:].mean())
    start = len(y) - lag
    ly_window = y[start:start + h].astype(float)
    ly_level = float(y[start - window:start].mean())
    if ly_level <= 0 or level <= 0:
        return moving_average(y, h, window)

    # Per-day seasonal multipliers, clipped so a freak day last year cannot
    # dominate the forecast.
    index = np.clip(ly_window / ly_level, 0.0, 5.0)
    return np.clip(level * index, 0, None)


def ets_forecast(y: np.ndarray, h: int, period: int = 7) -> np.ndarray:
    """Holt-Winters / exponential smoothing with weekly seasonality.

    Falls back to simple exponential smoothing when there is not enough history
    for a seasonal fit, and to the trailing mean if the optimiser fails.
    """
    from statsmodels.tsa.holtwinters import ExponentialSmoothing, SimpleExpSmoothing

    if len(y) < 2 * period + 2 or np.all(y == 0):
        return moving_average(y, h)
    try:
        if len(y) >= 4 * period:
            model = ExponentialSmoothing(
                y, trend=None, seasonal="add", seasonal_periods=period,
                initialization_method="estimated",
            ).fit(optimized=True)
        else:
            model = SimpleExpSmoothing(y, initialization_method="estimated").fit(optimized=True)
        pred = np.asarray(model.forecast(h), dtype=float)
        if not np.all(np.isfinite(pred)):
            return moving_average(y, h)
        return np.clip(pred, 0, None)   # demand cannot be negative
    except Exception:
        return moving_average(y, h)


def sarima_forecast(y: np.ndarray, h: int, period: int = 7) -> np.ndarray:
    """SARIMA(1,0,1)(1,0,1,7). Expensive; evaluated on a sample only."""
    from statsmodels.tsa.statespace.sarimax import SARIMAX

    if len(y) < 3 * period or np.all(y == 0):
        return moving_average(y, h)
    try:
        # trend="c" is essential: without a constant the model has zero mean and
        # every forecast decays to 0, which is catastrophic for demand data.
        model = SARIMAX(
            y, order=(1, 0, 1), seasonal_order=(1, 0, 1, period), trend="c",
            enforce_stationarity=False, enforce_invertibility=False,
        ).fit(disp=False, maxiter=50)
        pred = np.asarray(model.forecast(h), dtype=float)
        if not np.all(np.isfinite(pred)):
            return moving_average(y, h)
        return np.clip(pred, 0, None)
    except Exception:
        return moving_average(y, h)


# Model registry. "family" controls which SKUs a model is a candidate for.
MODELS: dict[str, dict] = {
    "naive":            {"fn": naive_last,               "family": "all",          "cost": "trivial"},
    "seasonal_naive":   {"fn": seasonal_naive,           "family": "all",          "cost": "trivial"},
    "moving_average":   {"fn": moving_average,           "family": "all",          "cost": "trivial"},
    "annual_naive":     {"fn": seasonal_naive_annual,    "family": "all",          "cost": "trivial"},
    "annual_scaled":    {"fn": seasonal_naive_annual_scaled, "family": "all",      "cost": "trivial"},
    "seasonal_index":   {"fn": seasonal_index_forecast,  "family": "all",          "cost": "trivial"},
    "croston":          {"fn": croston,                  "family": "intermittent", "cost": "cheap"},
    "croston_sba":      {"fn": croston_sba,              "family": "intermittent", "cost": "cheap"},
    "ets":              {"fn": ets_forecast,             "family": "dense",        "cost": "moderate"},
    "sarima":           {"fn": sarima_forecast,          "family": "sample",       "cost": "expensive"},
}


# ---------------------------------------------------------------------------
# Error metrics
# ---------------------------------------------------------------------------
def smape(actual: np.ndarray, pred: np.ndarray) -> float:
    """Symmetric MAPE, in percent.

    Chosen over MAPE because actual demand is zero on ~75% of SKU-days, which
    makes MAPE undefined (division by zero). sMAPE is bounded at 200% and stays
    finite; the pair (actual=0, pred=0) is defined as 0 error.
    """
    denom = (np.abs(actual) + np.abs(pred))
    mask = denom > 0
    if not mask.any():
        return 0.0
    return float(200.0 * np.mean(np.abs(pred[mask] - actual[mask]) / denom[mask]))


def mase(actual: np.ndarray, pred: np.ndarray, train: np.ndarray, period: int = 1) -> float:
    """Mean Absolute Scaled Error.

    The most appropriate accuracy measure for intermittent demand: it scales
    error by the in-sample naive forecast's MAE, so it is defined when actuals
    are zero and is comparable across SKUs of wildly different volume.
    MASE < 1 means the model beats a naive one-step forecast.
    """
    if len(train) <= period:
        return np.nan
    scale = np.mean(np.abs(train[period:] - train[:-period]))
    if scale == 0:
        return np.nan
    return float(np.mean(np.abs(pred - actual)) / scale)


def evaluate(actual: np.ndarray, pred: np.ndarray, train: np.ndarray) -> dict[str, float]:
    err = pred - actual
    return {
        "mae": float(np.mean(np.abs(err))),
        "rmse": float(np.sqrt(np.mean(err ** 2))),
        "smape": smape(actual, pred),
        "mase": mase(actual, pred, train),
        # The decision-relevant metric: error on TOTAL demand over the horizon,
        # which is what a reorder quantity is actually built from.
        "horizon_actual": float(actual.sum()),
        "horizon_pred": float(pred.sum()),
        "horizon_abs_error": float(abs(pred.sum() - actual.sum())),
        "horizon_bias": float(pred.sum() - actual.sum()),
    }


# ---------------------------------------------------------------------------
# Backtesting
# ---------------------------------------------------------------------------
@dataclass
class BacktestConfig:
    n_folds: int
    horizon: int
    seasonal_period: int
    intermittent_threshold: float
    sarima_sample_size: int


def _fold_origins(n_obs: int, cfg: BacktestConfig) -> list[int]:
    """Rolling origins, most recent last. Fold k predicts [origin, origin+h)."""
    return [n_obs - (k + 1) * cfg.horizon for k in reversed(range(cfg.n_folds))]


def backtest_sku(y: np.ndarray, candidates: list[str],
                 cfg: BacktestConfig) -> list[dict]:
    """Rolling-origin evaluation of every candidate model for one SKU."""
    rows = []
    for origin in _fold_origins(len(y), cfg):
        if origin < cfg.seasonal_period * 4:
            continue                      # not enough history for this fold
        train, actual = y[:origin], y[origin:origin + cfg.horizon]
        if len(actual) < cfg.horizon:
            continue
        for name in candidates:
            pred = MODELS[name]["fn"](train, cfg.horizon)
            rows.append({"model": name, "origin_index": origin,
                         **evaluate(actual, pred, train)})
    return rows


def select_model(scores: pd.DataFrame) -> tuple[str, pd.DataFrame]:
    """Pick the model with the lowest mean horizon-total absolute error.

    Ties (and near-ties) break toward the cheaper, simpler model: the ordering
    in MODELS is from trivial to expensive, so a complex model must be strictly
    better by more than 1% to be selected.
    """
    ranked = scores.groupby("model").agg(
        mae=("mae", "mean"), rmse=("rmse", "mean"), smape=("smape", "mean"),
        mase=("mase", "mean"), horizon_abs_error=("horizon_abs_error", "mean"),
        horizon_bias=("horizon_bias", "mean"), n_folds=("mae", "size"),
    )
    order = {name: i for i, name in enumerate(MODELS)}
    best_error = ranked["horizon_abs_error"].min()
    within = ranked[ranked["horizon_abs_error"] <= best_error * 1.01]
    winner = min(within.index, key=lambda m: order[m])
    return winner, ranked


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def run(cfg: Config | None = None, verbose: bool = True) -> dict[str, pd.DataFrame]:
    """Backtest every SKU, select a model, and produce the operational forecast."""
    cfg = cfg or load_config()
    paths = get_paths()
    fc = cfg["forecasting"]

    bt = BacktestConfig(
        n_folds=fc["validation"]["n_folds"],
        horizon=fc["validation"]["fold_horizon_days"],
        seasonal_period=fc["seasonal_period"],
        intermittent_threshold=fc["intermittent_zero_share"],
        sarima_sample_size=int(fc.get("sarima_sample_size", 100)),
    )

    panel = pd.read_parquet(paths.processed / "demand_daily.parquet")
    panel = panel.sort_values(["product_id", "invoice_date"])
    dim = pd.read_parquet(paths.processed / "dim_product.parquet").set_index("product_id")

    series = {pid: g["demand_units"].to_numpy(float)
              for pid, g in panel.groupby("product_id", sort=True)}
    dates = np.sort(panel["invoice_date"].unique())

    # SARIMA is only evaluated on the highest-revenue dense SKUs.
    dense = [p for p, y in series.items()
             if (y == 0).mean() <= bt.intermittent_threshold]
    sarima_skus = set(
        dim.loc[dim.index.isin(dense), "total_revenue"]
        .sort_values(ascending=False).head(bt.sarima_sample_size).index
    )

    accuracy_rows, selection_rows, forecast_rows = [], [], []
    for i, (pid, y) in enumerate(series.items()):
        zero_share = float((y == 0).mean())
        is_intermittent = zero_share > bt.intermittent_threshold
        candidates = [
            n for n, m in MODELS.items()
            if m["family"] == "all"
            or (m["family"] == "intermittent" and is_intermittent)
            or (m["family"] == "dense" and not is_intermittent)
            or (m["family"] == "sample" and pid in sarima_skus)
        ]

        scores = pd.DataFrame(backtest_sku(y, candidates, bt))
        if scores.empty:
            continue
        winner, ranked = select_model(scores)

        ranked = ranked.reset_index()
        ranked.insert(0, "product_id", pid)
        ranked["is_selected"] = ranked["model"] == winner
        ranked["is_intermittent"] = is_intermittent
        accuracy_rows.append(ranked)

        # Empirical prediction interval from backtest horizon errors of the
        # WINNING model - honest uncertainty rather than a model-implied CI
        # that would ignore model-selection error.
        win_scores = scores[scores.model == winner]
        resid_sd = float(win_scores["horizon_bias"].std(ddof=0)) if len(win_scores) > 1 else np.nan

        selection_rows.append({
            "product_id": pid, "selected_model": winner,
            "is_intermittent": is_intermittent, "zero_day_share": round(zero_share, 4),
            "backtest_mae": float(win_scores["mae"].mean()),
            "backtest_rmse": float(win_scores["rmse"].mean()),
            "backtest_smape": float(win_scores["smape"].mean()),
            "backtest_mase": float(win_scores["mase"].mean()),
            "backtest_horizon_abs_error": float(win_scores["horizon_abs_error"].mean()),
            "backtest_horizon_bias": float(win_scores["horizon_bias"].mean()),
            "horizon_error_sd": resid_sd,
            "n_candidates": len(candidates),
        })

        # Operational forecast: refit the winner on ALL history up to as-of.
        max_h = max(fc["horizons"])
        path = MODELS[winner]["fn"](y, max_h)

        # SEASONAL REGIME SCENARIO.
        # The as-of date sits on the Christmas peak and the operational window
        # is the post-Christmas collapse. Backtest folds all fall in the
        # ramp-up, so model selection has NO evidence about post-peak
        # behaviour, and with only two years of history a seasonal model
        # cannot be validated on that regime (it needs one year to learn the
        # season and another to be tested on it).
        #
        # Rather than silently trust the validated-but-regime-blind forecast,
        # both are published: the selected model, and what last year's
        # seasonal shape implies. The gap between them is the single largest
        # source of uncertainty in this project.
        seasonal_path = seasonal_index_forecast(y, max_h)

        for h in fc["horizons"]:
            forecast_rows.append({
                "product_id": pid, "horizon_days": h,
                "forecast_units": float(path[:h].sum()),
                "forecast_daily_rate": float(path[:h].mean()),
                "forecast_units_seasonal_scenario": float(seasonal_path[:h].sum()),
                "selected_model": winner,
            })

        if verbose and (i + 1) % 400 == 0:
            print(f"  ...{i + 1}/{len(series)} SKUs backtested")

    accuracy = pd.concat(accuracy_rows, ignore_index=True)
    selection = pd.DataFrame(selection_rows)
    forecast = pd.DataFrame(forecast_rows)

    # Scale the empirical horizon-error SD to each requested horizon
    # (error grows with the square root of the horizon under a random walk).
    forecast = forecast.merge(
        selection[["product_id", "horizon_error_sd"]], on="product_id", how="left"
    )
    forecast["forecast_sd"] = (
        forecast["horizon_error_sd"]
        * np.sqrt(forecast["horizon_days"] / bt.horizon)
    ).fillna(0.0)
    forecast["forecast_lower_80"] = (forecast["forecast_units"] - 1.2816 * forecast["forecast_sd"]).clip(lower=0)
    forecast["forecast_upper_80"] = forecast["forecast_units"] + 1.2816 * forecast["forecast_sd"]
    forecast["as_of_date"] = pd.Timestamp(cfg["project"]["as_of_date"])

    outputs = {"forecast_sku": forecast, "forecast_accuracy": accuracy,
               "forecast_selection": selection}
    for name, frame in outputs.items():
        frame.to_parquet(paths.processed / f"{name}.parquet", index=False)
        frame.to_csv(paths.forecasts / f"{name}.csv", index=False)
    return outputs


if __name__ == "__main__":  # pragma: no cover
    for key, frame in run().items():
        print(f"{key:22s} {frame.shape}")
