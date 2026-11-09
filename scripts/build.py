"""Run the whole pipeline: raw .mat files -> SQLite -> metrics -> results/*.json + charts.

Uses data/raw_all (all 34 cells) when it exists, else data/raw (the 4-cell set).
"""
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from battery_metrics.etl import build  # noqa: E402
from battery_metrics.report import fleet, plot, plot_conditions, summarize  # noqa: E402

if __name__ == "__main__":
    default = ROOT / "data" / "raw_all"
    raw = Path(sys.argv[1]) if len(sys.argv) > 1 else (default if default.exists() else ROOT / "data" / "raw")
    db = ROOT / "data" / "battery.db"
    start = time.perf_counter()
    build(raw, db)
    con = sqlite3.connect(db)
    summary = summarize(con)
    summary["totals"]["build_seconds"] = round(time.perf_counter() - start, 1)
    results = ROOT / "results"
    results.mkdir(exist_ok=True)
    (results / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    (results / "fleet.json").write_text(json.dumps(fleet(con), indent=2, default=str))
    plot(con, results / "capacity_fade.png")
    plot_conditions(con, results / "conditions.png")
    print(json.dumps(summary["totals"], indent=2, default=str))
