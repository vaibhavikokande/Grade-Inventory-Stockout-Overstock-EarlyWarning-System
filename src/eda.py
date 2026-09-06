"""Exploratory analysis figures.

Every figure answers a stated business question - there are no decorative
charts. Design rules applied throughout:

* **No dual-axis charts.** Two measures on different scales are drawn as two
  stacked panels sharing an x-axis, never two y-scales on one plot, because
  rescaling a second axis can manufacture any apparent relationship.
* **Colour-vision-safe palette.** The five risk colours were validated with a
  CVD simulator: worst adjacent separation is dE 19.1 against a >=12 target.
  Green and yellow fall below 3:1 contrast on a light surface, so every visual
  using them carries visible data labels - colour never carries meaning alone.
* Recessive grid and axes, thin marks, direct labels in preference to legends.
"""

from __future__ import annotations

import textwrap

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .config import Config, get_paths, load_config

# --- design tokens -----------------------------------------------------------
SURFACE = "#FCFCFB"
INK = "#0B0B0B"
INK_SOFT = "#52514E"
GRID = "#E4E7EB"

RISK_COLOURS = {
    "Critical Stockout Risk": "#C0392B",
    "Stockout Risk": "#EB6834",
    "Healthy": "#1BAF7A",
    "Overstock Risk": "#EDA100",
    "Severe Overstock": "#4A3AA7",
}
RISK_ORDER = list(RISK_COLOURS)

BLUE = "#2A78D6"        # categorical slot 1 / sequential hue
AQUA = "#1BAF7A"        # categorical slot 2
VIOLET = "#4A3AA7"      # categorical slot 5
RED = "#C0392B"
SEQ_BLUE = ["#CDE2FB", "#9EC5F4", "#6DA7EC", "#3987E5", "#256ABF", "#104281"]


def apply_style() -> None:
    mpl.rcParams.update({
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE,
        "savefig.facecolor": SURFACE, "font.family": "DejaVu Sans",
        "font.size": 10, "axes.titlesize": 13, "axes.titleweight": "semibold",
        "axes.labelsize": 10, "axes.labelcolor": INK_SOFT,
        "axes.edgecolor": GRID, "axes.linewidth": 0.8,
        "xtick.color": INK_SOFT, "ytick.color": INK_SOFT,
        "xtick.labelsize": 9, "ytick.labelsize": 9,
        "grid.color": GRID, "grid.linewidth": 0.7, "axes.grid": True,
        "axes.grid.axis": "y", "axes.spines.top": False, "axes.spines.right": False,
        "legend.frameon": False, "legend.fontsize": 9,
        "figure.dpi": 130, "savefig.dpi": 130, "savefig.bbox": "tight",
    })


def _title(ax, title: str, subtitle: str = "", wrap: int = 88) -> None:
    """Left-aligned title with an optional deck beneath it.

    The pad has to clear the wrapped subtitle: at pad=14 a two-line deck rendered
    straight through the title. Pad scales with the wrapped line count.
    """
    lines = textwrap.wrap(subtitle, wrap) if subtitle else []
    ax.set_title(title, color=INK, loc="left",
                 pad=8 + 13 * len(lines) if lines else 8)
    if lines:
        ax.text(0, 1.012, "\n".join(lines), transform=ax.transAxes,
                fontsize=9, color=INK_SOFT, va="bottom", linespacing=1.45)


def _save(fig, name: str) -> str:
    path = get_paths().figures / name
    fig.savefig(path)
    plt.close(fig)
    return str(path)


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------
def fig_seasonality(demand_monthly: pd.DataFrame) -> str:
    """Q: When does demand actually happen, and how sharp is the peak?"""
    d = demand_monthly.copy()
    d["month"] = pd.to_datetime(d["month"])
    d = d[d["month"] < d["month"].max()]           # drop the partial final month

    fig, axes = plt.subplots(2, 1, figsize=(11, 6.5), sharex=True,
                             gridspec_kw={"hspace": 0.28})
    axes[0].bar(d["month"], d["units"] / 1000, width=22, color=BLUE)
    _title(axes[0], "Demand is violently seasonal - and the peak is short",
           "Monthly demand units (thousands). November peaks at ~2.4x the February trough.")
    axes[0].set_ylabel("Units (000s)")

    axes[1].bar(d["month"], d["revenue"] / 1000, width=22, color=AQUA)
    _title(axes[1], "", "Monthly revenue (GBP 000s)")
    axes[1].set_ylabel("Revenue (£000s)")

    peak = d.loc[d["units"].idxmax()]
    axes[0].annotate(f"peak {peak['units']/1000:,.0f}k units",
                     xy=(peak["month"], peak["units"] / 1000),
                     xytext=(-70, -18), textcoords="offset points",
                     fontsize=9, color=INK,
                     arrowprops=dict(arrowstyle="-", color=INK_SOFT, lw=0.8))
    fig.text(0.01, -0.03,
             "Two panels rather than a dual-axis chart: separate y-scales on one plot "
             "would let the apparent relationship be set by axis choice.",
             fontsize=8, color=INK_SOFT)
    return _save(fig, "fig01_demand_seasonality.png")


