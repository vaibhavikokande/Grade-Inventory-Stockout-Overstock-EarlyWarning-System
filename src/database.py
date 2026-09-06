"""DuckDB analytical layer.

The processed parquet files are registered as base tables in a DuckDB file, and
the numbered scripts in ``sql/`` build the analytical tables on top of them.
DuckDB is used rather than SQLite/Postgres because it reads parquet natively,
runs the window functions this analysis needs, and keeps the whole warehouse in
a single committable file - so a reviewer can clone the repo and query it.
"""

from __future__ import annotations

import re
from pathlib import Path

import duckdb
import pandas as pd

from .config import PROJECT_ROOT, Config, get_paths, load_config

# Processed parquet files registered as base tables, in dependency order.
BASE_TABLES = {
    "transactions_clean": "transactions_clean.parquet",
    "returns": "returns.parquet",
    "adjustments": "adjustments.parquet",
    "dim_product": "dim_product.parquet",
    "dim_supplier": "dim_supplier.parquet",
    "dim_product_supply": "dim_product_supply.parquet",
    "demand_daily": "demand_daily.parquet",
    "inventory_daily": "inventory_daily.parquet",
    "inventory_snapshot": "inventory_snapshot.parquet",
}

# Optional tables produced by later pipeline stages.
OPTIONAL_TABLES = {
    "forecast_sku": "forecast_sku.parquet",
    "forecast_accuracy": "forecast_accuracy.parquet",
    "forecast_selection": "forecast_selection.parquet",
    "inventory_metrics": "inventory_metrics.parquet",
    "sku_risk": "sku_risk.parquet",
    "recommendations": "recommendations.parquet",
}


def connect(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    paths = get_paths()
    paths.duckdb.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(paths.duckdb), read_only=read_only)


def register_base_tables(con: duckdb.DuckDBPyConnection,
                         cfg: Config | None = None) -> list[str]:
    """(Re)create a view over every processed parquet file that exists."""
    cfg = cfg or load_config()
    paths = get_paths()
    created = []
    for table, filename in {**BASE_TABLES, **OPTIONAL_TABLES}.items():
        path = paths.processed / filename
        if not path.exists():
            continue
        con.execute(f"CREATE OR REPLACE VIEW {table} AS SELECT * FROM read_parquet('{path}')")
        created.append(table)

    # Config values the SQL layer needs, exposed as a one-row table so the
    # scripts never hardcode a business constant.
    con.execute("CREATE OR REPLACE TABLE params AS SELECT "
                f"DATE '{cfg['project']['as_of_date']}' AS as_of_date, "
                f"{cfg['incumbent_policy']['warmup_days']} AS warmup_days, "
                f"{cfg['inventory_policy']['planning_horizon_days']} AS planning_horizon_days, "
                f"{cfg['inventory_policy']['review_period_days']} AS review_period_days, "
                f"{cfg['inventory_policy']['service_level']} AS service_level")
    return created


def split_statements(sql: str) -> list[str]:
    """Split a script on semicolons, ignoring those inside strings/comments."""
    cleaned = re.sub(r"--[^\n]*", "", sql)
    return [s.strip() for s in cleaned.split(";") if s.strip()]


def run_script(con: duckdb.DuckDBPyConnection, path: Path) -> None:
    """Execute every statement in one .sql file."""
    for statement in split_statements(path.read_text(encoding="utf-8")):
        con.execute(statement)


def build(scripts: list[str] | None = None,
          cfg: Config | None = None) -> duckdb.DuckDBPyConnection:
    """Rebuild the warehouse: register base tables, then run the SQL scripts."""
    cfg = cfg or load_config()
    con = connect()
    register_base_tables(con, cfg)

    sql_dir = PROJECT_ROOT / "sql"
    files = (
        [sql_dir / s for s in scripts] if scripts
        else sorted(sql_dir.glob("*.sql"))
    )
    for path in files:
        if path.exists():
            run_script(con, path)
    return con


def query(sql: str, cfg: Config | None = None) -> pd.DataFrame:
    """Convenience helper: open, register, query, close."""
    con = connect(read_only=False)
    register_base_tables(con, cfg)
    try:
        return con.execute(sql).fetchdf()
    finally:
        con.close()


if __name__ == "__main__":  # pragma: no cover
    connection = build()
    tables = connection.execute(
        "SELECT table_name, table_type FROM information_schema.tables ORDER BY 2, 1"
    ).fetchdf()
    print(tables.to_string(index=False))
    connection.close()
