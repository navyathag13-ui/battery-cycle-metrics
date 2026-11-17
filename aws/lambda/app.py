"""Lambda entry point: S3 ObjectCreated events on raw/*.mat -> battery_metrics.cloud."""
import json
import logging
from urllib.parse import unquote_plus

import boto3

from battery_metrics.cloud import handle_object, table_schemas

log = logging.getLogger()
log.setLevel(logging.INFO)
S3 = boto3.client("s3")
SCHEMAS = table_schemas()


def handler(event, context):
    reports = []
    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = unquote_plus(record["s3"]["object"]["key"])  # S3 events URL-encode keys
        report = handle_object(S3, bucket, key, SCHEMAS)
        log.info(json.dumps(report))
        reports.append(report)
    return {"reports": reports}