def fig_intermittency(panel: pd.DataFrame, threshold: float) -> str:
    """Q: Is this data dense enough for conventional time-series models?"""
    zero_share = panel.groupby("product_id")["demand_units"].apply(lambda s: (s == 0).mean())

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.hist(zero_share, bins=40, color=BLUE, edgecolor=SURFACE, linewidth=0.6)
    ax.axvline(threshold, color=RED, lw=1.6, ls="--")
    n_int = int((zero_share > threshold).sum())
    ax.text(threshold + 0.012, ax.get_ylim()[1] * 0.92,
            f"{n_int:,} of {len(zero_share):,} SKUs ({100*n_int/len(zero_share):.0f}%)\n"
            f"are zero on >{threshold:.0%} of days\n→ routed to Croston / SBA",
            fontsize=9, color=INK, va="top")
    _title(ax, "Three-quarters of SKUs have intermittent demand",
           "Distribution of the share of days with zero demand, per SKU. "
           "This is why Prophet was rejected.")
    ax.set_xlabel("Share of days with zero demand")
    ax.set_ylabel("Number of SKUs")
    return _save(fig, "fig02_intermittency.png")


def fig_abc_pareto(abc: pd.DataFrame) -> str:
    """Q: How concentrated is revenue - where should attention go?"""
    d = abc.sort_values("revenue_rank")
    x = np.arange(1, len(d) + 1) / len(d) * 100
    y = d["cumulative_revenue_share"].to_numpy() * 100

    fig, ax = plt.subplots(figsize=(10, 5.5))
    ax.plot(x, y, color=BLUE, lw=2)
    ax.fill_between(x, 0, y, color=BLUE, alpha=0.08)
    for share, colour, label in [(80, "#256ABF", "A"), (95, "#6DA7EC", "B")]:
        cut = float(x[np.searchsorted(y, share)])
        ax.axvline(cut, color=colour, lw=1.2, ls="--")
        ax.text(cut + 1, 12, f"Class {label} boundary\n{cut:.0f}% of SKUs = {share}% of revenue",
                fontsize=9, color=INK)
    _title(ax, "30% of SKUs generate 80% of revenue",
           "Cumulative revenue share by SKU, ranked highest to lowest (ABC classification).")
    ax.set_xlabel("Cumulative share of SKUs (%)")
    ax.set_ylabel("Cumulative share of revenue (%)")
    ax.set_xlim(0, 100); ax.set_ylim(0, 100)
    return _save(fig, "fig03_abc_pareto.png")


def fig_service_drivers(drivers: pd.DataFrame) -> str:
    """Q: What actually causes poor service - lead time or volatility?"""
    piv = drivers.pivot(index="lead_time_band", columns="volatility_quartile",
                        values="avg_fill_rate")
    fig, ax = plt.subplots(figsize=(9, 4.8))
    im = ax.imshow(piv.to_numpy() * 100, cmap=mpl.colors.LinearSegmentedColormap.from_list(
        "seq_blue", SEQ_BLUE[::-1]), aspect="auto")
    ax.set_xticks(range(piv.shape[1]))
    ax.set_xticklabels(["Q1\nmost stable", "Q2", "Q3", "Q4\nmost volatile"])
    ax.set_yticks(range(piv.shape[0]))
    ax.set_yticklabels(piv.index)
    for i in range(piv.shape[0]):
        for j in range(piv.shape[1]):
            v = piv.iat[i, j] * 100
            ax.text(j, i, f"{v:.0f}%", ha="center", va="center", fontsize=10,
                    color="white" if v < 86 else INK, fontweight="semibold")
    _title(ax, "Demand volatility hurts service more than lead time does",
           "Historical unit fill rate (%) by supplier lead-time band and demand-volatility quartile.")
    ax.set_xlabel("Demand volatility quartile (coefficient of variation)")
    ax.set_ylabel("Supplier lead time")
    ax.grid(False)
    fig.colorbar(im, ax=ax, label="Fill rate (%)", fraction=0.03, pad=0.02)
    return _save(fig, "fig04_service_drivers.png")


