"""Fleet analysis across all 34 NASA cells.

1. Test conditions per cell, read from the data itself (ambient temperature,
   discharge current, constant or pulsed load), not typed in from the READMEs.
2. Aging by condition: starting capacity and fade rate, after removing the
   abnormal low-capacity runs.
3. End-of-life prediction from each cell's first 20 discharges, scored with
   leave-one-cell-out so every prediction is for a cell the model never saw.
"""
from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd

from .metrics import cycles_to_fraction, low_capacity_flags

EARLY_N = 20  # discharges the predictor is allowed to see
EOL_FRACTION = 0.8  # end of life: capacity stays below 80% of the cell's own start
FADE_WINDOW = 25  # conditions are compared on the first 25 discharges, which every clean condition has


def conditions(con: sqlite3.Connection) -> pd.DataFrame:
    temps = pd.read_sql("SELECT battery_id, ambient_temp_c FROM cycles WHERE type = 'discharge'", con)
    load = pd.read_sql("""
        SELECT s.battery_id, s.cycle_no, -s.current_a AS a FROM samples s
        JOIN cycles c USING (battery_id, cycle_no) WHERE c.type = 'discharge'""", con)
    rows = []
    for b, g in load.groupby("battery_id"):
        per_cycle = []
        for _, cg in g.groupby("cycle_no"):
            a = cg.a.to_numpy()
            on = np.nonzero(a > 0.1)[0]
            if len(on) < 3:
                continue
            window = a[on[0]:on[-1] + 1]
            per_cycle.append((float(np.median(a[on])), float(np.mean(window < 0.1))))
        amps = np.array([p[0] for p in per_cycle])
        rest_share = np.array([p[1] for p in per_cycle])
        t = temps[temps.battery_id == b].ambient_temp_c
        rows.append({
            "battery_id": b,
            "ambient_c": float(t.mode().iloc[0]),
            "mixed_temperature": bool(t.nunique() > 1),
            "load_a": float(np.round(np.median(amps))),
            "mixed_load": bool(np.ptp(np.round(amps)) > 0),
            "pulsed": bool(np.median(rest_share) > 0.2),
        })
    df = pd.DataFrame(rows)
    df["condition"] = df.apply(_label, axis=1)
    return df


def _label(r) -> str:
    if r.mixed_temperature or r.mixed_load:
        return "mixed conditions"
    load = f"{r.load_a:.0f} A {'pulsed' if r.pulsed else 'constant'}"
    return f"{r.ambient_c:.0f} C, {load}"


def cell_aging(con: sqlite3.Connection) -> pd.DataFrame:
    d = pd.read_sql("SELECT battery_id, discharge_no, capacity_to_2v7_ah AS cap FROM discharge_metrics "
                    "ORDER BY battery_id, discharge_no", con)
    rows = []
    for b, g in d.groupby("battery_id"):
        caps = g.cap.to_numpy()
        low = np.array(low_capacity_flags(caps))
        good = caps[~low]
        x = np.arange(1, len(caps) + 1)[~low]
        start = float(np.median(good[:5])) if len(good) >= 5 else float("nan")
        slope = float(np.polyfit(x, good, 1)[0]) if len(good) >= 10 else float("nan")
        first25 = caps[:FADE_WINDOW][~low[:FADE_WINDOW]]
        fade25 = ((float(np.median(first25[:5])) - float(np.median(first25[-5:]))) / float(np.median(first25[:5])) * 100
                  if len(caps) >= FADE_WINDOW and len(first25) >= 10 else float("nan"))
        rows.append({
            "battery_id": b,
            "discharges": int(len(caps)),
            "fade_pct_first_25": fade25,
            "low_runs_flagged": int(low.sum()),
            "start_ah": start,
            "end_ah": float(np.median(good[-5:])) if len(good) >= 5 else float("nan"),
            "fade_pct_per_100": -slope * 100 / start * 100 if start == start and slope == slope else float("nan"),
            "eol_discharge": cycles_to_fraction(good, EOL_FRACTION),
        })
    return pd.DataFrame(rows)


