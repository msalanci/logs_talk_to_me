# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

# config_multiregion.tf — AWS Config in main, dev and prod in each of local.forwarding_regions: a recorder, a delivery
# channel to the lake and a recorder status, and an EventBridge rule that forwards the Config events to the home
# Region's default bus for config_pipeline.tf. These recorders leave the global resources to the home Region's
# recorders (config_home_region.tf), so each is recorded once. Config's own snapshot cycle is off unless
# var.config_snapshot_frequency is set. A Region holds one recorder: remove any other one first.

locals {
  forwarding_regions = [
    var.global_region,
    var.agentcore_region,
  ]
}

resource "aws_config_configuration_recorder" "main_multiregion" {
  for_each = { for r in local.forwarding_regions : r => r }

  region   = each.value
  name     = "default"
  role_arn = "arn:aws:iam::${var.main_account_id}:role/aws-service-role/config.amazonaws.com/AWSServiceRoleForConfig"

  recording_group {
    all_supported                 = true
    include_global_resource_types = false
  }
}

resource "aws_config_delivery_channel" "main_multiregion" {
  for_each = { for r in local.forwarding_regions : r => r }

  region         = each.value
  name           = "default"
  s3_bucket_name = var.prefix

  dynamic "snapshot_delivery_properties" {
    for_each = var.config_snapshot_frequency == null ? [] : [var.config_snapshot_frequency]
    content {
      delivery_frequency = snapshot_delivery_properties.value
    }
  }

  depends_on = [aws_config_configuration_recorder.main_multiregion]
}

resource "aws_config_configuration_recorder_status" "main_multiregion" {
  for_each = { for r in local.forwarding_regions : r => r }

  region     = each.value
  name       = aws_config_configuration_recorder.main_multiregion[each.key].name
  is_enabled = true

  depends_on = [aws_config_delivery_channel.main_multiregion]
}

resource "aws_config_configuration_recorder" "dev_multiregion" {
  for_each = { for r in local.forwarding_regions : r => r }

  provider = aws.dev_eucentral1
  region   = each.value
  name     = "default"
  role_arn = "arn:aws:iam::${var.dev_account_id}:role/aws-service-role/config.amazonaws.com/AWSServiceRoleForConfig"

  recording_group {
    all_supported                 = true
    include_global_resource_types = false
  }
}

resource "aws_config_delivery_channel" "dev_multiregion" {
  for_each = { for r in local.forwarding_regions : r => r }

  provider       = aws.dev_eucentral1
  region         = each.value
  name           = "default"
  s3_bucket_name = var.prefix

  dynamic "snapshot_delivery_properties" {
    for_each = var.config_snapshot_frequency == null ? [] : [var.config_snapshot_frequency]
    content {
      delivery_frequency = snapshot_delivery_properties.value
    }
  }

  depends_on = [aws_config_configuration_recorder.dev_multiregion]
}

resource "aws_config_configuration_recorder_status" "dev_multiregion" {
  for_each = { for r in local.forwarding_regions : r => r }

  provider   = aws.dev_eucentral1
  region     = each.value
  name       = aws_config_configuration_recorder.dev_multiregion[each.key].name
  is_enabled = true

  depends_on = [aws_config_delivery_channel.dev_multiregion]
}

resource "aws_config_configuration_recorder" "prod_multiregion" {
  for_each = { for r in local.forwarding_regions : r => r }

  provider = aws.prod_eucentral1
  region   = each.value
  name     = "default"
  role_arn = "arn:aws:iam::${var.prod_account_id}:role/aws-service-role/config.amazonaws.com/AWSServiceRoleForConfig"

  recording_group {
    all_supported                 = true
    include_global_resource_types = false
  }
}

resource "aws_config_delivery_channel" "prod_multiregion" {
  for_each = { for r in local.forwarding_regions : r => r }

  provider       = aws.prod_eucentral1
  region         = each.value
  name           = "default"
  s3_bucket_name = var.prefix

  dynamic "snapshot_delivery_properties" {
    for_each = var.config_snapshot_frequency == null ? [] : [var.config_snapshot_frequency]
    content {
      delivery_frequency = snapshot_delivery_properties.value
    }
  }

  depends_on = [aws_config_configuration_recorder.prod_multiregion]
}

resource "aws_config_configuration_recorder_status" "prod_multiregion" {
  for_each = { for r in local.forwarding_regions : r => r }

  provider   = aws.prod_eucentral1
  region     = each.value
  name       = aws_config_configuration_recorder.prod_multiregion[each.key].name
  is_enabled = true

  depends_on = [aws_config_delivery_channel.prod_multiregion]
}

resource "aws_cloudwatch_event_rule" "config_forward_main" {
  for_each = { for r in local.forwarding_regions : r => r }

  region      = each.value
  name        = "lttm-config-forward-to-${var.project_region}"
  description = "Forwards AWS Config events from ${each.value} to ${var.project_region} for LTTM pipeline"

  event_pattern = jsonencode({
    source      = ["aws.config"]
    detail-type = ["Config Configuration Item Change"]
  })
}

resource "aws_cloudwatch_event_target" "config_forward_main" {
  for_each = { for r in local.forwarding_regions : r => r }

  region    = each.value
  rule      = aws_cloudwatch_event_rule.config_forward_main[each.key].name
  target_id = "forward-to-${var.project_region}"
  arn       = "arn:aws:events:${var.project_region}:${var.main_account_id}:event-bus/default"
  role_arn  = aws_iam_role.config_cross_region_main.arn
}

