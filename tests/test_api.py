"""End to end: .mat file -> ETL -> database -> HTTP API."""
import pytest
from fastapi.testclient import TestClient

import battery_metrics.api as api


@pytest.fixture()
def client(built_db, monkeypatch):
    monkeypatch.setattr(api, "DB_PATH", built_db[0])
    return TestClient(api.app)


def test_health(client):
    assert client.get("/health").json() == {"status": "ok", "database": True}


def test_fleet_summary(client):
    body = client.get("/batteries").json()
    assert body["B9999"]["eol_discharge"] == 5
    assert body["B9999"]["quarantined_cycles"] == 1


def test_discharges(client):
    rows = client.get("/batteries/B9999/discharges").json()
    assert len(rows) == 5
    assert rows[0]["capacity_ah"] == pytest.approx(2.0)


def test_charges(client):
    rows = client.get("/batteries/B9999/charges").json()
    assert len(rows) == 5
    assert rows[0]["coulombic_efficiency"] is None


def test_unknown_battery_404(client):
    assert client.get("/batteries/NOPE/discharges").status_code == 404


def test_quarantine(client):
    rows = client.get("/quarantine").json()
    assert rows == [{"battery_id": "B9999", "cycle_no": 16, "type": "charge", "reasons": "voltage out of range"}]


def test_missing_database_is_503(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "none.db")
    assert TestClient(api.app).get("/batteries").status_code == 503


def test_fleet_endpoint(client):
    body = client.get("/fleet").json()
    assert body["cells"][0]["battery_id"] == "B9999"
    assert "eol_prediction" in body