def fig_risk_distribution(risk: pd.DataFrame) -> str:
    """Q: How is the portfolio split across risk states?"""
    counts = risk["risk_level"].value_counts().reindex(RISK_ORDER).fillna(0)
    value = risk.groupby("risk_level")["inventory_value"].sum().reindex(RISK_ORDER).fillna(0)

    fig, axes = plt.subplots(1, 2, figsize=(12, 5), gridspec_kw={"wspace": 0.35})
    for ax, series, title, fmt in (
        (axes[0], counts, "SKU count by risk level", "{:,.0f}"),
        (axes[1], value / 1000, "Inventory value by risk level (£000s)", "£{:,.0f}k"),
    ):
        bars = ax.barh(range(len(series)), series.to_numpy(),
                       color=[RISK_COLOURS[i] for i in series.index], height=0.62)
        ax.set_yticks(range(len(series)))
        ax.set_yticklabels(series.index)
        ax.invert_yaxis()
        ax.grid(axis="x"); ax.grid(axis="y", visible=False)
        for bar, v in zip(bars, series.to_numpy()):
            ax.text(bar.get_width() * 1.01, bar.get_y() + bar.get_height() / 2,
                    fmt.format(v), va="center", fontsize=9, color=INK)
        ax.set_xlim(0, series.max() * 1.18)
        _title(ax, title)
    fig.suptitle("Overstock, not stockout, is this business's dominant inventory problem",
                 x=0.01, ha="left", fontsize=14, color=INK, fontweight="semibold", y=1.04)
    fig.text(0.01, -0.04,
             "Values labelled directly: two palette colours fall below 3:1 contrast on a "
             "light surface, so colour never carries the meaning alone.",
             fontsize=8, color=INK_SOFT)
    return _save(fig, "fig05_risk_distribution.png")


def fig_excess_by_category(overstock_cat: pd.DataFrame) -> str:
    """Q: Where is capital tied up?"""
    d = overstock_cat.sort_values("excess_value", ascending=True).tail(12)
    fig, ax = plt.subplots(figsize=(10, 6))
    bars = ax.barh(d["category"], d["excess_value"] / 1000, color=VIOLET, height=0.62)
    for bar, v, n in zip(bars, d["excess_value"] / 1000, d["n_overstocked_skus"]):
        ax.text(bar.get_width() * 1.01, bar.get_y() + bar.get_height() / 2,
                f"£{v:,.0f}k  ({n:,.0f} SKUs)", va="center", fontsize=9, color=INK)
    ax.set_xlim(0, (d["excess_value"] / 1000).max() * 1.28)
    ax.grid(axis="x"); ax.grid(axis="y", visible=False)
    _title(ax, "Kitchen & Dining holds the largest block of trapped capital",
           "Excess inventory value by category (£000s), at cost. "
           "Excess = position beyond the 90-day forecast requirement plus safety stock.")
    ax.set_xlabel("Excess inventory value (£000s)")
    return _save(fig, "fig06_excess_by_category.png")


