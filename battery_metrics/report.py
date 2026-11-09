"""Fleet-level summary from the metrics tables: fade, end of life, impedance growth, flags."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from .metrics import fade_summary


def summarize(con: sqlite3.Connection) -> dict:
    d = pd.read_sql("SELECT * FROM discharge_metrics ORDER BY battery_id, discharge_no", con)
    c = pd.read_sql("SELECT * FROM charge_metrics", con)
    imp = pd.read_sql("SELECT * FROM impedance ORDER BY battery_id, cycle_no", con)
    q = pd.read_sql("SELECT * FROM quarantine", con)
    out = {"batteries": {}}
    for b, g in d.groupby("battery_id"):
        s = fade_summary(g.discharge_no.values, g.capacity_to_2v7_ah.values)
        cg = c[c.battery_id == b]
        ig = imp[(imp.battery_id == b) & imp.re_ohm.notna()]
        s.update({
            "capacity_vs_nasa_mean_abs_ah": round(float(np.mean(np.abs(g.capacity_to_2v7_ah - g.nasa_capacity_ah))), 4),
            "capacity_vs_nasa_max_abs_ah": round(float(np.max(np.abs(g.capacity_to_2v7_ah - g.nasa_capacity_ah))), 4),
            "energy_first_wh": round(float(g.energy_wh.iloc[0]), 3),
            "energy_last_wh": round(float(g.energy_wh.iloc[-1]), 3),
            "median_coulombic_efficiency": round(float(cg.coulombic_efficiency.median()), 3),
            "flagged_charges": int(cg.flag.notna().sum()),
            "recoveries_after_rest": int(g.recovered_after_rest.sum()),
            "re_first_ohm": round(float(ig.re_ohm.iloc[0]), 4) if len(ig) else None,
            "re_last_ohm": round(float(ig.re_ohm.iloc[-1]), 4) if len(ig) else None,
            "quarantined_cycles": int((q.battery_id == b).sum()),
        })
        out["batteries"][b] = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in s.items()}
    out["totals"] = {
        "cycles": int(pd.read_sql("SELECT COUNT(*) n FROM cycles", con).n[0]),
        "samples": int(pd.read_sql("SELECT COUNT(*) n FROM samples", con).n[0]),
        "discharges": int(len(d)),
        "charges": int(len(c)),
        "quarantined": int(len(q)),
        "quarantine_reasons": q.groupby("reasons").size().to_dict(),
        "flagged_charges": c.dropna(subset=["flag"])[["battery_id", "cycle_no", "flag"]].to_dict("records"),
    }
    return out


def plot(con: sqlite3.Connection, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    d = pd.read_sql("SELECT battery_id, discharge_no, capacity_to_2v7_ah, recovered_after_rest FROM discharge_metrics "
                    "WHERE battery_id IN ('B0005', 'B0006', 'B0007', 'B0018')", con)
    fig, ax = plt.subplots(figsize=(8, 4.5))
    for b, g in d.groupby("battery_id"):
        ax.plot(g.discharge_no, g.capacity_to_2v7_ah, label=b, linewidth=1.4)
    ax.axhline(1.4, color="grey", linestyle="--", linewidth=1)
    ax.text(2, 1.41, "end of life (1.4 Ah, 30% fade)", fontsize=8, color="grey")
    ax.set_xlabel("Discharge number")
    ax.set_ylabel("Capacity to 2.7 V (Ah)")
    ax.set_title("Capacity fade, NASA 18650 cells at 24 C, 2 A discharge")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=130)


def fleet(con: sqlite3.Connection) -> dict:
    """Condition comparison and end-of-life prediction across every cell in the database."""
    from .analysis import by_condition, cell_aging, conditions, early_features, predict_eol

    conds = conditions(con)
    cells = cell_aging(con).merge(conds, on="battery_id")
    groups = by_condition(cells[["battery_id", "discharges", "low_runs_flagged", "start_ah", "fade_pct_first_25",
                                 "end_ah", "fade_pct_per_100", "eol_discharge"]], conds)
    clean = lambda df: df.astype(object).where(df.notna(), None).to_dict("records")  # noqa: E731
    return {
        "cells": clean(cells.round(4)),
        "by_condition": clean(groups.round(3)),
        "low_runs_flagged": int(cells.low_runs_flagged.sum()),
        "eol_prediction": predict_eol(early_features(con, conds)),
    }


def plot_conditions(con: sqlite3.Connection, path: Path) -> None:
    """Capacity relative to each cell's start, first 60 discharges, one colour per condition."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from .analysis import conditions
    from .metrics import low_capacity_flags

    conds = conditions(con).set_index("battery_id")
    d = pd.read_sql("SELECT battery_id, discharge_no, capacity_to_2v7_ah AS cap FROM discharge_metrics "
                    "ORDER BY battery_id, discharge_no", con)
    colours = {"24 C, 2 A constant": "#2a7ab9", "4 C, 2 A constant": "#7fb3e0", "4 C, 1 A constant": "#3b9e6b",
               "43 C, 4 A constant": "#d1495b", "24 C, 4 A pulsed": "#edae49"}
    fig, ax = plt.subplots(figsize=(8, 4.5))
    seen = set()
    for b, g in d.groupby("battery_id"):
        cond = conds.loc[b, "condition"]
        if cond not in colours or len(g) < 25:
            continue
        caps = g.cap.to_numpy()
        keep = ~np.array(low_capacity_flags(caps))
        x, y = np.arange(1, len(caps) + 1)[keep], caps[keep]
        rel = y / np.median(y[:5]) * 100
        ax.plot(x[x <= 60], rel[x <= 60], color=colours[cond], linewidth=1.2, alpha=0.85,
                label=None if cond in seen else cond)
        seen.add(cond)
    ax.set_xlabel("Discharge number")
    ax.set_ylabel("Capacity, % of the cell's start")
    ax.set_title("First 60 discharges by test condition (abnormal runs removed)")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
