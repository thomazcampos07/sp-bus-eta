output "data_bucket" {
  value = aws_s3_bucket.data.id
}

output "raw_positions_uri" {
  value = "s3://${aws_s3_bucket.data.id}/${var.raw_prefix}/"
}

output "collector_function" {
  value = aws_lambda_function.collector.function_name
}
