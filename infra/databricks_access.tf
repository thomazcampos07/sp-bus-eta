# Databricks Free Edition cannot assume an IAM role or use a Unity Catalog
# external location, so it reads the raw files with an access key. The user can
# only list and read the raw prefix. The key itself is created outside
# Terraform (scripts/rotate_databricks_key.py) and goes straight into a
# Databricks secret scope, so it never lands in the Terraform state.

resource "aws_iam_user" "databricks_reader" {
  name = "${var.project}-databricks-reader"
}

data "aws_iam_policy_document" "databricks_reader" {
  statement {
    sid       = "ListRawPrefix"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.data.arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["${var.raw_prefix}/*"]
    }
  }

  statement {
    sid       = "ReadRawObjects"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.data.arn}/${var.raw_prefix}/*"]
  }
}

resource "aws_iam_user_policy" "databricks_reader" {
  name   = "read-raw-positions"
  user   = aws_iam_user.databricks_reader.name
  policy = data.aws_iam_policy_document.databricks_reader.json
}

output "databricks_reader_user" {
  value = aws_iam_user.databricks_reader.name
}
