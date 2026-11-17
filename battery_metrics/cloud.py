"""The same ETL run per file on AWS: one NASA .mat file lands in S3, the checks run,
and each table is written back as Parquet that Athena queries through the Glue catalog.

S3 layout (one bucket):
  raw/<file>.mat                      uploads; each one triggers the Lambda
  curated/<table>/<battery_id>.parquet one file per cell per table, overwritten on re-upload
  reports/<battery_id>.json           counts for that run (cycles, samples, quarantined, seconds)
  rejected/<file>.json                files that could not be read, with the error

Nothing here is AWS-specific except the S3 client passed in, so the tests run it
against a local fake S3.
"""
from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import time
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .etl import SCHEMA, load_battery, read_mat

SCHEMA_FILE = Path(__file__).resolve().parents[1] / "aws" / "schema.json"
ARROW_TYPES = {"string": pa.string(), "bigint": pa.int64(), "double": pa.float64()}
RAW_PREFIX, CURATED_PREFIX = "raw/", "curated/"


def table_schemas(path: Path = SCHEMA_FILE) -> dict[str, pa.Schema]:
    """aws/schema.json is the one definition: Terraform builds the Glue tables from it too."""
    spec = json.loads(Path(path).read_text())
    return {t: pa.schema([(c, ARROW_TYPES[k]) for c, k in cols.items()]) for t, cols in spec.items()}


def process_file(mat_path: Path, schemas: dict[str, pa.Schema] | None = None) -> tuple[str, dict, dict[str, pa.Table]]:
    """Run the local pipeline's ETL and checks on one file; return its tables as Arrow."""
    schemas = schemas or table_schemas()
    battery_id, cycles = read_mat(mat_path)
    con = sqlite3.connect(":memory:")
    con.executescript(SCHEMA)
    stats = load_battery(con, battery_id, cycles)
    tables = {}
    for name, schema in schemas.items():
        frame = pd.read_sql(f"SELECT {', '.join(schema.names)} FROM {name}", con)
        # Fixed types, so an all-empty column (no flags, no quarantine) still matches Glue.
        tables[name] = pa.Table.from_pandas(frame, schema=schema, preserve_index=False)
    con.close()
    return battery_id, stats, tables


def handle_object(s3, bucket: str, key: str, schemas: dict[str, pa.Schema] | None = None) -> dict:
    """Process one uploaded object. Returns the run report (also written to reports/ or rejected/)."""
    if not (key.startswith(RAW_PREFIX) and key.endswith(".mat")):
        return {"key": key, "status": "skipped"}
    start = time.perf_counter()
    name = Path(key).name
    with tempfile.TemporaryDirectory() as tmp:
        local = Path(tmp) / name
        s3.download_file(bucket, key, str(local))
        try:
            battery_id, stats, tables = process_file(local, schemas)
        except Exception as exc:  # unreadable or wrong-format file: park it, do not crash the batch
            report = {"key": key, "status": "rejected", "error": f"{type(exc).__name__}: {exc}"}
            s3.put_object(Bucket=bucket, Key=f"rejected/{Path(name).stem}.json", Body=json.dumps(report).encode())
            return report
    for table_name, table in tables.items():
        buf = io.BytesIO()
        # zstd: about 13% smaller than snappy on the sample table; Athena reads both.
        pq.write_table(table, buf, compression="zstd")
        s3.put_object(Bucket=bucket, Key=f"{CURATED_PREFIX}{table_name}/{battery_id}.parquet", Body=buf.getvalue())
    report = {"key": key, "status": "loaded", "battery_id": battery_id, **stats,
              "rows": {t: tables[t].num_rows for t in tables},
              "seconds": round(time.perf_counter() - start, 2)}
    s3.put_object(Bucket=bucket, Key=f"reports/{battery_id}.json", Body=json.dumps(report).encode())
    return report
