output "bucket" {
  value = aws_s3_bucket.data.bucket
}

output "ecr_repository_url" {
  value = aws_ecr_repository.lambda.repository_url
}

output "lambda_name" {
  value = aws_lambda_function.etl.function_name
}

output "glue_database" {
  value = aws_glue_catalog_database.battery.name
}

output "athena_workgroup" {
  value = aws_athena_workgroup.battery.name
}
