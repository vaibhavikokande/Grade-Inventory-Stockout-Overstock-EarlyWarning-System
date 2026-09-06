"""Configuration loading and project path resolution.

Every module reads its constants from ``config/config.yaml`` through this
helper so that a single edit to the YAML changes the whole pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"


class Config(dict):
    """Dict with attribute access and dotted-path lookup."""

    def __getattr__(self, item: str) -> Any:
        try:
            value = self[item]
        except KeyError as exc:  # pragma: no cover - defensive
            raise AttributeError(item) from exc
        return Config(value) if isinstance(value, dict) else value

    def get_path(self, dotted: str, default: Any = None) -> Any:
        """Look up ``a.b.c`` and return ``default`` when any level is missing."""
        node: Any = self
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node


@lru_cache(maxsize=1)
def load_config(path: Path | str = CONFIG_PATH) -> Config:
    """Load and cache the YAML configuration."""
    with open(path, "r", encoding="utf-8") as fh:
        return Config(yaml.safe_load(fh))


@dataclass(frozen=True)
class Paths:
    """Absolute paths for every directory the pipeline writes to."""

    root: Path
    raw_excel: Path
    interim: Path
    processed: Path
    outputs: Path
    reports: Path
    duckdb: Path
    figures: Path
    forecasts: Path
    risk_tables: Path
    business_insights: Path
    powerbi: Path

    def ensure(self) -> "Paths":
        """Create every output directory if it does not already exist."""
        for directory in (
            self.interim, self.processed, self.outputs, self.reports,
            self.figures, self.forecasts, self.risk_tables,
            self.business_insights, self.powerbi,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        return self


@lru_cache(maxsize=1)
def get_paths() -> Paths:
    """Resolve all configured paths against the project root."""
    cfg = load_config()
    p = cfg["paths"]
    root = PROJECT_ROOT
    outputs = root / p["outputs"]
    return Paths(
        root=root,
        raw_excel=root / p["raw_excel"],
        interim=root / p["interim"],
        processed=root / p["processed"],
        outputs=outputs,
        reports=root / p["reports"],
        duckdb=root / p["duckdb"],
        figures=outputs / "figures",
        forecasts=outputs / "forecasts",
        risk_tables=outputs / "risk_tables",
        business_insights=outputs / "business_insights",
        powerbi=root / "dashboard" / "powerbi",
    ).ensure()
