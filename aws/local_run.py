"""Dry run of the AWS path on this machine: a fake S3 (moto), the real Lambda handler,
every .mat file in a folder, then the saved Athena queries on the Parquet with DuckDB.

    .venv/bin/python aws/local_run.py data/raw_all

No AWS account or network needed. Prints JSON: per-run totals and each query's rows.
"""
import importlib.util
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import boto3
import duckdb
from moto import mock_aws

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BUCKET = "battery-local"


def main(raw_dir: Path) -> dict:
    os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "local")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "local")
    files = sorted(raw_dir.glob("B*.mat"))
    with mock_aws():
        s3 = boto3.client("s3")
        s3.create_bucket(Bucket=BUCKET)
        spec = importlib.util.spec_from_file_location("lambda_app", ROOT / "aws" / "lambda" / "app.py")
        app = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(app)
        reports = []
        start = time.perf_counter()
        for f in files:
            key = f"raw/{f.name}"
            s3.upload_file(str(f), BUCKET, key)
            event = {"Records": [{"s3": {"bucket": {"name": BUCKET}, "object": {"key": key}}}]}
            reports += app.handler(event, None)["reports"]
        wall = time.perf_counter() - start
        objs = s3.list_objects_v2(Bucket=BUCKET)["Contents"]
        size = {p: sum(o["Size"] for o in objs if o["Key"].startswith(p)) for p in ("raw/", "curated/")}
        with tempfile.TemporaryDirectory() as tmp:
            db = duckdb.connect()
            for o in objs:
                if o["Key"].startswith("curated/"):
                    local = Path(tmp) / o["Key"]
                    local.parent.mkdir(parents=True, exist_ok=True)
                    s3.download_file(BUCKET, o["Key"], str(local))
            for table_dir in sorted((Path(tmp) / "curated").iterdir()):
                db.execute(f"CREATE VIEW {table_dir.name} AS SELECT * FROM read_parquet('{table_dir}/*.parquet')")
            queries = {}
            for q in sorted((ROOT / "aws" / "queries").glob("*.sql")):
                rel = db.execute(q.read_text())
                cols = [c[0] for c in rel.description]
                queries[q.stem] = [dict(zip(cols, r)) for r in rel.fetchall()]
    secs = [r["seconds"] for r in reports if r["status"] == "loaded"]
    return {
        "files": len(files),
        "loaded": sum(r["status"] == "loaded" for r in reports),
        "rejected": sum(r["status"] == "rejected" for r in reports),
        "wall_seconds": round(wall, 1),
        "slowest_file_seconds": max(secs) if secs else None,
        "raw_mb": round(size["raw/"] / 1e6, 1),
        "parquet_mb": round(size["curated/"] / 1e6, 1),
        "queries": queries,
    }


if __name__ == "__main__":
    print(json.dumps(main(Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "data" / "raw_all")), indent=1, default=str))
