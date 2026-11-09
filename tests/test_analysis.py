import numpy as np
import pandas as pd
import pytest

from battery_metrics.analysis import _label, predict_eol
from battery_metrics.metrics import cycles_to_fraction, low_capacity_flags


def test_abnormal_runs_are_flagged_but_normal_fade_is_not():
    caps = np.linspace(1.8, 1.5, 30)
    caps[12] = 0.4  # far too low
    caps[20] = 3.1  # far too high
    flags = low_capacity_flags(caps)
    assert flags[12] is True and flags[20] is True
    assert sum(flags) == 2


def test_end_of_life_needs_three_discharges_in_a_row():
    caps = np.r_[np.full(10, 2.0), 1.5, 2.0, np.full(10, 1.9), np.full(5, 1.5)]
    # the single dip at discharge 11 must not count; the run at 23-25 does
    assert cycles_to_fraction(caps, 0.8) == 23


def test_end_of_life_none_when_never_reached():
    assert cycles_to_fraction(np.linspace(2.0, 1.8, 50), 0.8) is None


@pytest.mark.parametrize("row,label", [
    (dict(mixed_temperature=False, mixed_load=False, ambient_c=24.0, load_a=2.0, pulsed=False), "24 C, 2 A constant"),
    (dict(mixed_temperature=False, mixed_load=False, ambient_c=24.0, load_a=4.0, pulsed=True), "24 C, 4 A pulsed"),
    (dict(mixed_temperature=True, mixed_load=False, ambient_c=4.0, load_a=2.0, pulsed=False), "mixed conditions"),
])
def test_condition_labels(row, label):
    assert _label(pd.Series(row)) == label


def _feats(eols, cond="24 C, 2 A constant"):
    rng = np.random.default_rng(0)
    rows = []
    for k, eol in enumerate(eols):
        rows.append({"battery_id": f"B{k}", "start_ah": 1.8, "early_slope": -0.36 / eol + rng.normal(0, 1e-5),
                     "early_drop_pct": 2.0, "eol_discharge": eol, "ambient_c": 24.0, "load_a": 2.0, "condition": cond})
    return pd.DataFrame(rows)


def test_cells_dying_inside_the_early_window_are_not_scored():
    out = predict_eol(_feats([10, 60, 80, 100, 120]), n=20)
    assert out["cells_scored"] == 4
    assert out["cells_died_inside_window"] == 1


def test_mixed_condition_cells_are_left_out():
    df = pd.concat([_feats([60, 80, 100]), _feats([70], cond="mixed conditions").assign(battery_id="BX")])
    assert predict_eol(df, n=20)["cells_scored"] == 3


def test_extrapolation_is_exact_on_straight_line_fade():
    out = predict_eol(_feats([60, 80, 100, 120]), n=20)
    for row in out["per_cell"]:
        assert row["extrapolate"] == pytest.approx(row["actual"], rel=0.01)
