# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

# alerts.tf — an email for every new file in the lake's Firehose error folders (cloudwatch-errors/, config-errors/,
# guardduty-errors/): an SNS topic that only the lake bucket's S3 events may publish to, and its email subscription
# (var.lake_alert_email, or else var.cognito_initial_email; AWS sends a confirmation link first). The events are in the
# bucket's one notification resource, in s3_notification.tf. Data that never reaches Firehose leaves no error file.

locals {
  lake_error_prefixes = ["cloudwatch-errors/", "config-errors/", "guardduty-errors/"]
  lake_alert_email    = coalesce(var.lake_alert_email, var.cognito_initial_email)
}

resource "aws_sns_topic" "lake_error_files" {
  name = "lttm-lake-error-files"
}

data "aws_iam_policy_document" "lake_error_files" {
  statement {
    sid       = "LakeErrorFolderEvents"
    effect    = "Allow"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.lake_error_files.arn]

    principals {
      type        = "Service"
      identifiers = ["s3.amazonaws.com"]
    }

    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:s3:::${var.prefix}"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.main_account_id]
    }
  }
}

resource "aws_sns_topic_policy" "lake_error_files" {
  arn    = aws_sns_topic.lake_error_files.arn
  policy = data.aws_iam_policy_document.lake_error_files.json
}

resource "aws_sns_topic_subscription" "lake_error_files_email" {
  topic_arn = aws_sns_topic.lake_error_files.arn
  protocol  = "email"
  endpoint  = local.lake_alert_email
}