resource "aws_cloudwatch_event_rule" "config_forward_dev" {
  for_each = { for r in local.forwarding_regions : r => r }

  provider    = aws.dev_eucentral1
  region      = each.value
  name        = "lttm-config-forward-to-${var.project_region}"
  description = "Forwards AWS Config events from ${each.value} to ${var.project_region} for LTTM pipeline"

  event_pattern = jsonencode({
    source      = ["aws.config"]
    detail-type = ["Config Configuration Item Change"]
  })
}

resource "aws_cloudwatch_event_target" "config_forward_dev" {
  for_each = { for r in local.forwarding_regions : r => r }

  provider  = aws.dev_eucentral1
  region    = each.value
  rule      = aws_cloudwatch_event_rule.config_forward_dev[each.key].name
  target_id = "forward-to-${var.project_region}"
  arn       = "arn:aws:events:${var.project_region}:${var.dev_account_id}:event-bus/default"
  role_arn  = aws_iam_role.config_cross_region_dev.arn
}

resource "aws_cloudwatch_event_rule" "config_forward_prod" {
  for_each = { for r in local.forwarding_regions : r => r }

  provider    = aws.prod_eucentral1
  region      = each.value
  name        = "lttm-config-forward-to-${var.project_region}"
  description = "Forwards AWS Config events from ${each.value} to ${var.project_region} for LTTM pipeline"

  event_pattern = jsonencode({
    source      = ["aws.config"]
    detail-type = ["Config Configuration Item Change"]
  })
}

resource "aws_cloudwatch_event_target" "config_forward_prod" {
  for_each = { for r in local.forwarding_regions : r => r }

  provider  = aws.prod_eucentral1
  region    = each.value
  rule      = aws_cloudwatch_event_rule.config_forward_prod[each.key].name
  target_id = "forward-to-${var.project_region}"
  arn       = "arn:aws:events:${var.project_region}:${var.prod_account_id}:event-bus/default"
  role_arn  = aws_iam_role.config_cross_region_prod.arn
}

data "aws_iam_policy_document" "config_cross_region_main_trust" {
  statement {
    sid     = "AllowEventBridgeAssumeRole"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.main_account_id]
    }
  }
}

data "aws_iam_policy_document" "config_cross_region_main_policy" {
  statement {
    sid       = "PutEventsToHomeRegionBus"
    effect    = "Allow"
    actions   = ["events:PutEvents"]
    resources = ["arn:aws:events:${var.project_region}:${var.main_account_id}:event-bus/default"]
  }
}

resource "aws_iam_role" "config_cross_region_main" {
  name               = "lttm-config-eventbridge-cross-region"
  assume_role_policy = data.aws_iam_policy_document.config_cross_region_main_trust.json
}

resource "aws_iam_role_policy" "config_cross_region_main" {
  name   = "lttm-config-cross-region-forward"
  role   = aws_iam_role.config_cross_region_main.id
  policy = data.aws_iam_policy_document.config_cross_region_main_policy.json
}

data "aws_iam_policy_document" "config_cross_region_dev_trust" {
  statement {
    sid     = "AllowEventBridgeAssumeRole"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.dev_account_id]
    }
  }
}

data "aws_iam_policy_document" "config_cross_region_dev_policy" {
  statement {
    sid       = "PutEventsToHomeRegionBus"
    effect    = "Allow"
    actions   = ["events:PutEvents"]
    resources = ["arn:aws:events:${var.project_region}:${var.dev_account_id}:event-bus/default"]
  }
}

resource "aws_iam_role" "config_cross_region_dev" {
  provider           = aws.dev_eucentral1
  name               = "lttm-config-eventbridge-cross-region"
  assume_role_policy = data.aws_iam_policy_document.config_cross_region_dev_trust.json
}

resource "aws_iam_role_policy" "config_cross_region_dev" {
  provider = aws.dev_eucentral1
  name     = "lttm-config-cross-region-forward"
  role     = aws_iam_role.config_cross_region_dev.id
  policy   = data.aws_iam_policy_document.config_cross_region_dev_policy.json
}

data "aws_iam_policy_document" "config_cross_region_prod_trust" {
  statement {
    sid     = "AllowEventBridgeAssumeRole"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.prod_account_id]
    }
  }
}

data "aws_iam_policy_document" "config_cross_region_prod_policy" {
  statement {
    sid       = "PutEventsToHomeRegionBus"
    effect    = "Allow"
    actions   = ["events:PutEvents"]
    resources = ["arn:aws:events:${var.project_region}:${var.prod_account_id}:event-bus/default"]
  }
}

resource "aws_iam_role" "config_cross_region_prod" {
  provider           = aws.prod_eucentral1
  name               = "lttm-config-eventbridge-cross-region"
  assume_role_policy = data.aws_iam_policy_document.config_cross_region_prod_trust.json
}

resource "aws_iam_role_policy" "config_cross_region_prod" {
  provider = aws.prod_eucentral1
  name     = "lttm-config-cross-region-forward"
  role     = aws_iam_role.config_cross_region_prod.id
  policy   = data.aws_iam_policy_document.config_cross_region_prod_policy.json
}
