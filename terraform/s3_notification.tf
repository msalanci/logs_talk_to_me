# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

# s3_notification.tf — the lake bucket's event notifications: a Config delivery under AWSLogs/ starts the Lambda
# lttm-config-snapshot-transform (config_snapshot.tf), and a new file in a Firehose error folder goes to the alarm
# topic (alerts.tf). A bucket takes one notification configuration, so every notification of the lake is in this
# resource: a second aws_s3_bucket_notification on the lake would replace it.

resource "aws_s3_bucket_notification" "config_snapshot" {
  bucket = var.prefix

  lambda_function {
    lambda_function_arn = aws_lambda_function.config_snapshot_transform.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "AWSLogs/"
    filter_suffix       = ".json.gz"
  }

  dynamic "topic" {
    for_each = local.lake_error_prefixes
    content {
      id            = "lake-error-${trimsuffix(topic.value, "/")}"
      topic_arn     = aws_sns_topic.lake_error_files.arn
      events        = ["s3:ObjectCreated:*"]
      filter_prefix = topic.value
    }
  }

  depends_on = [aws_lambda_permission.config_snapshot_s3, aws_sns_topic_policy.lake_error_files]
}
