"""Load config/model.yaml and resolve its relative paths against the file's folder."""
from __future__ import annotations

from pathlib import Path

import yaml


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _read(path: Path) -> dict:
    """YAML with optional ``extends: other.yaml`` (relative to this file): this file's keys override the other's."""
    cfg = yaml.safe_load(path.read_text()) or {}
    parent = cfg.pop("extends", None)
    return _merge(_read((path.parent / parent).resolve()), cfg) if parent else cfg


def load_config(path: str | Path) -> dict:
    path = Path(path).resolve()
    cfg = _read(path)
    root = path.parent
    cfg["_path"], cfg["_root"] = str(path), str(root)

    def rel(p):
        p = Path(p)
        return str(p if p.is_absolute() else (root / p).resolve())

    cfg["portfolio"]["file"] = rel(cfg["portfolio"]["file"])
    cfg["rates"]["dir"] = rel(cfg["rates"]["dir"])
    cfg["prepay"]["params_file"] = rel(cfg["prepay"]["params_file"])
    cfg["outputs"]["dir"] = rel(cfg["outputs"]["dir"])
    cfg["dashboard"]["dir"] = rel(cfg["dashboard"]["dir"])
    cfg.setdefault("history", {}).setdefault("file", "../history/ear_history.csv")
    cfg.setdefault("validation", {}).setdefault("file", "../calibration/income_validation.json")
    cfg["validation"]["file"] = rel(cfg["validation"]["file"])
    cfg["history"]["file"] = rel(cfg["history"]["file"])
    h = int(cfg.get("horizon_months", 60))
    if h < 12 or h % 12:
        raise ValueError("horizon_months must be a whole number of years, at least 12 (e.g. 60 for 1-5 year horizons)")
    cfg["horizon_months"] = h
    return cfg