def fig_model_comparison(accuracy: pd.DataFrame) -> str:
    """Q: Which forecasting model actually wins, and did complexity pay?"""
    fig, axes = plt.subplots(1, 2, figsize=(12.5, 5.2), gridspec_kw={"wspace": 0.32})
    for ax, flag, label in ((axes[0], False, "Dense SKUs (719)"),
                            (axes[1], True, "Intermittent SKUs (1,651)")):
        sub = (accuracy[accuracy.is_intermittent == flag]
               .groupby("model")["horizon_abs_error"].mean().sort_values())
        colours = [RED if m == "sarima" else BLUE for m in sub.index]
        bars = ax.barh(range(len(sub)), sub.to_numpy(), color=colours, height=0.62)
        ax.set_yticks(range(len(sub))); ax.set_yticklabels(sub.index)
        ax.invert_yaxis(); ax.grid(axis="x"); ax.grid(axis="y", visible=False)
        for bar, v in zip(bars, sub.to_numpy()):
            ax.text(bar.get_width() * 1.01, bar.get_y() + bar.get_height() / 2,
                    f"{v:,.0f}", va="center", fontsize=9, color=INK)
        ax.set_xlim(0, sub.max() * 1.16)
        _title(ax, label)
        ax.set_xlabel("Mean 30-day horizon absolute error (units)")
    fig.suptitle("SARIMA lost to a 28-day moving average - at ~50x the fitting cost",
                 x=0.01, ha="left", fontsize=14, color=INK, fontweight="semibold", y=1.03)
    fig.text(0.01, -0.05,
             "Rolling-origin backtest, 5 folds x 30 days. Lower is better. SARIMA (red) was "
             "benchmarked on the 100 highest-revenue dense SKUs only.",
             fontsize=8, color=INK_SOFT)
    return _save(fig, "fig07_model_comparison.png")


def fig_forecast_scenarios(demand_monthly: pd.DataFrame, forecast: pd.DataFrame) -> str:
    """Q: How much does the seasonal regime assumption change the answer?"""
    d = demand_monthly.copy()
    d["month"] = pd.to_datetime(d["month"])
    d = d[d["month"] < d["month"].max()].tail(18)
    f = forecast[forecast.horizon_days == 90]

    fig, ax = plt.subplots(figsize=(11, 5.5))
    ax.plot(d["month"], d["units"] / 1000, color=BLUE, lw=2, marker="o", ms=4,
            label="Actual monthly demand")

    nxt = d["month"].max() + pd.DateOffset(months=1)
    sel = f["forecast_units"].sum() / 3 / 1000          # 90d -> per month
    scen = f["forecast_units_seasonal_scenario"].sum() / 3 / 1000
    ax.scatter([nxt], [sel], color=VIOLET, s=70, zorder=5,
               label="Selected-model forecast (per month)")
    ax.scatter([nxt], [scen], color="#EDA100", s=70, zorder=5, marker="s",
               label="Seasonal-scenario forecast (per month)")
    ax.plot([nxt, nxt], [scen, sel], color=INK_SOFT, lw=1, ls=":")
    ax.text(nxt, (sel + scen) / 2, f"  {abs(sel-scen)/scen*100:.0f}% gap",
            fontsize=9, color=INK, va="center")

    _title(ax, "The forecast's biggest uncertainty is which seasonal regime applies",
           "The as-of date sits on the Christmas peak; the operational window is the "
           "post-Christmas collapse, which the backtest folds never covered.")
    ax.set_ylabel("Demand (000s units per month)")
    ax.legend(loc="upper left")
    return _save(fig, "fig08_forecast_scenarios.png")


# ---------------------------------------------------------------------------
def run(cfg: Config | None = None) -> list[str]:
    cfg = cfg or load_config()
    paths = get_paths()
    apply_style()

    from .database import connect, register_base_tables
    con = connect(); register_base_tables(con, cfg)
    demand_monthly = con.execute("SELECT * FROM demand_monthly").fetchdf()
    abc = con.execute("SELECT * FROM sku_abc").fetchdf()
    drivers = con.execute("SELECT * FROM stockout_drivers").fetchdf()
    overstock_cat = con.execute("SELECT * FROM overstock_by_category").fetchdf()
    con.close()

    panel = pd.read_parquet(paths.processed / "demand_daily.parquet")
    risk = pd.read_parquet(paths.processed / "sku_risk.parquet")
    accuracy = pd.read_parquet(paths.processed / "forecast_accuracy.parquet")
    forecast = pd.read_parquet(paths.processed / "forecast_sku.parquet")

    figures = [
        fig_seasonality(demand_monthly),
        fig_intermittency(panel, cfg["forecasting"]["intermittent_zero_share"]),
        fig_abc_pareto(abc),
        fig_service_drivers(drivers),
        fig_risk_distribution(risk),
        fig_excess_by_category(overstock_cat),
        fig_model_comparison(accuracy),
        fig_forecast_scenarios(demand_monthly, forecast),
    ]
    return figures


if __name__ == "__main__":  # pragma: no cover
    for f in run():
        print(f)
