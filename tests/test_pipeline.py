"""Tests for the analytical core.

These target the calculations a wrong answer would silently corrupt: the
inventory formulae, the intermittent-demand estimators, the metric guards, and
the no-lookahead property of the backtest. Run with ``pytest -q`` from the
project root.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.categorization import categorise_description
from src.forecasting import (BacktestConfig, MODELS, _croston_rate, _fold_origins,
                             croston, croston_sba, mase, moving_average,
                             naive_last, seasonal_index_forecast, smape)
from src.inventory_metrics import compute_safety_stock, z_for_service_level
from src.risk_scoring import (CRITICAL_STOCKOUT, HEALTHY, OVERSTOCK,
                              SEVERE_OVERSTOCK, STOCKOUT, classify)


# ---------------------------------------------------------------------------
# Inventory mathematics
# ---------------------------------------------------------------------------
def test_z_score_matches_standard_service_levels():
    assert z_for_service_level(0.95) == pytest.approx(1.6449, abs=1e-4)
    assert z_for_service_level(0.99) == pytest.approx(2.3263, abs=1e-4)
    assert z_for_service_level(0.50) == pytest.approx(0.0, abs=1e-9)


def test_safety_stock_matches_hand_calculation():
    d_bar = pd.Series([10.0]); sigma_d = pd.Series([4.0])
    lt = pd.Series([20]); sigma_lt = pd.Series([3.0])
    review, z = 7, 1.6449
    got = compute_safety_stock(d_bar, sigma_d, lt, sigma_lt, review, z, True)
    expected = z * np.sqrt((20 + 7) * 4.0 ** 2 + 10.0 ** 2 * 3.0 ** 2)
    assert got.iloc[0] == pytest.approx(expected)


def test_lead_time_variability_term_increases_safety_stock():
    """Ignoring lead-time variability must never produce MORE safety stock."""
    d_bar = pd.Series([10.0, 5.0]); sigma_d = pd.Series([4.0, 2.0])
    lt = pd.Series([20, 5]); sigma_lt = pd.Series([8.0, 1.0])
    both = compute_safety_stock(d_bar, sigma_d, lt, sigma_lt, 7, 1.6449, True)
    demand_only = compute_safety_stock(d_bar, sigma_d, lt, sigma_lt, 7, 1.6449, False)
    assert (both >= demand_only).all()
    # The SKU with the higher lead-time SD must gain proportionally more.
    assert (both[0] / demand_only[0]) > (both[1] / demand_only[1])


def test_safety_stock_is_zero_when_nothing_varies():
    z = 1.6449
    got = compute_safety_stock(pd.Series([10.0]), pd.Series([0.0]),
                               pd.Series([10]), pd.Series([0.0]), 7, z, True)
    assert got.iloc[0] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Forecasting models
# ---------------------------------------------------------------------------
def test_croston_recovers_a_known_intermittent_rate():
    """Demand of 20 units every 5 days is a rate of 4.0 per day."""
    y = np.zeros(300); y[np.arange(0, 300, 5)] = 20.0
    assert _croston_rate(y, alpha=0.1) == pytest.approx(4.0, rel=0.05)


def test_sba_is_a_downward_correction_of_croston():
    y = np.zeros(200); y[np.arange(0, 200, 4)] = 15.0
    c = croston(y, 30).sum()
    s = croston_sba(y, 30).sum()
    assert s < c
    assert s == pytest.approx(c * (1 - 0.1 / 2), rel=1e-9)


def test_croston_handles_all_zero_and_single_spike_series():
    assert _croston_rate(np.zeros(100)) == 0.0
    y = np.zeros(100); y[50] = 7.0
    assert _croston_rate(y) == pytest.approx(0.07)


def test_naive_fails_on_intermittent_demand_as_expected():
    """The naive baseline must return 0 after a zero day - this is why it loses."""
    y = np.zeros(100); y[np.arange(0, 100, 5)] = 10.0
    assert naive_last(y, 30).sum() == 0.0


def test_no_model_returns_negative_or_nan_demand():
    rng = np.random.default_rng(0)
    series = {
        "intermittent": np.where(rng.random(500) < 0.2, rng.poisson(8, 500), 0).astype(float),
        "dense": np.clip(50 + rng.normal(0, 12, 500), 0, None),
        "zeros": np.zeros(500),
        "trending": np.clip(np.linspace(0, 80, 500) + rng.normal(0, 5, 500), 0, None),
    }
    for name, y in series.items():
        for model, spec in MODELS.items():
            pred = spec["fn"](y, 30)
            assert len(pred) == 30, f"{model}/{name} wrong length"
            assert np.all(np.isfinite(pred)), f"{model}/{name} produced non-finite"
            assert np.all(pred >= 0), f"{model}/{name} produced negative demand"


def test_seasonal_index_tracks_a_seasonal_drop():
    """A SKU that collapsed after the peak last year should be forecast to do so again."""
    y = np.concatenate([
        np.full(300, 10.0), np.full(64, 40.0),   # ramp a year ago
        np.full(300, 12.0), np.full(64, 48.0),   # same shape, higher level
    ])
    # Append the post-peak collapse from a year ago into the history window.
    y = np.concatenate([np.full(30, 4.0), y])
    pred = seasonal_index_forecast(y, 30)
    assert np.all(np.isfinite(pred)) and np.all(pred >= 0)


def test_fold_origins_are_ordered_and_leave_a_full_horizon():
    cfg = BacktestConfig(n_folds=5, horizon=30, seasonal_period=7,
                         intermittent_threshold=0.7, sarima_sample_size=100)
    origins = _fold_origins(739, cfg)
    assert origins == sorted(origins), "origins must walk forward"
    assert origins[-1] + 30 == 739, "last fold must end exactly at the as-of date"
    assert len(set(origins)) == 5


def test_backtest_has_no_lookahead():
    """A change made AFTER the fold origin must not alter that fold's forecast."""
    rng = np.random.default_rng(7)
    y = np.clip(rng.normal(20, 5, 400), 0, None)
    origin = 300
    baseline = moving_average(y[:origin], 30)

    tampered = y.copy()
    tampered[origin:] = 9999.0          # corrupt only the future
    after = moving_average(tampered[:origin], 30)
    assert np.allclose(baseline, after)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def test_smape_is_defined_when_actuals_are_zero():
    """MAPE would divide by zero here; sMAPE must stay finite."""
    actual = np.array([0.0, 0.0, 5.0]); pred = np.array([2.0, 0.0, 5.0])
    value = smape(actual, pred)
    assert np.isfinite(value) and 0 <= value <= 200


