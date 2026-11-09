"""Read-only API over the metrics database.

GET /health
GET /batteries                          fleet summary per cell
GET /batteries/{battery_id}/discharges  per-discharge capacity, energy, temperature, SOH
GET /batteries/{battery_id}/charges     per-charge Ah, CC/CV time, efficiency and flags
GET /quarantine                         cycles that failed a data check and why
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException

from .report import summarize

DB_PATH = Path(os.environ.get("BATTERY_DB", Path(__file__).resolve().parents[1] / "data" / "battery.db"))
app = FastAPI(title="Battery cycle metrics")


def _con() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise HTTPException(503, f"Database not built yet: run scripts/build.py ({DB_PATH})")
    return sqlite3.connect(DB_PATH)


def _rows(sql: str, params: tuple = ()) -> list[dict]:
    with _con() as con:
        frame = pd.read_sql(sql, con, params=params)
    return frame.astype(object).where(frame.notna(), None).to_dict("records")


def _known(battery_id: str) -> None:
    if not _rows("SELECT 1 FROM cycles WHERE battery_id = ? LIMIT 1", (battery_id,)):
        raise HTTPException(404, f"Unknown battery {battery_id}")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "database": DB_PATH.exists()}


@app.get("/batteries")
def batteries() -> dict:
    with _con() as con:
        return summarize(con)["batteries"]


@app.get("/batteries/{battery_id}/discharges")
def discharges(battery_id: str) -> list[dict]:
    _known(battery_id)
    return _rows("SELECT * FROM discharge_metrics WHERE battery_id = ? ORDER BY discharge_no", (battery_id,))


@app.get("/batteries/{battery_id}/charges")
def charges(battery_id: str) -> list[dict]:
    _known(battery_id)
    return _rows("SELECT * FROM charge_metrics WHERE battery_id = ? ORDER BY cycle_no", (battery_id,))


@app.get("/quarantine")
def quarantine() -> list[dict]:
    return _rows("SELECT * FROM quarantine ORDER BY battery_id, cycle_no")


@app.get("/fleet")
def fleet_view() -> dict:
    """Aging by test condition and the leave-one-cell-out end-of-life comparison."""
    from .report import fleet

    with _con() as con:
        return fleet(con)
