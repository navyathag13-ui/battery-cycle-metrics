"""The AWS path, run against a local fake S3 (moto): S3 event -> Lambda handler -> Parquet,
then the saved Athena queries run on that Parquet with DuckDB."""
import importlib.util
import io
import json
import sqlite3
from pathlib import Path

import duckdb
import pandas as pd
import pyarrow.parquet as pq
import pytest

boto3 = pytest.importorskip("boto3")
moto = pytest.importorskip("moto")

from battery_metrics.cloud import SCHEMA_FILE, table_schemas  # noqa: E402
from battery_metrics.etl import SCHEMA  # noqa: E402
from tests.synthetic import write_cell  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BUCKET = "battery-test"
OUTPUT_PREFIXES = ("curated/", "reports/", "rejected/")  # the only prefixes the IAM policy lets it write


@pytest.fixture()
def s3(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.setenv(k, "testing")
    with moto.mock_aws():
        client = boto3.client("s3")
        client.create_bucket(Bucket=BUCKET)
        yield client


@pytest.fixture()
def app(s3):
    spec = importlib.util.spec_from_file_location("lambda_app", ROOT / "aws" / "lambda" / "app.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # creates its boto3 client inside the mock
    return mod


def event(*keys):
    # S3 URL-encodes keys in events (a space arrives as "+")
    return {"Records": [{"s3": {"bucket": {"name": BUCKET}, "object": {"key": k.replace(" ", "+")}}} for k in keys]}


def upload(s3, tmp_path, key, **kw):
    local = tmp_path / "cell.mat"
    write_cell(local, **kw)
    s3.upload_file(str(local), BUCKET, key)


def keys(s3):
    return sorted(o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET).get("Contents", []))


def read_table(s3, key):
    return pq.read_table(io.BytesIO(s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()))


def test_upload_writes_every_table_matching_the_local_build(s3, app, tmp_path, built_db):
    upload(s3, tmp_path, "raw/B9999.mat")
    out = app.handler(event("raw/B9999.mat"), None)["reports"][0]
    assert out["status"] == "loaded" and out["battery_id"] == "B9999"
    local = sqlite3.connect(built_db[0])
    for table in table_schemas():
        t = read_table(s3, f"curated/{table}/B9999.parquet")
        assert t.num_rows == pd.read_sql(f"SELECT COUNT(*) n FROM {table}", local).n[0], table
    q = read_table(s3, "curated/quarantine/B9999.parquet").to_pandas()
    assert list(q.reasons) == ["voltage out of range"]
    report = json.loads(s3.get_object(Bucket=BUCKET, Key="reports/B9999.json")["Body"].read())
    assert report["quarantined"] == 1 and report["rows"]["discharge_metrics"] == 5


def test_parquet_types_match_glue_even_when_a_table_is_empty(s3, app, tmp_path):
    upload(s3, tmp_path, "raw/clean.mat", bad_cycle=False)
    app.handler(event("raw/clean.mat"), None)
    q = read_table(s3, "curated/quarantine/B9999.parquet")
    assert q.num_rows == 0
    assert q.schema.equals(table_schemas()["quarantine"])  # no null-typed column for Athena to choke on
    c = read_table(s3, "curated/charge_metrics/B9999.parquet")
    assert str(c.schema.field("flag").type) == "string"


def test_reupload_overwrites_instead_of_duplicating(s3, app, tmp_path):
    upload(s3, tmp_path, "raw/B9999.mat")
    app.handler(event("raw/B9999.mat"), None)
    first = keys(s3)
    app.handler(event("raw/B9999.mat"), None)
    assert keys(s3) == first
    assert read_table(s3, "curated/discharge_metrics/B9999.parquet").num_rows == 5


def test_unreadable_file_is_parked_in_rejected(s3, app):
    s3.put_object(Bucket=BUCKET, Key="raw/broken.mat", Body=b"not a matlab file")
    out = app.handler(event("raw/broken.mat"), None)["reports"][0]
    assert out["status"] == "rejected"
    assert keys(s3) == ["raw/broken.mat", "rejected/broken.json"]


def test_other_keys_are_ignored(s3, app):
    s3.put_object(Bucket=BUCKET, Key="raw/notes.txt", Body=b"x")
    s3.put_object(Bucket=BUCKET, Key="curated/x.mat", Body=b"x")
    outs = app.handler(event("raw/notes.txt", "curated/x.mat"), None)["reports"]
    assert [o["status"] for o in outs] == ["skipped", "skipped"]
    assert keys(s3) == ["curated/x.mat", "raw/notes.txt"]


def test_url_encoded_key_with_a_space(s3, app, tmp_path):
    upload(s3, tmp_path, "raw/my cell.mat")
    assert app.handler(event("raw/my cell.mat"), None)["reports"][0]["status"] == "loaded"


def test_writes_only_where_the_iam_policy_allows(s3, app, tmp_path):
    upload(s3, tmp_path, "raw/B9999.mat")
    s3.put_object(Bucket=BUCKET, Key="raw/broken.mat", Body=b"junk")
    app.handler(event("raw/B9999.mat", "raw/broken.mat"), None)
    written = [k for k in keys(s3) if not k.startswith("raw/")]
    assert written and all(k.startswith(OUTPUT_PREFIXES) for k in written)
    tf = (ROOT / "aws" / "terraform" / "main.tf").read_text()
    for prefix in OUTPUT_PREFIXES:
        assert f"/{prefix}*" in tf


def test_glue_schema_matches_the_sqlite_tables():
    spec = json.loads(SCHEMA_FILE.read_text())
    con = sqlite3.connect(":memory:")
    con.executescript(SCHEMA)
    for table, cols in spec.items():
        sqlite_cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
        assert list(cols) == sqlite_cols, table


def test_saved_athena_queries_run_on_the_parquet(s3, app, tmp_path):
    upload(s3, tmp_path, "raw/B9999.mat")
    app.handler(event("raw/B9999.mat"), None)
    db = duckdb.connect()
    for table in table_schemas():
        path = tmp_path / f"{table}.parquet"
        path.write_bytes(s3.get_object(Bucket=BUCKET, Key=f"curated/{table}/B9999.parquet")["Body"].read())
        db.execute(f"CREATE VIEW {table} AS SELECT * FROM read_parquet('{path}')")
    queries = {p.stem: p.read_text() for p in (ROOT / "aws" / "queries").glob("*.sql")}
    # Same rule as tests/test_real_data.py, computed in pandas from the same rows.
    d = db.execute("SELECT * FROM discharge_metrics").df()
    within = int(((d.capacity_to_2v7_ah - d.nasa_capacity_ah).abs() < 0.01).sum())
    assert db.execute(queries["nasa_agreement"]).fetchall() == [(5, within, round(100.0 * within / 5, 1))]
    assert db.execute(queries["quarantine_reasons"]).fetchall() == [("voltage out of range", 1)]
    assert db.execute(queries["capacity_fade"]).fetchall() == [("B9999", 5, 2.0, 1.35, 32.5)]
    assert db.execute(queries["load_totals"]).fetchone()[:2] == (1, 16)
