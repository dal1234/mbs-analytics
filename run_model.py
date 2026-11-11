#!/usr/bin/env python
"""Project interest income for a portfolio of MBS CUSIPs along every Monte Carlo rate path, measure earnings at
risk for 1- to 5-year horizons and build the dashboard.

    python run_model.py validate [--config config/model.yaml] [--portfolio FILE] [--rates-dir DIR]
    python run_model.py run      [--config config/model.yaml] [--portfolio FILE] [--rates-dir DIR] [--revalidate]

validate  checks the portfolio file and the rate paths and prints every problem found; nothing is written.
run       validates, projects, writes outputs/ (CSV), appends to history/ear_history.csv and rebuilds dashboard/
          (open dashboard/index.html in a browser).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mbs_lite.config import load_config      # noqa: E402
from mbs_lite.run import InputError, prepare, run  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["validate", "run"])
    ap.add_argument("--config", default=str(Path(__file__).parent / "config" / "model.yaml"))
    ap.add_argument("--portfolio", help="portfolio CSV (overrides portfolio.file)")
    ap.add_argument("--rates-dir", help="run_rates.py output folder (overrides rates.dir)")
    ap.add_argument("--revalidate", action="store_true", help="rerun V5/V8 even if stored for this calibration")
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    if a.portfolio:
        cfg["portfolio"]["file"] = str(Path(a.portfolio).resolve())
    if a.rates_dir:
        cfg["rates"]["dir"] = str(Path(a.rates_dir).resolve())
    try:
        if a.command == "validate":
            prepare(cfg)
            print("OK: inputs are valid. Next: python run_model.py run")
        else:
            run(cfg, revalidate=a.revalidate)
    except InputError as e:
        print("\nInputs are not valid:\n  - " + str(e).replace("\n", "\n  - "), file=sys.stderr)
        return 2
    except (FileNotFoundError, ValueError) as e:
        print(f"\nError: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
