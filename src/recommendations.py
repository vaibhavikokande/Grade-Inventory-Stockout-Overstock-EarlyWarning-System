"""Action recommendation engine.

Every recommendation is generated from computed metrics, never from a hand-
written list. Each row carries the numbers that produced it, so a planner can
audit the advice instead of trusting it:

    action_type       what to do (Expedite Reorder, Markdown, Liquidate, ...)
    action_detail     the instruction, with quantities and dates filled in
    action_rationale  which metrics triggered it
    action_value_gbp  the money at stake

The rules are ordered from most to least urgent and the first match wins, so a
SKU receives exactly one action.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import Config, get_paths, load_config
from .risk_scoring import (CRITICAL_STOCKOUT, OVERSTOCK, SEVERE_OVERSTOCK,
                           STOCKOUT)

# Lead time above which a shortage cannot be fixed inside a normal cycle and
# needs escalation (air freight, partial shipment, alternate supplier).
LONG_LEAD_TIME_DAYS = 30


def _fmt(n: float) -> str:
    return f"{n:,.0f}"


def _money(n: float) -> str:
    return f"£{n:,.0f}"


def recommend_row(r: pd.Series) -> dict:
    """Return the single action for one SKU."""
    lead = int(r["lead_time_days"])
    qty = float(r.get("recommended_order_qty", 0) or 0)
    cover_days = r.get("days_of_cover_forecast")
    cover_txt = (f"{cover_days:,.0f} days of cover"
                 if pd.notna(cover_days) and np.isfinite(cover_days)
                 else "no forecast demand")

    # ---------------- Stockout side ----------------
    if r["risk_level"] == CRITICAL_STOCKOUT:
        if lead >= LONG_LEAD_TIME_DAYS:
            return {
                "action_type": "Expedite Reorder",
                "action_priority": "P1 - Immediate",
                "action_detail": (
                    f"Expedite {_fmt(qty)} units. Supplier lead time is {lead} days, "
                    f"so a standard order will not arrive before stock runs out. "
                    f"Escalate with {r['supplier_name']} or split the shipment by air."
                ),
                "action_rationale": (
                    f"Inventory position {_fmt(r['inventory_position'])} is at "
                    f"{r['cover_ratio']:.0%} of the reorder point "
                    f"({_fmt(r['reorder_point'])}) on a {lead}-day lead time."
                ),
                "action_value_gbp": float(r["estimated_lost_revenue"]),
            }
        return {
            "action_type": "Reorder Now",
            "action_priority": "P1 - Immediate",
            "action_detail": (
                f"Raise a purchase order for {_fmt(qty)} units with "
                f"{r['supplier_name']} today (lead time {lead} days)."
            ),
            "action_rationale": (
                f"Inventory position {_fmt(r['inventory_position'])} is at "
                f"{r['cover_ratio']:.0%} of the reorder point "
                f"({_fmt(r['reorder_point'])})."
            ),
            "action_value_gbp": float(r["estimated_lost_revenue"]),
        }

    if r["risk_level"] == STOCKOUT:
        return {
            "action_type": "Reorder",
            "action_priority": "P2 - This week",
            "action_detail": (
                f"Order {_fmt(qty)} units at the next weekly review "
                f"(supplier {r['supplier_name']}, lead time {lead} days)."
            ),
            "action_rationale": (
                f"Inventory position {_fmt(r['inventory_position'])} has fallen "
                f"below the reorder point {_fmt(r['reorder_point'])}; safety "
                f"stock is {_fmt(r['safety_stock'])} units."
            ),
            "action_value_gbp": float(r["estimated_lost_revenue"]),
        }

    # ---------------- Overstock side ----------------
    if r.get("is_dead_stock", False):
        return {
            "action_type": "Liquidate",
            "action_priority": "P2 - This month",
            "action_detail": (
                f"Clear {_fmt(r['current_inventory'])} units "
                f"({_money(r['inventory_value'])} at cost) through clearance, "
                f"bundling or write-off. Do not reorder."
            ),
            "action_rationale": (
                f"90-day forecast demand is {r['forecast_units_90d']:.0f} units "
                f"against {_fmt(r['current_inventory'])} on hand - the SKU has "
                f"effectively stopped selling."
            ),
            "action_value_gbp": float(r["inventory_value"]),
        }

    if r["risk_level"] == SEVERE_OVERSTOCK:
        return {
            "action_type": "Markdown / Clearance",
            "action_priority": "P2 - This month",
            "action_detail": (
                f"Run a clearance or promotion on {_fmt(r['excess_units'])} excess "
                f"units ({_money(r['excess_value'])} of capital). Suspend all "
                f"replenishment from {r['supplier_name']} for this SKU."
            ),
            "action_rationale": (
                f"Inventory position {_fmt(r['inventory_position'])} is "
                f"{r['excess_ratio_capped']:.1f}x the 90-day requirement "
                f"({_fmt(r['expected_requirement'])}); {cover_txt}."
            ),
            "action_value_gbp": float(r["excess_value"]),
        }

    if r["risk_level"] == OVERSTOCK:
        return {
            "action_type": "Reduce Replenishment",
            "action_priority": "P3 - Next cycle",
            "action_detail": (
                f"Pause or halve the next order. {_fmt(r['excess_units'])} excess "
                f"units are tying up {_money(r['excess_value'])}."
            ),
            "action_rationale": (
                f"Inventory position is {r['excess_ratio_capped']:.1f}x the 90-day "
                f"requirement; {cover_txt}."
            ),
            "action_value_gbp": float(r["excess_value"]),
        }

    return {
        "action_type": "No Action",
        "action_priority": "P4 - Monitor",
        "action_detail": "Inventory is within the target operating range.",
        "action_rationale": (
            f"Position {_fmt(r['inventory_position'])} sits between the reorder "
            f"point ({_fmt(r['reorder_point'])}) and the 90-day requirement "
            f"({_fmt(r['expected_requirement'])})."
        ),
        "action_value_gbp": 0.0,
    }


def build(risk: pd.DataFrame) -> pd.DataFrame:
    actions = pd.DataFrame([recommend_row(r) for _, r in risk.iterrows()],
                           index=risk.index)
    cols = [
        "product_id", "product_name", "category", "supplier_name", "sourcing_region",
        "abc_class", "risk_level", "risk_emoji", "risk_family", "priority_score",
        "priority_rank", "is_actionable", "is_dead_stock", "current_inventory",
        "on_order", "inventory_position", "inventory_value", "safety_stock",
        "reorder_point", "expected_requirement", "cover_ratio", "excess_ratio_capped",
        "excess_units", "excess_value", "estimated_shortfall_units",
        "estimated_lost_revenue", "recommended_order_qty", "order_cost",
        "lead_time_days", "days_of_cover_forecast", "forecast_units_30d",
        "forecast_units_90d", "selected_model", "selling_price", "unit_cost",
    ]
    out = pd.concat([risk[[c for c in cols if c in risk.columns]], actions], axis=1)
    return out.sort_values(["is_actionable", "priority_score"], ascending=[False, False])


def top_actions(recs: pd.DataFrame, n: int = 10) -> pd.DataFrame:
    """The n highest-value actions across both risk families - the exec list."""
    a = recs[recs["is_actionable"]].copy()
    return a.nlargest(n, "action_value_gbp")[[
        "product_id", "product_name", "risk_level", "action_type",
        "action_priority", "action_detail", "action_value_gbp", "priority_score",
    ]]


def run(cfg: Config | None = None) -> dict[str, pd.DataFrame]:
    cfg = cfg or load_config()
    paths = get_paths()
    risk = pd.read_parquet(paths.processed / "sku_risk.parquet")
    recs = build(risk)
    recs.to_parquet(paths.processed / "recommendations.parquet", index=False)
    recs.to_csv(paths.risk_tables / "recommendations.csv", index=False)

    summary = recs.groupby(["action_type", "action_priority"], observed=False).agg(
        n_skus=("product_id", "size"),
        total_value_gbp=("action_value_gbp", "sum"),
    ).round(2).sort_values("total_value_gbp", ascending=False).reset_index()
    summary.to_csv(paths.business_insights / "action_summary.csv", index=False)

    top = top_actions(recs, 10)
    top.to_csv(paths.business_insights / "top_10_actions.csv", index=False)
    return {"recommendations": recs, "action_summary": summary, "top_10_actions": top}


if __name__ == "__main__":  # pragma: no cover
    for key, frame in run().items():
        print(f"{key:22s} {frame.shape}")
