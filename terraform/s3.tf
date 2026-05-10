# Copyright (c) 2026 Michal Salanci
# SPDX-License-Identifier: MIT


# =====================================================================================
# s3.tf - Defines the central S3 data lake bucket and all its supporting configuration
#
# S3 in Terraform is split across multiple resource types:
#   - the bucket itself is one resource
#   - settings like encryption, versioning, and public access are separate 
#     resources that reference the bucket by ID. 
# ======================================================================================


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
  bucket                  = aws_s3_bucket.lake.id
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
  }

  statement {
    sid       = "AllowConfigDeliveryChannelWrite"
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.lake.arn}/*"]

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
    sid    = "AllowAgentRoleObjectAccess"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
    ]
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
