"""Data checks run on every charge and discharge before any metric is computed.

A cycle that fails goes to the quarantine table with its reasons instead of
silently skewing the capacity curve.
"""
from __future__ import annotations

import numpy as np

MIN_SAMPLES = 10
VOLTAGE_RANGE = (1.5, 4.5)  # volts; outside this the sensor or the file is wrong for an 18650 cell
TEMP_RANGE = (-10.0, 80.0)  # degrees C
MAX_GAP_S = 120.0  # longer gaps between samples make the coulomb count unreliable
MIN_LOAD_A = 0.5


def check_cycle(kind: str, t, v, i, temp) -> list[str]:
    t, v, i, temp = (np.asarray(a, dtype=float) for a in (t, v, i, temp))
    reasons: list[str] = []
    n = len(t)
    if n < MIN_SAMPLES:
        return [f"only {n} samples"]
    if not (len(v) == len(i) == len(temp) == n):
        return ["signal lengths differ"]
    if not np.all(np.isfinite(np.concatenate([t, v, i, temp]))):
        reasons.append("missing or non-finite values")
    dt = np.diff(t)
    if np.any(dt <= 0):
        reasons.append("time not increasing")
    elif np.max(dt) > MAX_GAP_S:
        reasons.append(f"gap of {np.max(dt):.0f} s between samples")
    if np.nanmin(v) < VOLTAGE_RANGE[0] or np.nanmax(v) > VOLTAGE_RANGE[1]:
        reasons.append("voltage out of range")
    if np.nanmin(temp) < TEMP_RANGE[0] or np.nanmax(temp) > TEMP_RANGE[1]:
        reasons.append("temperature out of range")
    if kind == "discharge" and np.nanmax(-i) < MIN_LOAD_A:
        reasons.append("no discharge current")
    if kind == "charge" and np.nanmax(i) < MIN_LOAD_A:
        reasons.append("no charge current")
    return reasons
