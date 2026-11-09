"""Per-cycle battery metrics computed from raw current, voltage and time samples.

Sign convention (same as the NASA data): current is negative while the cell
discharges into the load and positive while it charges.
"""
from __future__ import annotations

import numpy as np

LOAD_ON_A = 0.1  # below this current magnitude the load or charger is treated as off
REFERENCE_CUTOFF_V = 2.7  # NASA reports each discharge's capacity down to 2.7 V, whatever the cell's own cutoff
CHARGE_ON_A = 0.01  # the charger runs until current falls to 20 mA, so the CV tail must count
CV_START_V = 4.19  # charge switches from constant current to constant voltage near 4.2 V


def _trapz(y: np.ndarray, x: np.ndarray) -> float:
    if len(x) < 2:
        return 0.0
    return float(np.sum((y[1:] + y[:-1]) * np.diff(x) / 2.0))


def discharge_metrics(t: np.ndarray, v: np.ndarray, i: np.ndarray, temp: np.ndarray,
                      reference_cutoff_v: float = REFERENCE_CUTOFF_V) -> dict:
    """Capacity, energy and temperature for one discharge.

    Capacity is coulomb counting: the integral of the discharge current over
    time, only over the stretch where the load is drawing current. The
    recording continues for a while after the load switches off, and those
    samples must not count.

    capacity_to_ref_ah stops at the first sample below reference_cutoff_v, which
    is how the dataset's own Capacity field is defined, so the two can be
    compared directly.
    """
    t, v, i, temp = (np.asarray(a, dtype=float) for a in (t, v, i, temp))
    on = -i > LOAD_ON_A
    if not on.any():
        return {"capacity_ah": 0.0, "capacity_to_ref_ah": 0.0, "energy_wh": 0.0, "avg_voltage_v": None, "duration_s": 0.0,
                "end_voltage_v": None, "max_temp_c": float(np.max(temp)) if len(temp) else None,
                "temp_rise_c": 0.0}
    first, last = int(np.argmax(on)), int(len(on) - 1 - np.argmax(on[::-1]))
    s = slice(first, last + 1)
    amps = -i[s]
    capacity_ah = _trapz(amps, t[s]) / 3600.0
    energy_wh = _trapz(amps * v[s], t[s]) / 3600.0
    below = np.nonzero(v[s] < reference_cutoff_v)[0]
    ref_end = first + (int(below[0]) if len(below) else last - first)
    capacity_to_ref_ah = _trapz(-i[first:ref_end + 1], t[first:ref_end + 1]) / 3600.0
    return {
        "capacity_ah": capacity_ah,
        "capacity_to_ref_ah": capacity_to_ref_ah,
        "energy_wh": energy_wh,
        "avg_voltage_v": energy_wh / capacity_ah if capacity_ah else None,
        "duration_s": float(t[last] - t[first]),
        "end_voltage_v": float(v[last]),
        "max_temp_c": float(np.max(temp[s])),
        "temp_rise_c": float(np.max(temp[s]) - temp[first]),
    }


def charge_metrics(t: np.ndarray, v: np.ndarray, i: np.ndarray) -> dict:
    """Charge put in, split into the constant-current and constant-voltage phases."""
    t, v, i = (np.asarray(a, dtype=float) for a in (t, v, i))
    on = i > CHARGE_ON_A
    charge_ah = _trapz(np.where(on, i, 0.0), t) / 3600.0
    reached = np.nonzero(on & (v >= CV_START_V))[0]
    on_idx = np.nonzero(on)[0]
    if not len(on_idx):
        return {"charge_ah": 0.0, "cc_time_s": 0.0, "cv_time_s": 0.0}
    start, end = on_idx[0], on_idx[-1]
    cv_start = reached[0] if len(reached) else end
    return {
        "charge_ah": charge_ah,
        "cc_time_s": float(t[cv_start] - t[start]),
        "cv_time_s": float(t[end] - t[cv_start]),
    }


def fade_summary(cycle_index: np.ndarray, capacity_ah: np.ndarray, rated_ah: float = 2.0,
                 eol_fraction: float = 0.7) -> dict:
    """Capacity fade over a cell's life.

    End of life follows the dataset's own rule: 30% fade from the 2.0 Ah
    rating, i.e. 1.4 Ah. Fade rate is a least-squares slope.
    """
    x = np.asarray(cycle_index, dtype=float)
    y = np.asarray(capacity_ah, dtype=float)
    eol_ah = rated_ah * eol_fraction
    below = np.nonzero(y < eol_ah)[0]
    slope = float(np.polyfit(x, y, 1)[0]) if len(x) >= 2 else 0.0
    return {
        "discharges": int(len(y)),
        "first_capacity_ah": float(y[0]),
        "last_capacity_ah": float(y[-1]),
        "fade_pct": float((y[0] - y[-1]) / y[0] * 100.0) if y[0] > 0 else None,
        "fade_ah_per_100_cycles": -slope * 100.0,
        "eol_ah": eol_ah,
        "eol_discharge": int(x[below[0]]) if len(below) else None,
    }


def charge_flag(coulombic_efficiency: float | None, charge_ah: float, low: float = 0.9, high: float = 1.1) -> str | None:
    """Reason a charge/discharge pair looks wrong, or None."""
    if coulombic_efficiency is None:
        return None
    if coulombic_efficiency > high:
        return f"interrupted or partial charge: {charge_ah:.2f} Ah in, efficiency {coulombic_efficiency:.2f}"
    if coulombic_efficiency < low:
        return f"low efficiency {coulombic_efficiency:.2f}: charge lost or sensor drift"
    return None


def recovery_flags(capacity_ah, jump_ah: float = 0.03) -> list[bool]:
    """True where capacity rose by more than jump_ah over the previous discharge.

    Lithium-ion cells partly recover capacity after a long rest; these jumps are
    real but should not be read as the cell getting healthier.
    """
    caps = list(capacity_ah)
    return [False] + [b - a > jump_ah for a, b in zip(caps, caps[1:])]


def low_capacity_flags(capacity_ah, fraction: float = 0.75, window: int = 9) -> list[bool]:
    """True for a discharge far off its neighbours: under `fraction` of the rolling
    median, or over 1/`fraction` of it.

    NASA notes 'several discharge runs where the capacity was very low' in the
    4 deg C sets without explaining them, and a few runs read far too high. A
    cell does not lose (or gain) a quarter of its capacity in one cycle and
    swing back the next, so these runs are excluded from fade fits instead of
    being read as aging.
    """
    caps = np.asarray(capacity_ah, dtype=float)
    out = []
    for k in range(len(caps)):
        lo, hi = max(0, k - window // 2), min(len(caps), k + window // 2 + 1)
        neighbours = np.delete(caps[lo:hi], k - lo)
        ref = np.median(neighbours) if len(neighbours) else caps[k]
        out.append(bool(caps[k] < fraction * ref or caps[k] > ref / fraction))
    return out


def cycles_to_fraction(capacity_ah, fraction: float = 0.8, baseline_n: int = 5) -> int | None:
    """First discharge (1-based) where capacity stays below `fraction` of the cell's starting capacity.

    'Stays' means the next two discharges are below too, so one dip or a
    flagged low run does not count as end of life.
    """
    caps = np.asarray(capacity_ah, dtype=float)
    if len(caps) < baseline_n + 3:
        return None
    limit = fraction * np.median(caps[:baseline_n])
    for k in range(baseline_n, len(caps) - 2):
        if caps[k] < limit and caps[k + 1] < limit and caps[k + 2] < limit:
            return k + 1
    return None
