#!/usr/bin/env bash
# Upload the NASA .mat files to raw/; each upload triggers the Lambda.
#   bash aws/upload.sh [folder]     (default data/raw_all)
set -euo pipefail
SRC=${1:-data/raw_all}
BUCKET=$(terraform -chdir=aws/terraform output -raw bucket)
aws s3 cp "$SRC" "s3://$BUCKET/raw/" --recursive --exclude "*" --include "B*.mat"
echo "Uploaded. Reports appear under s3://$BUCKET/reports/ as each file finishes."
