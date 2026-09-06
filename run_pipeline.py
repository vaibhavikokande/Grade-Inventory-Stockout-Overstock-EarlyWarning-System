"""End-to-end pipeline runner.

    python run_pipeline.py             # full rebuild
    python run_pipeline.py --skip-forecast   # reuse cached forecasts (~3 min saved)

Stages run in dependency order:

    clean -> supply -> sql(01-04) -> forecast -> metrics -> risk
          -> recommendations -> sql(05-06) -> export dashboard tables
"""

from __future__ import annotations

import argparse
import time

import pandas as pd

from src import (data_cleaning, database, forecasting, inventory_metrics,
                 recommendations, risk_scoring, supply_simulation)
from src.config import get_paths, load_config

# Tables exported to CSV for Power BI (which cannot read parquet natively
# without a gateway/connector).
POWERBI_TABLES = [
    "dim_product_bi", "dim_supplier_bi", "dim_risk_level", "dim_date",
    "fact_sku_risk", "fact_demand_weekly_bi", "fact_forecast_bi",
    "fact_forecast_accuracy_bi", "kpi_executive",
    "category_performance", "inventory_by_category", "inventory_by_supplier",
    "inventory_monthly", "demand_monthly", "stockout_by_category",
    "stockout_by_supplier", "stockout_episodes", "overstock_by_category",
    "overstock_by_supplier", "dead_stock", "working_capital_summary",
    "dq_assertions", "sku_abc",
]


def _stage(name: str, fn, *args, **kwargs):
    print(f"\n{'=' * 70}\n{name}\n{'=' * 70}")
    t0 = time.time()
    result = fn(*args, **kwargs)
    print(f"  done in {time.time() - t0:.1f}s")
    return result


def export_powerbi_tables() -> pd.DataFrame:
    """Write the dashboard star schema to dashboard/powerbi/data/ as CSV."""
    paths = get_paths()
    target = paths.powerbi / "data"
    target.mkdir(parents=True, exist_ok=True)

    con = database.connect()
    database.register_base_tables(con)
    rows = []
    for table in POWERBI_TABLES:
        try:
            df = con.execute(f"SELECT * FROM {table}").fetchdf()
        except Exception as exc:  # table not built yet
            rows.append({"table": table, "rows": 0, "columns": 0, "status": f"SKIPPED: {exc}"})
            continue
        df.to_csv(target / f"{table}.csv", index=False)
        rows.append({"table": table, "rows": len(df), "columns": df.shape[1], "status": "OK"})
    con.close()

    manifest = pd.DataFrame(rows)
    manifest.to_csv(paths.powerbi / "table_manifest.csv", index=False)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-forecast", action="store_true",
                        help="reuse the cached forecast outputs")
    parser.add_argument("--skip-clean", action="store_true",
                        help="reuse the cached cleaned data and supply layer")
    args = parser.parse_args()

    cfg = load_config()
    started = time.time()

    if not args.skip_clean:
        _stage("1/8  Data cleaning", data_cleaning.run, cfg)
        _stage("2/8  Supply-layer simulation", supply_simulation.run, cfg)

    _stage("3/8  SQL analytics (01-04)", database.build,
           ["01_data_quality.sql", "02_sales_analysis.sql",
            "03_inventory_analysis.sql", "04_stockout_analysis.sql"], cfg).close()

    if not args.skip_forecast:
        _stage("4/8  Demand forecasting + backtest", forecasting.run, cfg, False)

    _stage("5/8  Inventory metrics", inventory_metrics.run, cfg)
    _stage("6/8  Risk classification & scoring", risk_scoring.run, cfg)
    _stage("7/8  Recommendations", recommendations.run, cfg)

    _stage("8/8  SQL dashboard layer (05-06)", database.build,
           ["01_data_quality.sql", "02_sales_analysis.sql",
            "03_inventory_analysis.sql", "04_stockout_analysis.sql",
            "05_overstock_analysis.sql", "06_dashboard_tables.sql"], cfg).close()

    manifest = export_powerbi_tables()
    print(f"\n{'=' * 70}\nPower BI export\n{'=' * 70}")
    print(manifest.to_string(index=False))
    print(f"\nPipeline complete in {time.time() - started:.1f}s")


if __name__ == "__main__":
    main()