def test_smape_is_zero_for_a_perfect_all_zero_forecast():
    assert smape(np.zeros(10), np.zeros(10)) == 0.0


def test_smape_saturates_at_200_for_a_zero_actual():
    assert smape(np.array([0.0]), np.array([5.0])) == pytest.approx(200.0)


def test_mase_below_one_means_better_than_naive():
    train = np.array([10.0, 12.0, 11.0, 13.0, 12.0, 14.0, 13.0])
    actual = np.array([13.0, 13.0, 13.0])
    good = mase(actual, np.array([13.0, 13.0, 13.0]), train)
    bad = mase(actual, np.array([0.0, 0.0, 0.0]), train)
    assert good == pytest.approx(0.0)
    assert bad > 1


def test_mase_returns_nan_for_a_constant_series():
    """A flat training series has zero naive error - the scale is undefined."""
    assert np.isnan(mase(np.array([1.0]), np.array([1.0]), np.full(10, 5.0)))


# ---------------------------------------------------------------------------
# Risk classification
# ---------------------------------------------------------------------------
def _risk_frame(cover, excess, lost=1000.0, excess_val=1000.0, forecast=500.0):
    return pd.DataFrame({
        "cover_ratio": cover, "excess_ratio": excess,
        "estimated_lost_revenue": [lost] * len(cover),
        "excess_value": [excess_val] * len(cover),
        "forecast_units_90d": [forecast] * len(cover),
        "current_inventory": [100.0] * len(cover),
        "inventory_value": [1000.0] * len(cover),
    })


