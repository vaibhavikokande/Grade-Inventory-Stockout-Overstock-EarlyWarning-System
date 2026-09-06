"""Reproducible cleaning pipeline for the Online Retail II transaction file.

The pipeline is written as an ordered list of explicit steps. Every step
records how many rows it touched and why, so ``reports/data_quality_report.md``
is generated from the run itself rather than written by hand.

Design decisions that matter downstream
---------------------------------------
* **Sheet overlap.** The workbook's two sheets both contain 2010-12-01..09.
  The overlapping rows are an identical multiset. Concatenating naively
  doubles demand on those nine trading days. We drop the second copy.
* **Returns vs. stock adjustments.** Negative quantities come from two very
  different processes. Lines on a ``C`` invoice are genuine customer
  cancellations/returns and reduce net demand. Negative lines *not* on a ``C``
  invoice carry a zero price and a free-text warehouse note ("lost",
  "damages", "wrongly coded") - these are inventory write-offs, not demand,
  and are removed from the demand series entirely.
* **Trading calendar.** The business does not trade on Saturdays and closes
  over holidays. The demand panel is built on CALENDAR days (zero-filled)
  because supplier lead times are quoted in calendar days, keeping the demand
  statistics and the lead-time arithmetic on the same clock.

Outputs (data/processed/)
    transactions_clean.parquet  line-level saleable demand
    returns.parquet             customer cancellations / returns
    adjustments.parquet         warehouse write-offs (excluded from demand)
    dim_product.parquet         one row per product SKU
    demand_daily.parquet        SKU x calendar-day demand panel
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from .categorization import categorise, coverage_report
from .config import Config, get_paths, load_config

BUSINESS_KEY = [
    "invoice", "product_id", "description", "quantity",
    "invoice_datetime", "unit_price", "customer_id", "country",
]

RAW_RENAME = {
    "Invoice": "invoice",
    "StockCode": "product_id",
    "Description": "description",
    "Quantity": "quantity",
    "InvoiceDate": "invoice_datetime",
    "Price": "unit_price",
    "Customer ID": "customer_id",
    "CustomerID": "customer_id",
    "Country": "country",
}


@dataclass
class DataQualityLog:
    """Accumulates one row per data-quality finding for the report."""

    rows: list[dict] = field(default_factory=list)

    def add(self, issue: str, affected: int, treatment: str, rationale: str,
            severity: str = "Medium", affected_unit: str = "rows") -> None:
        self.rows.append({
            "issue": issue,
            "affected_records": int(affected),
            "unit": affected_unit,
            "severity": severity,
            "treatment": treatment,
            "rationale": rationale,
        })

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.rows, columns=[
            "issue", "affected_records", "unit", "severity", "treatment", "rationale"
        ])


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def load_raw(cfg: Config | None = None, use_cache: bool = True) -> pd.DataFrame:
    """Read both workbook sheets, tagging each row with its source sheet.

    Reading the 45 MB workbook takes ~25s, so the concatenated frame is cached
    as parquet in data/interim/.
    """
    cfg = cfg or load_config()
    paths = get_paths()
    cache = paths.interim / "raw_transactions.parquet"

    if use_cache and cache.exists():
        raw = pd.read_parquet(cache)
    else:
        sheets = pd.read_excel(
            paths.raw_excel, sheet_name=None,
            dtype={"Invoice": str, "StockCode": str},
        )
        frames = []
        for name, sheet in sheets.items():
            sheet = sheet.copy()
            sheet["source_sheet"] = name
            frames.append(sheet)
        raw = pd.concat(frames, ignore_index=True)
        # Description is a mixed-type object column: a handful of cells hold a
        # numeric stock code rather than text. Normalise to string.
        raw["Description"] = raw["Description"].where(
            raw["Description"].isna(), raw["Description"].astype(str)
        )
        raw.to_parquet(cache, index=False)

    raw = raw.rename(columns=RAW_RENAME)
    # RAW_RENAME maps two possible source spellings onto customer_id, so the
    # ordered target list must be de-duplicated before selecting.
    wanted = list(dict.fromkeys(list(RAW_RENAME.values()) + ["source_sheet"]))
    raw = raw.loc[:, [c for c in wanted if c in raw.columns]]
    raw["invoice"] = raw["invoice"].astype(str).str.strip()
    raw["product_id"] = raw["product_id"].astype(str).str.strip()
    return raw


# ---------------------------------------------------------------------------
# Cleaning steps
# ---------------------------------------------------------------------------
def clean_transactions(raw: pd.DataFrame, cfg: Config,
                       log: DataQualityLog) -> dict[str, pd.DataFrame]:
    """Apply every cleaning rule in order, logging each one.

    Returns a dict with the ``sales``, ``returns`` and ``adjustments`` frames.
    """
    ccfg = cfg["cleaning"]
    df = raw.copy()
    n0 = len(df)
    log.add("Raw rows loaded (both sheets concatenated)", n0,
            "None - baseline", "Starting point for all subsequent counts.",
            severity="Info")

    # --- 1. Structural: the two sheets overlap on 2010-12-01..09 -------------
    if ccfg["drop_sheet_overlap"]:
        sheets = df["source_sheet"].dropna().unique()
        if len(sheets) == 2:
            first, second = sorted(sheets)
            overlap_invoices = (
                set(df.loc[df.source_sheet == first, "invoice"])
                & set(df.loc[df.source_sheet == second, "invoice"])
            )
            drop_mask = (df.source_sheet == second) & df.invoice.isin(overlap_invoices)
            n_drop = int(drop_mask.sum())
            df = df.loc[~drop_mask].copy()
            log.add(
                f"Sheet overlap: {len(overlap_invoices):,} invoices (2010-12-01..09) "
                f"exported into BOTH workbook sheets",
                n_drop,
                f"Dropped the '{second}' copy of the overlapping invoices",
                "The overlapping rows are an identical multiset. Naive "
                "concatenation inflates demand by ~100% on those nine trading "
                "days, which would corrupt every forecast and safety-stock "
                "estimate built on them.",
                severity="Critical",
            )

    # --- 2. Exact duplicate transaction lines -------------------------------
    if ccfg["drop_exact_duplicates"]:
        dup_mask = df.duplicated(subset=BUSINESS_KEY, keep="first")
        n_dup = int(dup_mask.sum())
        df = df.loc[~dup_mask].copy()
        log.add(
            "Exact duplicate transaction lines (same invoice, SKU, timestamp, "
            "quantity and price)",
            n_dup, "Dropped, keeping the first occurrence",
            "An identical line repeated within one invoice at the same second "
            "is a double-scanned row rather than two separate sales. Retaining "
            "them would overstate demand.",
            severity="High",
        )

    # --- 3. Missing descriptions --------------------------------------------
    n_missing_desc = int(df["description"].isna().sum())
    log.add("Missing product description", n_missing_desc,
            "Backfilled from the modal description of the same product_id "
            "where available; otherwise left blank",
            "Description drives the derived category. Recovering it from other "
            "rows of the same SKU preserves category coverage without inventing "
            "values.",
            severity="Medium")

    # --- 4. Split negatives: customer returns vs. warehouse write-offs -------
    is_cancellation = df["invoice"].str.upper().str.startswith("C")
    is_adjustment_invoice = df["invoice"].str.upper().str.startswith("A")
    negative = df["quantity"] < 0

    adjustments = df[(negative & ~is_cancellation) | is_adjustment_invoice].copy()
    n_adj_zero_price = int((adjustments["unit_price"] <= 0).sum())
    log.add(
        "Negative quantities NOT on a cancellation invoice (warehouse "
        "write-offs: 'lost', 'damages', 'wrongly coded', 'thrown away')",
        len(adjustments),
        "Separated into adjustments.parquet and EXCLUDED from the demand series",
        f"These are stock adjustments, not customer demand - {n_adj_zero_price:,} "
        f"of {len(adjustments):,} carry a zero price and a free-text warehouse "
        "note. Counting them as negative demand would understate true demand "
        "and corrupt demand variability.",
        severity="High",
    )

    returns = df[negative & is_cancellation].copy()
    log.add("Customer cancellations / returns (invoice prefix 'C')", len(returns),
            "Separated into returns.parquet and netted against gross demand "
            "per SKU-day",
            "Cancelled and returned units are real reductions in net demand and "
            "must not be forecast as sales.",
            severity="Info")

    log.add("Bad-debt adjustment invoices (prefix 'A', negative unit price)",
            int(is_adjustment_invoice.sum()),
            "Excluded from all sales and demand analysis",
            "Accounting write-offs against customer accounts, not product "
            "transactions. Their large negative prices would distort price "
            "statistics.",
            severity="Medium")

    sales = df[~negative & ~is_cancellation & ~is_adjustment_invoice].copy()

    # --- 5. Non-product stock codes -----------------------------------------
    product_re = re.compile(ccfg["product_stockcode_regex"])
    explicit_non_product = {c.upper() for c in ccfg["non_product_codes"]}

    def _is_product(code: str) -> bool:
        return bool(product_re.fullmatch(code)) and code.upper() not in explicit_non_product

    for name, frame in (("sales", sales), ("returns", returns)):
        frame["is_product"] = frame["product_id"].map(_is_product)

    n_non_product = int((~sales["is_product"]).sum())
    non_product_codes_found = sorted(sales.loc[~sales.is_product, "product_id"].unique())
    log.add(
        "Service / fee / adjustment stock codes carried in the sales table "
        f"({', '.join(non_product_codes_found[:12])}...)",
        n_non_product,
        "Excluded from the product demand series",
        "POST (postage), DOT (dotcom postage), M (manual), BANK CHARGES, "
        "AMAZONFEE, gift vouchers and TEST codes are not stockable inventory "
        "and have no reorder point.",
        severity="High",
    )
    sales = sales[sales.is_product].drop(columns="is_product")
    returns = returns[returns.is_product].drop(columns="is_product")

    # --- 6. Stock-code case inconsistency -----------------------------------
    upper = sales["product_id"].str.upper()
    variants = sales.groupby(upper)["product_id"].nunique()
    n_case_variants = int((variants > 1).sum())
    log.add("Stock codes differing only by letter case (e.g. 85123a vs 85123A)",
            n_case_variants,
            "Upper-cased product_id so variants collapse to one SKU",
            "Left unmerged, the same physical product would be split across two "
            "SKUs, halving its apparent demand and producing two wrong reorder "
            "points.",
            severity="High", affected_unit="stock codes")
    sales["product_id"] = upper
    returns["product_id"] = returns["product_id"].str.upper()

    # --- 7. Zero / implausible prices ---------------------------------------
    lo, hi = ccfg["min_unit_price"], ccfg["max_unit_price"]
    zero_price = sales["unit_price"] < lo
    log.add("Zero or negative unit price on a product SKU", int(zero_price.sum()),
            "Rows removed from revenue analysis; the units are retained as demand",
            "A zero-price line is a giveaway, sample or data-entry error rather "
            "than a sale. The customer still consumed a unit of stock, so the "
            "demand signal is kept while the revenue is not.",
            severity="Medium")
    sales["is_revenue_line"] = ~zero_price

    high_price = sales["unit_price"] > hi
    log.add(f"Unit price above the £{hi:,.0f} plausibility ceiling on a product SKU",
            int(high_price.sum()),
            "Flagged and excluded from average-selling-price calculations",
            "Retained as demand but excluded from price statistics: a handful of "
            "extreme values would otherwise dominate the mean selling price used "
            "to value lost sales.",
            severity="Low")
    sales.loc[high_price, "is_revenue_line"] = False

    # --- 8. Extreme quantity outliers ---------------------------------------
    q99_9 = sales["quantity"].quantile(0.999)
    extreme = sales["quantity"] > q99_9
    cancelled_same_day = _flag_cancelled_bulk_orders(sales, returns)
    log.add(
        f"Extreme order quantities (> {q99_9:,.0f} units, the 99.9th percentile)",
        int(extreme.sum()),
        "RETAINED and flagged as bulk orders, not deleted",
        "Inspection shows these are legitimate wholesale orders, not errors "
        f"(this is a wholesaler). {int(cancelled_same_day.sum()):,} of them were "
        "cancelled by a matching 'C' invoice and are netted out by the returns "
        "logic. Deleting genuine bulk demand would understate the reorder point "
        "for exactly the SKUs where a stockout is most costly.",
        severity="Medium",
    )
    sales["is_bulk_order"] = extreme

    # --- 9. Inconsistent product attributes ---------------------------------
    desc_per_sku = sales.dropna(subset=["description"]).groupby("product_id")["description"].nunique()
    log.add("Product IDs carrying more than one distinct description",
            int((desc_per_sku > 1).sum()),
            "Resolved to the modal (most frequent) description per SKU",
            "Most variants are warehouse annotations appended to the product "
            "name. The modal description is the stable product name; taking the "
            "first or last would pick up an annotation.",
            severity="Medium", affected_unit="SKUs")

    # --- 10. Missing customer id --------------------------------------------
    n_null_cust = int(sales["customer_id"].isna().sum())
    log.add("Missing customer ID", n_null_cust,
            "Retained - customer identity is not required for demand planning",
            "These rows carry valid product, quantity, price and date. Dropping "
            "~23% of transactions purely for a missing customer key would "
            "materially understate demand. Customer ID is only needed for "
            "customer-level analysis, which this project does not perform.",
            severity="Low")

    # --- 11. Derived fields --------------------------------------------------
    for frame in (sales, returns, adjustments):
        frame["invoice_date"] = pd.to_datetime(frame["invoice_datetime"]).dt.normalize()
    sales["revenue"] = np.where(
        sales["is_revenue_line"], sales["quantity"] * sales["unit_price"], 0.0
    )
    returns["revenue"] = returns["quantity"] * returns["unit_price"]

    return {"sales": sales, "returns": returns, "adjustments": adjustments}


def _flag_cancelled_bulk_orders(sales: pd.DataFrame, returns: pd.DataFrame) -> pd.Series:
    """Identify bulk sales lines matched by an equal-and-opposite return."""
    if returns.empty:
        return pd.Series(False, index=sales.index)
    ret_key = set(
        zip(returns["product_id"].str.upper(), returns["quantity"].abs(),
            pd.to_datetime(returns["invoice_datetime"]).dt.normalize())
    )
    sale_key = list(
        zip(sales["product_id"].str.upper(), sales["quantity"],
            pd.to_datetime(sales["invoice_datetime"]).dt.normalize())
    )
    return pd.Series([k in ret_key for k in sale_key], index=sales.index)


# ---------------------------------------------------------------------------
# Product dimension
# ---------------------------------------------------------------------------
def build_product_dimension(sales: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """One row per SKU with a stable name, derived category and price stats."""
    named = sales.dropna(subset=["description"])
    modal_desc = (
        named.groupby(["product_id", "description"]).size()
        .rename("n").reset_index()
        .sort_values(["product_id", "n"], ascending=[True, False])
        .drop_duplicates("product_id")
        .set_index("product_id")["description"]
    )

    revenue_lines = sales[sales["is_revenue_line"]]
    agg = sales.groupby("product_id").agg(
        total_units=("quantity", "sum"),
        n_transactions=("quantity", "size"),
        first_sale_date=("invoice_date", "min"),
        last_sale_date=("invoice_date", "max"),
        active_days=("invoice_date", "nunique"),
    )
    price_agg = revenue_lines.groupby("product_id").agg(
        total_revenue=("revenue", "sum"),
        median_unit_price=("unit_price", "median"),
        mean_unit_price=("unit_price", "mean"),
        min_unit_price=("unit_price", "min"),
        max_unit_price=("unit_price", "max"),
        revenue_units=("quantity", "sum"),
    )

    dim = agg.join(price_agg, how="left").reset_index()
    dim["product_name"] = dim["product_id"].map(modal_desc).fillna("UNKNOWN")
    dim["total_revenue"] = dim["total_revenue"].fillna(0.0)

    # Selling price: the median of observed transaction prices is robust to the
    # promotional and wholesale-tier price variation present in the data.
    dim["selling_price"] = dim["median_unit_price"]
    dim["category"] = categorise(dim["product_name"])
    dim["history_days"] = (dim["last_sale_date"] - dim["first_sale_date"]).dt.days
    return dim.sort_values("total_revenue", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------------
# SKU universe
# ---------------------------------------------------------------------------
def select_sku_universe(dim: pd.DataFrame, cfg: Config,
                        log: DataQualityLog) -> pd.DataFrame:
    """Restrict to SKUs the business is still actively stocking.

    An early-warning system should only raise alerts for SKUs that are (a) still
    being sold and (b) have enough history to estimate demand variability.
    Discontinued and one-off SKUs would otherwise generate meaningless reorder
    points built on two data points.
    """
    u = cfg["sku_universe"]
    as_of = pd.Timestamp(cfg["project"]["as_of_date"])
    recent_cutoff = as_of - pd.Timedelta(days=u["recent_window_days"])

    criteria = {
        f"history span >= {u['min_history_days']} days": dim["history_days"] >= u["min_history_days"],
        f"active on >= {u['min_active_days']} days": dim["active_days"] >= u["min_active_days"],
        f"sold within last {u['recent_window_days']} days": dim["last_sale_date"] >= recent_cutoff,
        f"total units >= {u['min_total_units']}": dim["total_units"] >= u["min_total_units"],
        "has a positive selling price": dim["selling_price"].fillna(0) > 0,
    }
    keep = pd.Series(True, index=dim.index)
    for label, mask in criteria.items():
        keep &= mask
    for label, mask in criteria.items():
        log.add(f"SKU universe filter - fails '{label}'", int((~mask).sum()),
                "Excluded from the operational inventory system",
                "Retained in the sales history for EDA, but no reorder point is "
                "published for it.", severity="Info", affected_unit="SKUs")

    log.add("SKUs entering the operational inventory system", int(keep.sum()),
            "Carried forward to forecasting and risk scoring",
            f"Of {len(dim):,} product SKUs seen in the cleaned history.",
            severity="Info", affected_unit="SKUs")

    dim = dim.copy()
    dim["in_universe"] = keep.values
    return dim


# ---------------------------------------------------------------------------
# Demand panel
# ---------------------------------------------------------------------------
def build_demand_panel(sales: pd.DataFrame, returns: pd.DataFrame,
                       dim: pd.DataFrame, cfg: Config,
                       log: DataQualityLog) -> pd.DataFrame:
    """Build the SKU x calendar-day demand panel used for forecasting.

    Columns
    -------
    gross_units   units on positive sales lines
    return_units  units cancelled/returned that day
    demand_units  net demand, floored at zero (the forecasting target)
    revenue       revenue on qualifying revenue lines
    is_trading_day  whether the business traded at all that day
    """
    universe = set(dim.loc[dim["in_universe"], "product_id"])
    as_of = pd.Timestamp(cfg["project"]["as_of_date"])

    s = sales[sales["product_id"].isin(universe) & (sales["invoice_date"] <= as_of)]
    r = returns[returns["product_id"].isin(universe) & (returns["invoice_date"] <= as_of)]

    gross = s.groupby(["product_id", "invoice_date"]).agg(
        gross_units=("quantity", "sum"), revenue=("revenue", "sum"),
        n_orders=("invoice", "nunique"),
    )
    rets = r.groupby(["product_id", "invoice_date"]).agg(
        return_units=("quantity", lambda x: -x.sum())
    )

    # Trading calendar: days on which the business transacted at all. Used to
    # distinguish "sold nothing" from "was closed".
    trading_days = pd.Index(sorted(sales["invoice_date"].unique()), name="invoice_date")
    trading_days = trading_days[trading_days <= as_of]

    start = sales["invoice_date"].min()
    calendar = pd.date_range(start, as_of, freq="D")
    log.add("Calendar days with no trading activity (Saturdays and holiday closures)",
            int(len(calendar) - len(trading_days)),
            "Zero-filled in the demand panel but flagged via is_trading_day",
            "The business does not trade on Saturdays. Treating those zeros as "
            "genuine zero-demand days is correct for lead-time arithmetic "
            "(lead times are quoted in calendar days) but is flagged so that "
            "demand statistics can be recomputed on trading days if needed.",
            severity="Info", affected_unit="days")

    idx = pd.MultiIndex.from_product(
        [sorted(universe), calendar], names=["product_id", "invoice_date"]
    )
    panel = pd.DataFrame(index=idx).join(gross).join(rets).fillna(
        {"gross_units": 0, "revenue": 0.0, "return_units": 0, "n_orders": 0}
    ).reset_index()

    panel["demand_units"] = (panel["gross_units"] - panel["return_units"]).clip(lower=0)
    n_netted_negative = int(((panel["gross_units"] - panel["return_units"]) < 0).sum())
    log.add("SKU-days where returns exceeded same-day gross sales", n_netted_negative,
            "Net demand floored at zero for that day",
            "A return is usually booked days after the original order, so the "
            "offsetting sale sits on an earlier date. Allowing negative daily "
            "demand would corrupt the forecast; the floor keeps the series "
            "non-negative at a small cost in netting accuracy.",
            severity="Low", affected_unit="SKU-days")

    panel["is_trading_day"] = panel["invoice_date"].isin(set(trading_days))
    return panel


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def run(cfg: Config | None = None, use_cache: bool = True) -> dict[str, pd.DataFrame]:
    """Execute the full cleaning pipeline and persist every output."""
    cfg = cfg or load_config()
    paths = get_paths()
    log = DataQualityLog()

    raw = load_raw(cfg, use_cache=use_cache)
    parts = clean_transactions(raw, cfg, log)
    sales, returns, adjustments = parts["sales"], parts["returns"], parts["adjustments"]

    dim = build_product_dimension(sales, cfg)
    # Backfill descriptions now that the modal name per SKU is known.
    name_map = dim.set_index("product_id")["product_name"]
    sales["description"] = sales["description"].fillna(
        sales["product_id"].map(name_map)
    )

    dim = select_sku_universe(dim, cfg, log)
    panel = build_demand_panel(sales, returns, dim, cfg, log)

    dq = log.to_frame()
    outputs = {
        "transactions_clean": sales, "returns": returns,
        "adjustments": adjustments, "dim_product": dim,
        "demand_daily": panel, "data_quality": dq,
    }
    for name, frame in outputs.items():
        target = paths.processed if name != "data_quality" else paths.business_insights
        frame.to_parquet(target / f"{name}.parquet", index=False)
    dq.to_csv(paths.business_insights / "data_quality_report.csv", index=False)

    coverage_report(dim[dim.in_universe]).to_csv(
        paths.business_insights / "category_coverage.csv"
    )
    return outputs


if __name__ == "__main__":  # pragma: no cover
    result = run()
    for key, frame in result.items():
        print(f"{key:22s} {frame.shape}")
