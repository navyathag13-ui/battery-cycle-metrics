import sqlite3

import pytest

from battery_metrics.etl import build
from tests.synthetic import write_cell


@pytest.fixture(scope="session")
def built_db(tmp_path_factory):
    raw = tmp_path_factory.mktemp("raw")
    write_cell(raw / "B9999.mat")
    db = tmp_path_factory.mktemp("db") / "battery.db"
    stats = build(raw, db)
    return db, stats


@pytest.fixture()
def con(built_db):
    c = sqlite3.connect(built_db[0])
    yield c
    c.close()
