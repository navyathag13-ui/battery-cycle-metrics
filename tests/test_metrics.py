import numpy as np
import pytest

from battery_metrics.metrics import charge_flag, charge_metrics, discharge_metrics, fade_summary, recovery_flags
from tests.synthetic import charge, discharge


def test_constant_current_capacity_is_amps_times_hours():
    t, v, i, temp = discharge(amps=2.0, seconds=3600.0)
    m = discharge_metrics(t, v, i, temp)
    assert m["capacity_ah"] == pytest.approx(2.0, rel=1e-9)
    assert m["duration_s"] == pytest.approx(3600.0)


def test_rest_after_load_off_is_not_counted():
    with_tail = discharge(tail=600.0)
    no_tail = discharge(tail=0.0)
    assert discharge_metrics(*with_tail)["capacity_ah"] == pytest.approx(discharge_metrics(*no_tail)["capacity_ah"])


def test_energy_is_capacity_times_average_voltage():
    t, v, i, temp = discharge(amps=2.0, seconds=3600.0, v_start=4.0, v_end=3.0)
    m = discharge_metrics(t, v, i, temp)
    assert m["energy_wh"] == pytest.approx(2.0 * 3.5, rel=1e-6)
    assert m["avg_voltage_v"] == pytest.approx(3.5, rel=1e-6)


def test_reference_cutoff_stops_at_2_7_volts():
    t, v, i, temp = discharge(amps=2.0, seconds=3600.0, v_start=4.2, v_end=2.2)
    m = discharge_metrics(t, v, i, temp)
    frac = (4.2 - 2.7) / (4.2 - 2.2)
    assert m["capacity_to_ref_ah"] == pytest.approx(2.0 * frac, abs=0.01)
    assert m["capacity_to_ref_ah"] < m["capacity_ah"]


def test_temperature_rise():
    m = discharge_metrics(*discharge())
    assert m["temp_rise_c"] == pytest.approx(10.0)


def test_no_load_gives_zero_capacity():
    t, v, i, temp = discharge()
    m = discharge_metrics(t, v, np.zeros_like(i), temp)
    assert m["capacity_ah"] == 0.0


def test_charge_counts_cv_tail_down_to_20_ma():
    t, v, i, _ = charge(cc_amps=1.5, cc_seconds=3600.0, cv_seconds=1800.0)
    m = charge_metrics(t, v, i)
    cv_ah = (1.5 + 0.02) / 2 * 1800.0 / 3600.0
    assert m["charge_ah"] == pytest.approx(1.5 + cv_ah, abs=0.01)
    assert m["cc_time_s"] == pytest.approx(3600.0, abs=10.0)
    assert m["cv_time_s"] == pytest.approx(1800.0, abs=10.0)


def test_fade_summary_finds_end_of_life():
    s = fade_summary(np.arange(1, 6), np.array([2.0, 1.9, 1.8, 1.6, 1.35]))
    assert s["eol_discharge"] == 5
    assert s["fade_pct"] == pytest.approx(32.5)
    assert s["fade_ah_per_100_cycles"] > 0


def test_charge_flag():
    assert charge_flag(0.99, 1.8) is None
    assert "interrupted" in charge_flag(10.3, 0.17)
    assert "low efficiency" in charge_flag(0.8, 1.8)
    assert charge_flag(None, 1.8) is None


def test_recovery_flags_mark_capacity_jumps():
    assert recovery_flags([1.8, 1.79, 1.85, 1.84]) == [False, False, True, False]
