import numpy as np

from battery_metrics.checks import check_cycle
from tests.synthetic import charge, discharge


def test_clean_cycles_pass():
    assert check_cycle("discharge", *discharge()) == []
    assert check_cycle("charge", *charge()) == []


def test_too_few_samples():
    t, v, i, temp = discharge()
    assert check_cycle("discharge", t[:5], v[:5], i[:5], temp[:5]) == ["only 5 samples"]


def test_voltage_glitch():
    t, v, i, temp = charge()
    v = v.copy(); v[3] = 9.9
    assert "voltage out of range" in check_cycle("charge", t, v, i, temp)


def test_nan_values():
    t, v, i, temp = discharge()
    temp = temp.copy(); temp[2] = np.nan
    assert "missing or non-finite values" in check_cycle("discharge", t, v, i, temp)


def test_time_going_backwards():
    t, v, i, temp = discharge()
    t = t.copy(); t[10] = t[9] - 1
    assert "time not increasing" in check_cycle("discharge", t, v, i, temp)


def test_large_gap():
    t, v, i, temp = discharge()
    t = t.copy(); t[20:] += 500
    assert any("gap" in r for r in check_cycle("discharge", t, v, i, temp))


def test_discharge_without_load():
    t, v, i, temp = discharge()
    assert "no discharge current" in check_cycle("discharge", t, v, np.zeros_like(i), temp)
