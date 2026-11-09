"""Runs only when the NASA files are present (they are not in git): checks the
pipeline reproduces NASA's own capacity figure on every real discharge."""
from pathlib import Path

import pandas as pd
import pytest

from battery_metrics.etl import build

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"
pytestmark = pytest.mark.skipif(not list(RAW.glob("B*.mat")), reason="NASA .mat files not downloaded")


def test_capacity_matches_nasa_within_0_01_ah(tmp_path):
    import sqlite3

    build(RAW, tmp_path / "b.db")
    d = pd.read_sql("SELECT * FROM discharge_metrics", sqlite3.connect(tmp_path / "b.db"))
    assert len(d) == 636
    assert (d.capacity_to_2v7_ah - d.nasa_capacity_ah).abs().max() < 0.01


ALL = Path(__file__).resolve().parents[1] / "data" / "raw_all"


@pytest.mark.skipif(not list(ALL.glob("B*.mat")) if ALL.exists() else True, reason="full NASA set not unpacked")
def test_all_34_cells_match_nasa_on_99_percent_of_discharges(tmp_path):
    import sqlite3

    build(ALL, tmp_path / "all.db")
    d = pd.read_sql("SELECT * FROM discharge_metrics", sqlite3.connect(tmp_path / "all.db"))
    assert d.battery_id.nunique() == 34
    assert ((d.capacity_to_2v7_ah - d.nasa_capacity_ah).abs() < 0.01).mean() >= 0.99
