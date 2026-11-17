variable "project" {
  description = "Prefix for every resource name."
  type        = string
  default     = "battery-aging"
}

variable "region" {
  type    = string
  default = "us-east-1"
}

variable "image_tag" {
  description = "Tag of the Lambda image pushed to ECR (deploy.sh passes the git commit)."
  type        = string
}

variable "lambda_memory_mb" {
  type    = number
  default = 2048
}

variable "max_concurrency" {
  description = "Reserved concurrency for the ETL Lambda; -1 leaves it unreserved."
  type        = number
  default     = -1
}
