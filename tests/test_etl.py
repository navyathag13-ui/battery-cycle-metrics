"""Integration: synthetic .mat file -> ETL -> SQLite tables."""
import pandas as pd
import pytest


def test_every_cycle_is_loaded(built_db, con):
    _, stats = built_db
    assert stats["B9999"]["cycles"] == 16
    assert pd.read_sql("SELECT COUNT(*) n FROM cycles", con).n[0] == 16


def test_glitched_charge_is_quarantined_not_loaded(con):
    q = pd.read_sql("SELECT * FROM quarantine", con)
    assert len(q) == 1 and q.reasons[0] == "voltage out of range"
    assert pd.read_sql("SELECT COUNT(*) n FROM samples WHERE cycle_no = 16", con).n[0] == 0


def test_capacity_matches_the_file(con):
    d = pd.read_sql("SELECT * FROM discharge_metrics ORDER BY discharge_no", con)
    assert list(d.capacity_ah.round(3)) == [2.0, 1.9, 1.8, 1.6, 1.35]
    assert (d.capacity_ah - d.nasa_capacity_ah).abs().max() < 1e-6


def test_coulombic_efficiency_only_for_clean_pairs(con):
    c = pd.read_sql("SELECT * FROM charge_metrics ORDER BY cycle_no", con)
    # first charge has no discharge before it, so no efficiency
    assert pd.isna(c.coulombic_efficiency.iloc[0])
    assert c.coulombic_efficiency.iloc[1:].notna().all()


def test_impedance_loaded(con):
    imp = pd.read_sql("SELECT * FROM impedance ORDER BY cycle_no", con)
    assert len(imp) == 5
    assert imp.re_ohm.is_monotonic_increasing


def test_state_of_health(con):
    d = pd.read_sql("SELECT soh_pct FROM discharge_metrics ORDER BY discharge_no", con)
    assert d.soh_pct.iloc[0] == pytest.approx(100.0)
    assert d.soh_pct.iloc[-1] == pytest.approx(67.5)
