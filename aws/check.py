"""After upload.sh: wait for the Lambda run reports, then run every saved Athena query
and print the results with the data each query scanned.

    python3 aws/check.py [expected_files]      (default 34)

Needs boto3 and AWS credentials (CloudShell has both). Reads names from terraform output.
"""
import json
import subprocess
import sys
import time

import boto3


def tf_output(name: str) -> str:
    return subprocess.check_output(["terraform", "-chdir=aws/terraform", "output", "-raw", name], text=True).strip()


def wait_for_reports(s3, bucket: str, expected: int, timeout_s: int = 600) -> list[dict]:
    deadline = time.time() + timeout_s
    while True:
        objs = s3.list_objects_v2(Bucket=bucket, Prefix="reports/").get("Contents", [])
        rejected = s3.list_objects_v2(Bucket=bucket, Prefix="rejected/").get("KeyCount", 0)
        if len(objs) + rejected >= expected or time.time() > deadline:
            return [json.loads(s3.get_object(Bucket=bucket, Key=o["Key"])["Body"].read()) for o in objs]
        print(f"  {len(objs)} of {expected} loaded, {rejected} rejected; waiting")
        time.sleep(10)


def run_query(athena, sql: str, database: str, workgroup: str) -> tuple[list[list[str]], dict]:
    qid = athena.start_query_execution(QueryString=sql, QueryExecutionContext={"Database": database},
                                       WorkGroup=workgroup)["QueryExecutionId"]
    while True:
        ex = athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]
        if ex["Status"]["State"] in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        time.sleep(1)
    if ex["Status"]["State"] != "SUCCEEDED":
        raise RuntimeError(ex["Status"].get("StateChangeReason", ex["Status"]["State"]))
    rows = athena.get_query_results(QueryExecutionId=qid)["ResultSet"]["Rows"]
    return [[c.get("VarCharValue") for c in r["Data"]] for r in rows], ex["Statistics"]


def main(expected: int) -> None:
    bucket, database, workgroup = tf_output("bucket"), tf_output("glue_database"), tf_output("athena_workgroup")
    s3, athena = boto3.client("s3"), boto3.client("athena")
    reports = wait_for_reports(s3, bucket, expected)
    secs = sorted(r["seconds"] for r in reports)
    print(f"Lambda: {len(reports)} files loaded; seconds per file median {secs[len(secs) // 2]}, max {secs[-1]}")
    ids = athena.list_named_queries(WorkGroup=workgroup)["NamedQueryIds"]
    for q in athena.batch_get_named_query(NamedQueryIds=ids)["NamedQueries"]:
        rows, stats = run_query(athena, q["QueryString"], database, workgroup)
        print(f"\n== {q['Name']}  ({stats['DataScannedInBytes'] / 1e6:.1f} MB scanned, "
              f"{stats['EngineExecutionTimeInMillis'] / 1000:.1f} s)")
        for r in rows[:12]:
            print("  " + " | ".join(str(v) for v in r))


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 34)
