"""Simulated supply-side layer: cost, supplier, lead time and daily inventory.

WHY THIS EXISTS
---------------
Online Retail II contains real transaction-level demand but no inventory
positions, unit costs, suppliers or lead times. No public dataset pairs real
demand with real inventory. Rather than abandon the inventory question or
fabricate inventory numbers at random, this module builds a supply layer whose
single random input is a set of *documented, industry-plausible parameters* -
and then derives the inventory ledger deterministically by simulating a
replenishment policy against the REAL observed demand series.

The consequence matters for interpretation:

    Historical stockouts in this project are not injected noise. They are what
    happens when a stated ordering policy meets the actual demand volatility
    present in the source data.

THE INCUMBENT POLICY
--------------------
The simulated planner runs a periodic-review, order-up-to (R, S) policy that is
deliberately naive - it is the legacy rule the project is meant to critique:

* review every ``review_period_days`` (weekly)
* order up to ``cover_weeks`` of trailing average daily demand
* assume a single flat ``nominal_lead_time_days`` for every supplier
* hold no explicit safety stock

Actual replenishment then arrives after the SKU's *true* stochastic lead time,
which for a Far-East supplier is far longer than the planner's flat assumption.
The gap between the two is a real, quantifiable failure mode, and it is what
the early-warning system exists to surface.

NO LOOKAHEAD
------------
At every review the order quantity is computed from a strictly trailing window
of demand (``demand[t - window : t]``). Nothing at or after ``t`` is visible.
The only exception is the opening stock position at t=0, which is a simulation
initial condition; the first ``warmup_days`` are therefore excluded from all
reported stockout statistics.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .categorization import CATEGORIES
from .config import Config, get_paths, load_config

# Sourcing regions determine the lead-time band. Ranges are stated as
# assumptions in reports/ASSUMPTIONS.md, benchmarked against typical UK
# giftware sourcing: domestic restock in ~1-2 weeks, EU in 2-4 weeks, Far East
# container freight in 4-7 weeks.
SOURCING_REGIONS: list[tuple[str, int, tuple[int, int], tuple[float, float]]] = [
    # (region, n_suppliers, lead_time_mean_range, lead_time_sd_range)
    ("UK Domestic", 4, (5, 12), (1.0, 2.5)),
    ("EU Import", 4, (12, 25), (2.0, 4.5)),
    ("Far East Import", 4, (28, 45), (5.0, 8.0)),
]

SUPPLIER_NAMES = [
    "Northgate Homeware", "Pennine Giftware", "Severn Valley Supply",
    "Camden Wholesale", "Rhineland Dekor", "Maison Lyonnaise",
    "Iberica Casa", "Adriatic Trading", "Pearl River Sourcing",
    "Golden Harbour Export", "Shenzhen Craftworks", "Bay Point Industries",
]


# ---------------------------------------------------------------------------
# Supplier master
# ---------------------------------------------------------------------------
def build_supplier_master(cfg: Config) -> pd.DataFrame:
    """Create the synthetic supplier dimension with lead-time characteristics."""
    rng = np.random.default_rng(cfg["project"]["random_seed"])
    rows, idx = [], 0
    for region, count, (lt_lo, lt_hi), (sd_lo, sd_hi) in SOURCING_REGIONS:
        for _ in range(count):
            rows.append({
                "supplier_id": f"SUP-{idx + 1:02d}",
                "supplier_name": SUPPLIER_NAMES[idx],
                "sourcing_region": region,
                "supplier_lead_time_days": int(rng.integers(lt_lo, lt_hi + 1)),
                "supplier_lead_time_sd_days": round(float(rng.uniform(sd_lo, sd_hi)), 2),
            })
            idx += 1
    return pd.DataFrame(rows)


def assign_supply_attributes(dim: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """Attach unit_cost, supplier and lead time to every SKU in the universe.

    Suppliers specialise: each category gets a primary and a secondary
    supplier, and SKUs fall to the primary 70% of the time. This makes
    "which supplier carries the most replenishment risk" a meaningful question
    rather than a uniform random split.
    """
    dcfg = cfg["derived_attributes"]
    rng = np.random.default_rng(cfg["project"]["random_seed"] + 1)
    suppliers = build_supplier_master(cfg)

    # --- category -> (primary, secondary) supplier ---------------------------
    order = rng.permutation(len(suppliers))
    routing = {}
    for i, category in enumerate(CATEGORIES):
        primary = suppliers.iloc[order[i % len(suppliers)]]
        secondary = suppliers.iloc[order[(i + 5) % len(suppliers)]]
        routing[category] = (primary.supplier_id, secondary.supplier_id)

    sku = dim[dim["in_universe"]].copy().reset_index(drop=True)
    cats = sku["category"].astype(str).to_numpy()
    pick_primary = rng.random(len(sku)) < 0.70
    sku["supplier_id"] = [
        routing[c][0] if p else routing[c][1] for c, p in zip(cats, pick_primary)
    ]
    sku = sku.merge(suppliers, on="supplier_id", how="left")

    # --- SKU-level lead time = supplier mean + jitter ------------------------
    jitter = dcfg["sku_lead_time_jitter_days"]
    sku["lead_time_days"] = np.clip(
        sku["supplier_lead_time_days"] + rng.integers(-jitter, jitter + 1, len(sku)),
        1, None,
    ).astype(int)
    sku["lead_time_sd_days"] = sku["supplier_lead_time_sd_days"]

    # --- unit cost = selling price x (1 - gross margin) ----------------------
    margin_map = dcfg["gross_margin_by_category"]
    base_margin = sku["category"].astype(str).map(margin_map).astype(float)
    lo, hi = dcfg["margin_bounds"]
    margin = np.clip(
        base_margin + rng.normal(0, dcfg["margin_jitter_sd"], len(sku)), lo, hi
    )
    sku["gross_margin"] = margin.round(4)
    sku["unit_cost"] = np.maximum(
        (sku["selling_price"] * (1 - margin)).round(2), 0.01
    )
    return sku


# ---------------------------------------------------------------------------
# Inventory ledger
# ---------------------------------------------------------------------------
def _simulate_one_sku(demand: np.ndarray, lead_time_mean: int, lead_time_sd: float,
                      pcfg: dict, rng: np.random.Generator
                      ) -> dict[str, np.ndarray]:
    """Run the incumbent (R, S) policy for a single SKU over the full horizon.

    Returns per-day arrays. All ordering decisions use strictly trailing demand.
    """
    horizon = len(demand)
    review_period = pcfg["review_period_days"]
    cover_days = pcfg["cover_weeks"] * 7
    window = pcfg["trailing_demand_window"]
    # The planner covers the supplier's QUOTED (mean) lead time but carries no
    # buffer for lead-time variability or demand variability. That is the
    # realistic legacy failure mode: the policy is right on average and wrong
    # exactly when demand spikes or a shipment runs late.
    nominal_lt = (
        lead_time_mean if pcfg.get("use_supplier_quoted_lead_time", False)
        else pcfg["nominal_lead_time_days"]
    )

    receipts = np.zeros(horizon + 400)          # arrivals indexed by day
    opening = np.zeros(horizon)
    closing = np.zeros(horizon)
    fulfilled = np.zeros(horizon)
    lost = np.zeros(horizon)
    on_order = np.zeros(horizon)
    ordered = np.zeros(horizon)

    # Opening position: initial_cover_weeks of the first month's mean demand.
    # This is a simulation initial condition, not a forecast; the first
    # warmup_days are excluded from all reported statistics.
    seed_mean = demand[: min(28, horizon)].mean() if horizon else 0.0
    on_hand = float(np.ceil(seed_mean * pcfg["initial_cover_weeks"] * 7))
    outstanding = 0.0

    for t in range(horizon):
        arrived = receipts[t]
        on_hand += arrived
        outstanding -= arrived
        opening[t] = on_hand

        sold = min(demand[t], on_hand)
        fulfilled[t] = sold
        lost[t] = demand[t] - sold
        on_hand -= sold

        if t % review_period == 0:
            # Trailing average daily demand - nothing from day t onwards.
            past = demand[max(0, t - window):t]
            avg = past.mean() if past.size else seed_mean
            target = avg * cover_days
            # The planner sizes the position using its FLAT lead-time
            # assumption, ignoring the supplier's true lead time.
            position = on_hand + outstanding - avg * nominal_lt
            qty = max(0.0, target - position)
            if qty >= 1:
                qty = float(np.ceil(qty))
                actual_lt = max(1, int(round(rng.normal(lead_time_mean, lead_time_sd))))
                receipts[t + actual_lt] += qty
                outstanding += qty
                ordered[t] = qty

        closing[t] = on_hand
        on_order[t] = outstanding

    return {
        "opening_inventory": opening, "receipts": receipts[:horizon],
        "units_fulfilled": fulfilled, "lost_units": lost,
        "closing_inventory": closing, "on_order": on_order,
        "units_ordered": ordered,
    }


def simulate_inventory_ledger(panel: pd.DataFrame, sku_attrs: pd.DataFrame,
                              cfg: Config) -> pd.DataFrame:
    """Build the daily inventory ledger for every SKU in the universe."""
    pcfg = dict(cfg["incumbent_policy"])
    seed = cfg["project"]["random_seed"]

    panel = panel.sort_values(["product_id", "invoice_date"])
    dates = np.sort(panel["invoice_date"].unique())
    attrs = sku_attrs.set_index("product_id")

    frames = []
    for i, (product_id, group) in enumerate(panel.groupby("product_id", sort=True)):
        if product_id not in attrs.index:
            continue
        row = attrs.loc[product_id]
        rng = np.random.default_rng(seed + 1000 + i)
        demand = group["demand_units"].to_numpy(dtype=float)
        result = _simulate_one_sku(
            demand, int(row["lead_time_days"]), float(row["lead_time_sd_days"]),
            pcfg, rng,
        )
        frame = pd.DataFrame(result)
        frame.insert(0, "invoice_date", group["invoice_date"].to_numpy())
        frame.insert(0, "product_id", product_id)
        frame["demand_units"] = demand
        frames.append(frame)

    ledger = pd.concat(frames, ignore_index=True)

    # Value the position and flag service failures.
    cost = attrs["unit_cost"]
    price = attrs["selling_price"]
    ledger["unit_cost"] = ledger["product_id"].map(cost)
    ledger["inventory_value"] = (ledger["closing_inventory"] * ledger["unit_cost"]).round(2)
    ledger["lost_revenue"] = (ledger["lost_units"] * ledger["product_id"].map(price)).round(2)
    ledger["is_stockout"] = (ledger["closing_inventory"] <= 0) & (ledger["demand_units"] > 0)
    ledger["is_zero_stock"] = ledger["closing_inventory"] <= 0

    # Flag the burn-in so downstream statistics can exclude it.
    warmup_end = pd.Timestamp(dates[0]) + pd.Timedelta(days=pcfg["warmup_days"])
    ledger["is_warmup"] = ledger["invoice_date"] < warmup_end
    return ledger


def build_current_snapshot(ledger: pd.DataFrame, sku_attrs: pd.DataFrame,
                           cfg: Config) -> pd.DataFrame:
    """Position of every SKU on the as-of date - the system's starting point."""
    as_of = pd.Timestamp(cfg["project"]["as_of_date"])
    snap = ledger[ledger["invoice_date"] == as_of][[
        "product_id", "closing_inventory", "on_order", "unit_cost", "inventory_value",
    ]].rename(columns={"closing_inventory": "current_inventory"})
    return sku_attrs.merge(snap, on="product_id", how="left", suffixes=("", "_snap"))


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def run(cfg: Config | None = None) -> dict[str, pd.DataFrame]:
    """Generate the supply layer and persist it."""
    cfg = cfg or load_config()
    paths = get_paths()

    dim = pd.read_parquet(paths.processed / "dim_product.parquet")
    panel = pd.read_parquet(paths.processed / "demand_daily.parquet")

    suppliers = build_supplier_master(cfg)
    sku_attrs = assign_supply_attributes(dim, cfg)
    ledger = simulate_inventory_ledger(panel, sku_attrs, cfg)
    snapshot = build_current_snapshot(ledger, sku_attrs, cfg)

    outputs = {
        "dim_supplier": suppliers,
        "dim_product_supply": sku_attrs,
        "inventory_daily": ledger,
        "inventory_snapshot": snapshot,
    }
    for name, frame in outputs.items():
        frame.to_parquet(paths.processed / f"{name}.parquet", index=False)
    return outputs


if __name__ == "__main__":  # pragma: no cover
    for key, frame in run().items():
        print(f"{key:22s} {frame.shape}")
