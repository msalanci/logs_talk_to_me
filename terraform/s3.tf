# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

# s3.tf — the lake bucket (var.prefix): SSE-S3 default encryption, no public access, no versioning (log files are only
# added), a lifecycle that expires Athena results and abandoned uploads, placeholder objects for the main folders, and
# the bucket policy: each service and account writes only under its own folders, except the BCM export, which needs the
# whole bucket. The dev and prod Firehose writes allow Principal "*" and name the delivery role in aws:PrincipalArn: a
# role given as the principal is bound to its unique ID, so recreating the role would silently stop delivery.

resource "aws_s3_bucket" "lake" {
  bucket = var.prefix

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "lake" {
  bucket = aws_s3_bucket.lake.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "lake" {
  bucket = aws_s3_bucket.lake.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "lake" {
  bucket = aws_s3_bucket.lake.id

  versioning_configuration {
    status = "Disabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "lake" {
  bucket = aws_s3_bucket.lake.id

  rule {
    id     = "expire-athena-results"
    status = "Enabled"

    filter {
      prefix = "athena-results/"
    }

    expiration {
      days = var.athena_results_retention_days
    }
  }

  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

resource "aws_s3_object" "placeholder_cloudtrail" {
  bucket = aws_s3_bucket.lake.id
  key    = "cloudtrail/"
}

resource "aws_s3_object" "placeholder_cloudwatch" {
  bucket = aws_s3_bucket.lake.id
  key    = "cloudwatch/"
}

resource "aws_s3_object" "placeholder_cur" {
  bucket = aws_s3_bucket.lake.id
  key    = "cur/"
}

resource "aws_s3_object" "placeholder_flowlogs" {
  bucket = aws_s3_bucket.lake.id
  key    = "flowlogs/"
}

data "aws_iam_policy_document" "lake" {
  statement {
    sid       = "AllowCloudTrailWrite"
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.lake.arn}/cloudtrail/*"]

    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "s3:x-amz-server-side-encryption"
      values   = ["AES256"]
    }

    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:cloudtrail:*:${var.main_account_id}:trail/*"]
    }
  }

  statement {
    sid       = "AllowCloudTrailAcl"
    effect    = "Allow"
    actions   = ["s3:GetBucketAcl", "s3:GetBucketLocation"]
    resources = [aws_s3_bucket.lake.arn]

    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }
  }

  statement {
    sid     = "AllowDevFirehoseWrite"
    effect  = "Allow"
    actions = ["s3:PutObject"]
    resources = [
      "${aws_s3_bucket.lake.arn}/cloudwatch/*",
      "${aws_s3_bucket.lake.arn}/cloudwatch-errors/*",
    ]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:PrincipalAccount"
      values   = [var.dev_account_id]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:PrincipalArn"
      values   = [aws_iam_role.firehose_cross_account_dev.arn]
    }
  }

  statement {
    sid     = "AllowProdFirehoseWrite"
    effect  = "Allow"
    actions = ["s3:PutObject"]
    resources = [
      "${aws_s3_bucket.lake.arn}/cloudwatch/*",
      "${aws_s3_bucket.lake.arn}/cloudwatch-errors/*",
    ]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:PrincipalAccount"
      values   = [var.prod_account_id]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:PrincipalArn"
      values   = [aws_iam_role.firehose_cross_account_prod.arn]
    }
  }

  statement {
    sid     = "AllowDevConfigFirehoseWrite"
    effect  = "Allow"
    actions = ["s3:PutObject"]
    resources = [
      "${aws_s3_bucket.lake.arn}/config/*",
      "${aws_s3_bucket.lake.arn}/config-errors/*",
    ]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:PrincipalAccount"
      values   = [var.dev_account_id]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:PrincipalArn"
      values   = [aws_iam_role.config_firehose_cross_account_dev.arn]
    }
  }

  statement {
    sid     = "AllowProdConfigFirehoseWrite"
    effect  = "Allow"
    actions = ["s3:PutObject"]
    resources = [
      "${aws_s3_bucket.lake.arn}/config/*",
      "${aws_s3_bucket.lake.arn}/config-errors/*",
    ]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:PrincipalAccount"
      values   = [var.prod_account_id]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:PrincipalArn"
      values   = [aws_iam_role.config_firehose_cross_account_prod.arn]
    }
  }

  statement {
    sid       = "AllowConfigDeliveryChannelWrite"
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.lake.arn}/AWSLogs/${var.main_account_id}/Config/*"]

    principals {
      type        = "Service"
      identifiers = ["config.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceAccount"
      values   = [var.main_account_id]
    }
  }

  statement {
    sid       = "AllowConfigBucketCheck"
    effect    = "Allow"
    actions   = ["s3:GetBucketAcl", "s3:ListBucket"]
    resources = [aws_s3_bucket.lake.arn]

    principals {
      type        = "Service"
      identifiers = ["config.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceAccount"
      values   = [var.main_account_id]
    }
  }

  statement {
    sid       = "AllowCURDeliveryWrite"
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.lake.arn}/*"]

    principals {
      type        = "Service"
      identifiers = ["bcm-data-exports.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.main_account_id]
    }
  }

  statement {
    sid       = "AllowCURDeliveryBucketCheck"
    effect    = "Allow"
    actions   = ["s3:GetBucketPolicy"]
    resources = [aws_s3_bucket.lake.arn]

    principals {
      type        = "Service"
      identifiers = ["bcm-data-exports.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.main_account_id]
    }
  }

  statement {
    sid    = "AllowAgentRoleAccess"
    effect = "Allow"
    actions = [
      "s3:ListBucket",
      "s3:GetBucketLocation",
    ]
    resources = [aws_s3_bucket.lake.arn]

    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.agent.arn]
    }
  }

  statement {
    sid     = "AllowAgentRoleObjectRead"
    effect  = "Allow"
    actions = ["s3:GetObject"]
    resources = [
      "${aws_s3_bucket.lake.arn}/cloudtrail/*",
      "${aws_s3_bucket.lake.arn}/cloudwatch/*",
      "${aws_s3_bucket.lake.arn}/config/*",
      "${aws_s3_bucket.lake.arn}/cur/*",
      "${aws_s3_bucket.lake.arn}/flowlogs/*",
      "${aws_s3_bucket.lake.arn}/guardduty/*",
      "${aws_s3_bucket.lake.arn}/athena-results/*",
    ]

    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.agent.arn]
    }
  }

  statement {
    sid       = "AllowAgentRoleAthenaResultsWrite"
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.lake.arn}/athena-results/*"]

    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.agent.arn]
    }
  }

  statement {
    sid     = "AllowDevGuardDutyFirehoseWrite"
    effect  = "Allow"
    actions = ["s3:PutObject"]
    resources = [
      "${aws_s3_bucket.lake.arn}/guardduty/*",
      "${aws_s3_bucket.lake.arn}/guardduty-errors/*",
    ]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:PrincipalAccount"
      values   = [var.dev_account_id]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:PrincipalArn"
      values   = [aws_iam_role.guardduty_firehose_cross_account_dev.arn]
    }
  }

  statement {
    sid     = "AllowProdGuardDutyFirehoseWrite"
    effect  = "Allow"
    actions = ["s3:PutObject"]
    resources = [
      "${aws_s3_bucket.lake.arn}/guardduty/*",
      "${aws_s3_bucket.lake.arn}/guardduty-errors/*",
    ]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:PrincipalAccount"
      values   = [var.prod_account_id]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:PrincipalArn"
      values   = [aws_iam_role.guardduty_firehose_cross_account_prod.arn]
    }
  }

  statement {
    sid       = "AllowFlowLogDeliveryWrite"
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.lake.arn}/flowlogs/*"]

    principals {
      type        = "Service"
      identifiers = ["delivery.logs.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.main_account_id, var.dev_account_id, var.prod_account_id]
    }
  }

  statement {
    sid       = "AllowFlowLogDeliveryBucketCheck"
    effect    = "Allow"
    actions   = ["s3:GetBucketAcl", "s3:GetBucketLocation"]
    resources = [aws_s3_bucket.lake.arn]

    principals {
      type        = "Service"
      identifiers = ["delivery.logs.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.main_account_id, var.dev_account_id, var.prod_account_id]
    }
  }
  statement {
    sid    = "AllowConfigCrossAccountWrite"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["config.amazonaws.com"]
    }

    actions = [
      "s3:PutObject",
    ]

    resources = [
      "${aws_s3_bucket.lake.arn}/AWSLogs/${var.dev_account_id}/Config/*",
      "${aws_s3_bucket.lake.arn}/AWSLogs/${var.prod_account_id}/Config/*",
    ]

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceAccount"
      values   = [var.dev_account_id, var.prod_account_id]
    }
  }

  statement {
    sid    = "AllowConfigCrossAccountBucketCheck"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["config.amazonaws.com"]
    }

    actions = [
      "s3:GetBucketAcl",
      "s3:ListBucket",
    ]

    resources = [
      aws_s3_bucket.lake.arn,
    ]

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceAccount"
      values   = [var.dev_account_id, var.prod_account_id]
    }
  }
}

resource "aws_s3_bucket_policy" "lake" {
  bucket = aws_s3_bucket.lake.id
  policy = data.aws_iam_policy_document.lake.json
}