def by_condition(cells: pd.DataFrame, conds: pd.DataFrame) -> pd.DataFrame:
    df = cells.merge(conds, on="battery_id")
    df = df[(df.condition != "mixed conditions") & df.fade_pct_first_25.notna()]
    return (df.groupby("condition")
              .agg(cells=("battery_id", "count"), ids=("battery_id", lambda s: ", ".join(s)),
                   start_ah=("start_ah", "median"), fade_pct_first_25=("fade_pct_first_25", "median"),
                   fade_min=("fade_pct_first_25", "min"), fade_max=("fade_pct_first_25", "max"))
              .reset_index().sort_values("condition"))


def early_features(con: sqlite3.Connection, conds: pd.DataFrame, n: int = EARLY_N) -> pd.DataFrame:
    d = pd.read_sql("SELECT battery_id, discharge_no, capacity_to_2v7_ah AS cap FROM discharge_metrics "
                    "ORDER BY battery_id, discharge_no", con)
    rows = []
    for b, g in d.groupby("battery_id"):
        caps = g.cap.to_numpy()
        good = caps[~np.array(low_capacity_flags(caps))]
        if len(good) < n + 3:
            continue
        early = good[:n]
        start = float(np.median(early[:5]))
        slope = float(np.polyfit(np.arange(n), early, 1)[0])
        rows.append({"battery_id": b, "start_ah": start, "early_slope": slope,
                     "early_drop_pct": (start - float(np.median(early[-5:]))) / start * 100,
                     "eol_discharge": cycles_to_fraction(good, EOL_FRACTION)})
    cols = ["battery_id", "start_ah", "early_slope", "early_drop_pct", "eol_discharge"]
    return pd.DataFrame(rows, columns=cols).merge(conds[["battery_id", "ambient_c", "load_a", "condition"]], on="battery_id")


def predict_eol(feats: pd.DataFrame, n: int = EARLY_N) -> dict:
    """Leave-one-cell-out comparison of three ways to predict end of life from early cycles.

    - mean: predict the average end of life of the other cells (the bar to beat)
    - extrapolate: extend the cell's own early fade line to 80% of its start
    - regression: ridge regression on early fade, starting capacity, temperature and load
    Only cells that reached end of life after the early window can be scored (a
    cell that died inside it would hand the answer to the model), and cells whose
    temperature or load changed mid-test are left out.
    """
    usable = feats[feats.condition != "mixed conditions"]
    df = usable[usable.eol_discharge > n].reset_index(drop=True)
    if len(df) < 3:
        return {"cells_scored": int(len(df)), "note": "too few cells reached end of life to score", "per_cell": []}
    cols = ["early_slope", "early_drop_pct", "start_ah", "ambient_c", "load_a"]
    preds = {"mean": [], "extrapolate": [], "regression": []}
    for k in range(len(df)):
        train, test = df.drop(index=k), df.iloc[k]
        preds["mean"].append(float(train.eol_discharge.mean()))
        slope = test.early_slope
        target_drop = (1 - EOL_FRACTION) * test.start_ah
        preds["extrapolate"].append(float(target_drop / -slope) if slope < 0 else float("nan"))
        X = train[cols].to_numpy(float)
        mu, sd = X.mean(0), X.std(0) + 1e-9
        Xs = np.c_[np.ones(len(X)), (X - mu) / sd]
        y = train.eol_discharge.to_numpy(float)
        lam = 1.0
        w = np.linalg.solve(Xs.T @ Xs + lam * np.diag([0] + [1] * len(cols)), Xs.T @ y)
        xt = np.r_[1, (test[cols].to_numpy(float) - mu) / sd]
        preds["regression"].append(float(max(n, xt @ w)))
    truth = df.eol_discharge.to_numpy(float)
    out = {"cells_scored": int(len(df)), "cells_never_reached_eol": int(usable.eol_discharge.isna().sum()),
           "cells_died_inside_window": int((usable.eol_discharge <= n).sum()),
           "early_discharges_used": n, "per_cell": []}
    for name, p in preds.items():
        p = np.array(p)
        ok = ~np.isnan(p)
        out[f"mae_{name}"] = round(float(np.mean(np.abs(p[ok] - truth[ok]))), 1)
        out[f"median_ae_{name}"] = round(float(np.median(np.abs(p[ok] - truth[ok]))), 1)
        out[f"scored_{name}"] = int(ok.sum())
    for k, r in df.iterrows():
        out["per_cell"].append({"battery_id": r.battery_id, "condition": r.condition, "actual": int(r.eol_discharge),
                                **{m: (None if np.isnan(preds[m][k]) else round(preds[m][k])) for m in preds}})
    return out
