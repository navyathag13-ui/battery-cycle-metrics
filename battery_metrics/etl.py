"""Extract NASA battery .mat files, check them and load them into SQLite.

Tables:
  cycles             one row per charge, discharge or impedance test
  samples            raw time series for charges and discharges
  impedance          electrolyte (Re) and charge-transfer (Rct) resistance
  quarantine         cycles that failed a data check, with the reasons
  discharge_metrics  capacity, energy, temperature and state of health per discharge
  charge_metrics     charge in, CC and CV time, coulombic efficiency
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import scipy.io as sio

from .checks import check_cycle
from .metrics import charge_flag, charge_metrics, discharge_metrics, recovery_flags

SCHEMA = """
CREATE TABLE IF NOT EXISTS cycles (battery_id TEXT, cycle_no INTEGER, type TEXT, start_time TEXT,
    ambient_temp_c REAL, n_samples INTEGER, PRIMARY KEY (battery_id, cycle_no));
CREATE TABLE IF NOT EXISTS samples (battery_id TEXT, cycle_no INTEGER, t_s REAL, voltage_v REAL,
    current_a REAL, temp_c REAL);
CREATE TABLE IF NOT EXISTS impedance (battery_id TEXT, cycle_no INTEGER, re_ohm REAL, rct_ohm REAL);
CREATE TABLE IF NOT EXISTS quarantine (battery_id TEXT, cycle_no INTEGER, type TEXT, reasons TEXT);
CREATE TABLE IF NOT EXISTS discharge_metrics (battery_id TEXT, cycle_no INTEGER, discharge_no INTEGER,
    capacity_ah REAL, capacity_to_2v7_ah REAL, nasa_capacity_ah REAL, energy_wh REAL, avg_voltage_v REAL, end_voltage_v REAL,
    duration_s REAL, max_temp_c REAL, temp_rise_c REAL, soh_pct REAL, recovered_after_rest INTEGER);
CREATE TABLE IF NOT EXISTS charge_metrics (battery_id TEXT, cycle_no INTEGER, charge_ah REAL,
    cc_time_s REAL, cv_time_s REAL, next_discharge_ah REAL, coulombic_efficiency REAL, flag TEXT);
CREATE INDEX IF NOT EXISTS samples_cycle ON samples (battery_id, cycle_no);
"""
RATED_AH = 2.0


def _matlab_time(vec) -> str:
    y, mo, d, h, mi, s = (float(x) for x in vec)
    return (datetime(int(y), int(mo), int(d), int(h), int(mi)) + timedelta(seconds=s)).isoformat(timespec="seconds")


def _real(x) -> float | None:
    try:
        x = complex(np.asarray(x).ravel()[0])
    except (IndexError, TypeError, ValueError):
        return None
    return float(abs(x)) if x.imag else float(x.real)


def read_mat(path: Path) -> tuple[str, list[dict]]:
    m = sio.loadmat(str(path), simplify_cells=True)
    battery_id = next(k for k in m if not k.startswith("__"))
    return battery_id, list(m[battery_id]["cycle"])


def load_battery(con: sqlite3.Connection, battery_id: str, cycles: list[dict]) -> dict:
    stats = {"cycles": 0, "samples": 0, "quarantined": 0}
    cycle_rows, impedance_rows, quarantine_rows, d_rows, c_rows = [], [], [], [], []
    sample_frames = []
    discharge_no = 0
    for n, c in enumerate(cycles, start=1):
        kind, data = c["type"], c["data"]
        size = len(np.atleast_1d(data.get("Time", data.get("Battery_current", []))))
        cycle_rows.append((battery_id, n, kind, _matlab_time(c["time"]), float(c["ambient_temperature"]), size))
        stats["cycles"] += 1
        if kind == "impedance":
            impedance_rows.append((battery_id, n, _real(data.get("Re")), _real(data.get("Rct"))))
            continue
        t, v = np.atleast_1d(data["Time"]), np.atleast_1d(data["Voltage_measured"])
        i, temp = np.atleast_1d(data["Current_measured"]), np.atleast_1d(data["Temperature_measured"])
        reasons = check_cycle(kind, t, v, i, temp)
        if reasons:
            quarantine_rows.append((battery_id, n, kind, "; ".join(reasons)))
            stats["quarantined"] += 1
            continue
        sample_frames.append(pd.DataFrame({"battery_id": battery_id, "cycle_no": n, "t_s": t,
                                           "voltage_v": v, "current_a": i, "temp_c": temp}))
        if kind == "discharge":
            discharge_no += 1
            m = discharge_metrics(t, v, i, temp)
            d_rows.append((battery_id, n, discharge_no, m["capacity_ah"], m["capacity_to_ref_ah"], _real(data.get("Capacity")),
                           m["energy_wh"], m["avg_voltage_v"], m["end_voltage_v"], m["duration_s"],
                           m["max_temp_c"], m["temp_rise_c"], m["capacity_ah"] / RATED_AH * 100.0))
        else:
            m = charge_metrics(t, v, i)
            c_rows.append([battery_id, n, m["charge_ah"], m["cc_time_s"], m["cv_time_s"], None, None, None])

    # Coulombic efficiency: charge taken out on a discharge divided by the charge put in just
    # before it. Only clean discharge -> charge -> discharge sequences count: the first charge
    # starts from a part-full cell and back-to-back charges only top the cell up, so both
    # would make the ratio meaningless.
    by_cycle = {r[1]: r[3] for r in d_rows}
    ops = sorted([(r[1], "discharge") for r in d_rows] + [(r[1], "charge") for r in c_rows])
    kind_at = {n: k for k, (n, _) in enumerate(ops)}
    for row in c_rows:
        k = kind_at[row[1]]
        if 0 < k < len(ops) - 1 and ops[k - 1][1] == "discharge" and ops[k + 1][1] == "discharge" and row[2] > 0:
            row[5] = by_cycle[ops[k + 1][0]]
            row[6] = row[5] / row[2]
            row[7] = charge_flag(row[6], row[2])
    jumps = recovery_flags([r[3] for r in d_rows])
    d_rows = [r + (int(j),) for r, j in zip(d_rows, jumps)]

    con.executemany("INSERT OR REPLACE INTO cycles VALUES (?,?,?,?,?,?)", cycle_rows)
    con.executemany("INSERT INTO impedance VALUES (?,?,?,?)", impedance_rows)
    con.executemany("INSERT INTO quarantine VALUES (?,?,?,?)", quarantine_rows)
    con.executemany("INSERT INTO discharge_metrics VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", d_rows)
    con.executemany("INSERT INTO charge_metrics VALUES (?,?,?,?,?,?,?,?)", [tuple(r) for r in c_rows])
    if sample_frames:
        frame = pd.concat(sample_frames, ignore_index=True)
        frame.to_sql("samples", con, if_exists="append", index=False)
        stats["samples"] = len(frame)
    con.commit()
    return stats


def build(raw_dir: Path, db_path: Path) -> dict:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA)
    out = {}
    for path in sorted(Path(raw_dir).glob("*.mat")):
        battery_id, cycles = read_mat(path)
        out[battery_id] = load_battery(con, battery_id, cycles)
    con.close()
    return out
