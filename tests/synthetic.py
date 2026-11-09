"""Synthetic cells with known answers, written in the same .mat layout NASA uses."""
from __future__ import annotations

import numpy as np
import scipy.io as sio


def discharge(amps=2.0, seconds=3600.0, step=10.0, v_start=4.2, v_end=2.6, tail=60.0):
    """Constant-current discharge, then a rest where the load is off (like the real files)."""
    t_on = np.arange(0.0, seconds + step, step)
    t_off = np.arange(seconds + step, seconds + tail + step, step)
    t = np.concatenate([t_on, t_off])
    v = np.concatenate([np.linspace(v_start, v_end, len(t_on)), np.full(len(t_off), 3.2)])
    i = np.concatenate([np.full(len(t_on), -amps), np.zeros(len(t_off))])
    temp = np.concatenate([np.linspace(24.0, 34.0, len(t_on)), np.full(len(t_off), 33.0)])
    return t, v, i, temp


def charge(cc_amps=1.5, cc_seconds=3600.0, cv_seconds=1800.0, step=10.0):
    """1.5 A to 4.2 V, then a current tail decaying to 20 mA at constant voltage."""
    t_cc = np.arange(0.0, cc_seconds, step)
    t_cv = np.arange(cc_seconds, cc_seconds + cv_seconds + step, step)
    t = np.concatenate([t_cc, t_cv])
    v = np.concatenate([np.linspace(3.5, 4.18, len(t_cc)), np.full(len(t_cv), 4.2)])
    i = np.concatenate([np.full(len(t_cc), cc_amps), np.linspace(cc_amps, 0.02, len(t_cv))])
    temp = np.full(len(t), 25.0)
    return t, v, i, temp


def _cycle(kind, data, n):
    return {"type": kind, "ambient_temperature": 24, "time": np.array([2008, 4, 2, 13, n % 60, 0.0]), "data": data}


def write_cell(path, name="B9999", capacities=(2.0, 1.9, 1.8, 1.6, 1.35), bad_cycle=True):
    """A small cell that fades through the 1.4 Ah end-of-life line, plus one corrupt charge."""
    cycles = []
    n = 0
    for cap in capacities:
        t, v, i, temp = charge()
        cycles.append(_cycle("charge", {"Voltage_measured": v, "Current_measured": i, "Temperature_measured": temp,
                                        "Current_charge": i, "Voltage_charge": v, "Time": t}, n)); n += 1
        t, v, i, temp = discharge(seconds=cap / 2.0 * 3600.0)
        cycles.append(_cycle("discharge", {"Voltage_measured": v, "Current_measured": i, "Temperature_measured": temp,
                                           "Current_load": -i, "Voltage_load": v, "Time": t, "Capacity": cap}, n)); n += 1
        cycles.append(_cycle("impedance", {"Re": 0.05 + 0.002 * n, "Rct": 0.08 + 0.003 * n}, n)); n += 1
    if bad_cycle:
        t, v, i, temp = charge()
        v = v.copy(); v[5] = 9.9  # sensor glitch the checks must catch
        cycles.append(_cycle("charge", {"Voltage_measured": v, "Current_measured": i, "Temperature_measured": temp,
                                        "Current_charge": i, "Voltage_charge": v, "Time": t}, n))
    sio.savemat(str(path), {name: {"cycle": cycles}})
    return name
