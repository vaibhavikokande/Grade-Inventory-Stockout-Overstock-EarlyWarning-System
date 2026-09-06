"""Render analysis artifacts to Markdown.

Reports are generated from the pipeline's own outputs so that every number in
reports/ is traceable to a run rather than typed by hand.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from .config import get_paths

SEVERITY_ORDER = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3, "Info": 4}


def _md_table(df: pd.DataFrame, floatfmt: str = "{:,.2f}") -> str:
    """Render a DataFrame as a GitHub-flavoured Markdown table."""
    out = df.copy()
    for col in out.columns:
        if pd.api.types.is_float_dtype(out[col]):
            out[col] = out[col].map(lambda v: floatfmt.format(v) if pd.notna(v) else "")
        elif pd.api.types.is_integer_dtype(out[col]):
            out[col] = out[col].map(lambda v: f"{v:,}")
    header = "| " + " | ".join(str(c) for c in out.columns) + " |"
    rule = "| " + " | ".join("---" for _ in out.columns) + " |"
    rows = [
        "| " + " | ".join(str(v).replace("|", "\\|") for v in rec) + " |"
        for rec in out.itertuples(index=False)
    ]
    return "\n".join([header, rule, *rows])


def render_data_quality_report(dq: pd.DataFrame, extra_sections: dict[str, str] | None = None) -> str:
    """Build reports/data_quality_report.md from the pipeline's DQ log."""
    dq = dq.copy()
    dq["_order"] = dq["severity"].map(SEVERITY_ORDER).fillna(9)
    dq = dq.sort_values(["_order", "affected_records"], ascending=[True, False]).drop(columns="_order")

    lines = [
        "# Data Quality Report",
        "",
        f"_Generated {datetime.now():%Y-%m-%d %H:%M} from `src/data_cleaning.py`._",
        "",
        "Every row below was produced by the cleaning pipeline itself. The",
        "`affected_records` column is counted at run time, so re-running the",
        "pipeline regenerates this report.",
        "",
        "## Severity key",
        "",
        "| Severity | Meaning |",
        "| --- | --- |",
        "| Critical | Would materially corrupt demand, forecasts or inventory maths if untreated |",
        "| High | Distorts SKU-level demand or the product master |",
        "| Medium | Affects a specific metric but not the core demand series |",
        "| Low | Cosmetic or low-volume |",
        "| Info | Recorded for traceability; no defect |",
        "",
        "## Findings",
        "",
    ]

    for severity in ["Critical", "High", "Medium", "Low", "Info"]:
        block = dq[dq.severity == severity]
        if block.empty:
            continue
        lines += [f"### {severity}", ""]
        for rec in block.itertuples(index=False):
            lines += [
                f"**{rec.issue}**",
                "",
                f"- **Affected:** {rec.affected_records:,} {rec.unit}",
                f"- **Treatment:** {rec.treatment}",
                f"- **Rationale:** {rec.rationale}",
                "",
            ]

    lines += ["## Summary table", "", _md_table(
        dq[["severity", "issue", "affected_records", "unit", "treatment"]]
    ), ""]

    for title, body in (extra_sections or {}).items():
        lines += [f"## {title}", "", body, ""]

    return "\n".join(lines)


def write_data_quality_report(dq: pd.DataFrame,
                              extra_sections: dict[str, str] | None = None) -> str:
    paths = get_paths()
    text = render_data_quality_report(dq, extra_sections)
    target = paths.reports / "data_quality_report.md"
    target.write_text(text, encoding="utf-8")
    return str(target)