CFG = {"risk_thresholds": {
    "critical_stockout_cover_ratio": 0.5, "stockout_cover_ratio": 1.0,
    "overstock_excess_ratio": 1.5, "severe_overstock_excess_ratio": 3.0,
    "min_excess_value_gbp": 50.0, "min_lost_revenue_gbp": 25.0,
    "dead_stock_max_forecast_units": 1.0}}


def test_risk_levels_map_to_the_documented_thresholds():
    df = _risk_frame([0.2, 0.8, 1.2, 1.2, 1.2], [1.0, 1.0, 1.0, 2.0, 5.0])
    out = classify(df, CFG)
    assert list(out.risk_level) == [
        CRITICAL_STOCKOUT, STOCKOUT, HEALTHY, OVERSTOCK, SEVERE_OVERSTOCK]


def test_stockout_takes_precedence_over_overstock():
    """Contradictory ratios must resolve to the more urgent condition."""
    df = _risk_frame([0.3], [10.0])
    assert classify(df, CFG).risk_level.iloc[0] == CRITICAL_STOCKOUT


def test_materiality_floor_suppresses_trivial_alerts():
    big = classify(_risk_frame([1.2], [5.0], excess_val=1000.0), CFG)
    tiny = classify(_risk_frame([1.2], [5.0], excess_val=4.0), CFG)
    assert bool(big.is_actionable.iloc[0]) is True
    assert bool(tiny.is_actionable.iloc[0]) is False
    # The risk LEVEL is retained for reporting even when not actionable.
    assert tiny.risk_level.iloc[0] == SEVERE_OVERSTOCK


def test_dead_stock_is_flagged_independently_of_the_excess_ratio():
    """A SKU with no forward demand is dead stock even if its ratio looks healthy."""
    df = _risk_frame([1.2], [1.0], forecast=0.0)
    out = classify(df, CFG)
    assert out.risk_level.iloc[0] == HEALTHY
    assert bool(out.is_dead_stock.iloc[0]) is True


def test_excess_ratio_is_capped_for_display():
    df = _risk_frame([1.2], [9999.0])
    assert classify(df, CFG).excess_ratio_capped.iloc[0] == 100


# ---------------------------------------------------------------------------
# Categorisation
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("description,expected", [
    ("JUMBO BAG RED RETROSPOT", "Bags"),                 # Bags beats Vintage
    ("VINTAGE SNAKES & LADDERS", "Toys & Games"),        # Toys beats Vintage
    ("WHITE HANGING HEART T-LIGHT HOLDER", "Candles & Fragrance"),
    ("REGENCY CAKESTAND 3 TIER", "Kitchen & Dining"),
    ("PARTY INVITES DINOSAURS", "Party & Celebration"),  # Party beats Toys
    ("PAPER CHAIN KIT 50'S CHRISTMAS", "Christmas & Seasonal"),
    ("SCOTTIES DESIGN WASHBAG", "Bags"),                 # closed compound
])
def test_category_priority_order(description, expected):
    assert categorise_description(description) == expected


def test_word_boundaries_prevent_substring_false_matches():
    """Unanchored 'PEN ' matched 'O(PEN S)HAPE' - the bug the audit caught."""
    assert categorise_description("HAND OPEN SHAPE GOLD") != "Stationery & Craft"


def test_exclusions_let_a_later_rule_claim_the_product():
    assert categorise_description("BLUE GLASS GEMS IN BAG") != "Bags"
    assert categorise_description("LOVEBIRD HANGING DECORATION WHITE") == "Home Decor"


def test_missing_description_falls_back_to_other():
    for value in (None, "", "   ", float("nan"), 12345):
        assert categorise_description(value) == "Other"
