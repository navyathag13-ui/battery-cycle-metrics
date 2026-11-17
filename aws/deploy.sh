#!/usr/bin/env bash
# Deploy the AWS path. Written for AWS CloudShell (it already has the AWS CLI, Docker and your
# login); run from the repo root:  bash aws/deploy.sh
#   1. creates the ECR repository
#   2. builds the Lambda image and pushes it, tagged with the git commit
#   3. applies everything else (S3, Lambda, S3 trigger, Glue tables, Athena workgroup and queries)
# Tear down with:  terraform -chdir=aws/terraform destroy -var image_tag=x
set -euo pipefail

TF_VERSION=1.9.8
if ! command -v terraform >/dev/null; then
  mkdir -p "$HOME/bin"
  curl -sSLo /tmp/tf.zip "https://releases.hashicorp.com/terraform/${TF_VERSION}/terraform_${TF_VERSION}_linux_amd64.zip"
  unzip -oq /tmp/tf.zip -d "$HOME/bin" && export PATH="$HOME/bin:$PATH"
fi

TAG=$(git rev-parse --short HEAD)
TF="terraform -chdir=aws/terraform"
$TF init -input=false
$TF apply -input=false -auto-approve -target=aws_ecr_repository.lambda -var "image_tag=$TAG"

REPO=$($TF output -raw ecr_repository_url)
aws ecr get-login-password | docker login --username AWS --password-stdin "${REPO%%/*}"
docker build --platform linux/amd64 --provenance=false -f aws/lambda/Dockerfile -t "$REPO:$TAG" .
docker push "$REPO:$TAG"

$TF apply -input=false -var "image_tag=$TAG"   # shows the plan and asks before creating anything
echo "Bucket: $($TF output -raw bucket)"
echo "Next:   bash aws/upload.sh   then   python3 aws/check.py"
