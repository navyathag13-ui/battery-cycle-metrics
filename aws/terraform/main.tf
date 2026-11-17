terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.70"
    }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { project = var.project }
  }
}

data "aws_caller_identity" "me" {}

locals {
  bucket = "${var.project}-${data.aws_caller_identity.me.account_id}-${var.region}"
  # One definition of every table's columns, shared with battery_metrics/cloud.py.
  tables = jsondecode(file("${path.module}/../schema.json"))
}

# ---------- Storage: raw uploads, curated Parquet, run reports, Athena results ----------

resource "aws_s3_bucket" "data" {
  bucket        = local.bucket
  force_destroy = true # `terraform destroy` removes the data too; this is a demo, not a system of record
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    id     = "expire-athena-results"
    status = "Enabled"
    filter {
      prefix = "athena-results/"
    }
    expiration {
      days = 7
    }
  }
}

# ---------- Lambda: container image, since numpy + scipy + pandas + pyarrow exceed the zip limit ----------

resource "aws_ecr_repository" "lambda" {
  name                 = "${var.project}-etl"
  image_tag_mutability = "MUTABLE"
  force_delete         = true
  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "lambda" {
  repository = aws_ecr_repository.lambda.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "keep the last 3 images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 3 }
      action       = { type = "expire" }
    }]
  })
}

resource "aws_cloudwatch_log_group" "etl" {
  name              = "/aws/lambda/${var.project}-etl"
  retention_in_days = 14
}

data "aws_iam_policy_document" "assume_lambda" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "etl" {
  name               = "${var.project}-etl"
  assume_role_policy = data.aws_iam_policy_document.assume_lambda.json
}

# Least privilege: read only raw/, write only the three output prefixes, log only to its own group.
data "aws_iam_policy_document" "etl" {
  statement {
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.data.arn}/raw/*"]
  }
  statement {
    actions = ["s3:PutObject"]
    resources = [
      "${aws_s3_bucket.data.arn}/curated/*",
      "${aws_s3_bucket.data.arn}/reports/*",
      "${aws_s3_bucket.data.arn}/rejected/*",
    ]
  }
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.etl.arn}:*"]
  }
}

resource "aws_iam_role_policy" "etl" {
  role   = aws_iam_role.etl.id
  policy = data.aws_iam_policy_document.etl.json
}

resource "aws_lambda_function" "etl" {
  function_name = "${var.project}-etl"
  role          = aws_iam_role.etl.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.lambda.repository_url}:${var.image_tag}"
  architectures = ["x86_64"]
  memory_size   = var.lambda_memory_mb
  timeout       = 300
  # -1 = no reservation. New accounts often have a total limit of 10, and AWS refuses any
  # reservation that leaves fewer than 10 unreserved, so capping is opt-in.
  reserved_concurrent_executions = var.max_concurrency
  depends_on                     = [aws_cloudwatch_log_group.etl, aws_iam_role_policy.etl]
}

resource "aws_lambda_permission" "s3" {
  statement_id   = "AllowS3Invoke"
  action         = "lambda:InvokeFunction"
  function_name  = aws_lambda_function.etl.function_name
  principal      = "s3.amazonaws.com"
  source_arn     = aws_s3_bucket.data.arn
  source_account = data.aws_caller_identity.me.account_id
}

resource "aws_s3_bucket_notification" "raw_upload" {
  bucket = aws_s3_bucket.data.id
  lambda_function {
    lambda_function_arn = aws_lambda_function.etl.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "raw/"
    filter_suffix       = ".mat"
  }
  depends_on = [aws_lambda_permission.s3]
}

# ---------- Glue catalog: one external Parquet table per pipeline table ----------

resource "aws_glue_catalog_database" "battery" {
  name = replace(var.project, "-", "_")
}

resource "aws_glue_catalog_table" "t" {
  for_each      = local.tables
  database_name = aws_glue_catalog_database.battery.name
  name          = each.key
  table_type    = "EXTERNAL_TABLE"
  parameters = {
    classification = "parquet"
    EXTERNAL       = "TRUE"
  }
  storage_descriptor {
    location      = "s3://${aws_s3_bucket.data.bucket}/curated/${each.key}/"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"
    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
    }
    # Athena matches Parquet columns by name, so the order here does not matter.
    dynamic "columns" {
      for_each = each.value
      content {
        name = columns.key
        type = columns.value
      }
    }
  }
}

# ---------- Athena: a workgroup with a scan cap, and the check queries saved in it ----------

resource "aws_athena_workgroup" "battery" {
  name          = var.project
  force_destroy = true
  configuration {
    enforce_workgroup_configuration    = true
    bytes_scanned_cutoff_per_query     = 1073741824 # 1 GB; the whole curated set is far smaller
    publish_cloudwatch_metrics_enabled = true
    result_configuration {
      output_location = "s3://${aws_s3_bucket.data.bucket}/athena-results/"
    }
  }
}

resource "aws_athena_named_query" "q" {
  for_each  = fileset("${path.module}/../queries", "*.sql")
  name      = trimsuffix(each.value, ".sql")
  workgroup = aws_athena_workgroup.battery.id
  database  = aws_glue_catalog_database.battery.name
  query     = file("${path.module}/../queries/${each.value}")
}
