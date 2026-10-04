locals {
  function_name = "${var.project}-collector"
}

data "archive_file" "collector" {
  type        = "zip"
  source_dir  = "${path.module}/../collector"
  output_path = "${path.module}/.build/collector.zip"
  excludes    = ["__pycache__"]
}

resource "aws_cloudwatch_log_group" "collector" {
  name              = "/aws/lambda/${local.function_name}"
  retention_in_days = var.log_retention_days
}

# --- Lambda execution role ---------------------------------------------------

data "aws_iam_policy_document" "collector_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_iam_role" "collector" {
  name               = "${local.function_name}-role"
  assume_role_policy = data.aws_iam_policy_document.collector_trust.json
}

# Baseline from IAM Policy Autopilot on collector/handler.py, narrowed to the
# concrete resources: the code only calls ssm.get_parameter and s3.put_object.
data "aws_iam_policy_document" "collector" {
  statement {
    sid       = "WriteLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.collector.arn}:*"]
  }

  statement {
    sid       = "ReadApiToken"
    actions   = ["ssm:GetParameter"]
    resources = [local.token_parameter_arn]
  }

  statement {
    sid       = "WriteRawPositions"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.data.arn}/${var.raw_prefix}/*"]
  }
}

resource "aws_iam_role_policy" "collector" {
  name   = "collector"
  role   = aws_iam_role.collector.id
  policy = data.aws_iam_policy_document.collector.json
}

# --- Function ------------------------------------------------------------------

resource "aws_lambda_function" "collector" {
  function_name    = local.function_name
  role             = aws_iam_role.collector.arn
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = "handler.lambda_handler"
  filename         = data.archive_file.collector.output_path
  source_code_hash = data.archive_file.collector.output_base64sha256
  memory_size      = 128
  # Twelve sequential HTTP calls take a few seconds; the ceiling stays under the
  # one-minute schedule so two runs never pile up.
  timeout = 50

  environment {
    variables = {
      BUCKET          = aws_s3_bucket.data.id
      RAW_PREFIX      = var.raw_prefix
      TOKEN_PARAMETER = var.token_parameter_name
      HC_PING_URL     = var.hc_ping_url
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.collector.name
  }

  depends_on = [aws_iam_role_policy.collector]
}

# --- Schedule ------------------------------------------------------------------

data "aws_iam_policy_document" "scheduler_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
    # Scheduler sets aws:SourceArn to the schedule group, not the schedule.
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:scheduler:${var.region}:${local.account_id}:schedule-group/default"]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "${local.function_name}-scheduler-role"
  assume_role_policy = data.aws_iam_policy_document.scheduler_trust.json
}

data "aws_iam_policy_document" "scheduler" {
  statement {
    sid       = "InvokeCollector"
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.collector.arn]
  }
}

resource "aws_iam_role_policy" "scheduler" {
  name   = "invoke-collector"
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.scheduler.json
}

resource "aws_scheduler_schedule" "collector" {
  name                = local.function_name
  group_name          = "default"
  schedule_expression = var.schedule_expression

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.collector.arn
    role_arn = aws_iam_role.scheduler.arn

    # A missed minute is not worth retrying: the next run is a minute away and
    # a late snapshot would be stamped with the wrong time anyway.
    retry_policy {
      maximum_retry_attempts       = 0
      maximum_event_age_in_seconds = 60
    }
  }
}
