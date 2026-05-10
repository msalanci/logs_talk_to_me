# Copyright (c) 2026 Michal Salanci
# SPDX-License-Identifier: MIT


# =============================================================================
# config_multiregion.tf
# Enables AWS Config recording in all regions for all three LTTM accounts  and creates EventBridge 
# cross-region forwarding rules so Config events from those regions are forwarded to eu-central-1 
# where the existing Firehose pipeline (config_pipeline.tf) picks them up.
# =============================================================================


locals {
  forwarding_regions = [
    var.global_region,
    var.agentcore_region,
  ]

  config_accounts = {
    main = { account_id = var.main_account_id }
    dev  = { account_id = var.dev_account_id }
    prod = { account_id = var.prod_account_id }
  }

  account_region_pairs = merge([
    for acct_key, acct in local.config_accounts : {
      for region in local.forwarding_regions :
      "${acct_key}-${region}" => {
        account_key = acct_key
        account_id  = acct.account_id
        region      = region
      }
    }
  ]...)
}

resource "aws_config_configuration_recorder" "main_multiregion" {
  for_each = { for r in local.forwarding_regions : r => r }

  region   = each.value
  name     = "default"
  role_arn = "arn:aws:iam::${var.main_account_id}:role/aws-service-role/config.amazonaws.com/AWSServiceRoleForConfig"

  recording_group {
    all_supported                 = true
    include_global_resource_types = false # Only eu-central-1 should record global resources
  }
}

resource "aws_config_delivery_channel" "main_multiregion" {
  for_each = { for r in local.forwarding_regions : r => r }

  region         = each.value
  name           = "default"
  s3_bucket_name = var.prefix
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

resource "aws_config_configuration_recorder" "main_home" {
  name     = "default"
  role_arn = "arn:aws:iam::${var.main_account_id}:role/aws-service-role/config.amazonaws.com/AWSServiceRoleForConfig"

  recording_group {
    all_supported                 = true
    include_global_resource_types = true
  }
}

resource "aws_config_delivery_channel" "main_home" {
  name           = "default"
  s3_bucket_name = var.prefix
  depends_on = [aws_config_configuration_recorder.main_home]
}

resource "aws_config_configuration_recorder_status" "main_home" {
  name       = aws_config_configuration_recorder.main_home.name
  is_enabled = true

  depends_on = [aws_config_delivery_channel.main_home]
}

resource "aws_cloudwatch_event_rule" "config_forward_main" {
  for_each = { for r in local.forwarding_regions : r => r }

  region      = each.value
  name        = "lttm-config-forward-to-eu-central-1"
  description = "Forwards AWS Config events from ${each.value} to eu-central-1 for LTTM pipeline"

  event_pattern = jsonencode({
    source      = ["aws.config"]
    detail-type = ["Config Configuration Item Change"]
  })
}

resource "aws_cloudwatch_event_target" "config_forward_main" {
  for_each = { for r in local.forwarding_regions : r => r }

  region    = each.value
  rule      = aws_cloudwatch_event_rule.config_forward_main[each.key].name
  target_id = "forward-to-eu-central-1"
  arn       = "arn:aws:events:${var.project_region}:${var.main_account_id}:event-bus/default"
  role_arn  = aws_iam_role.config_cross_region_main.arn
}

# --- Dev account forwarding rules (16 regions) ---

resource "aws_cloudwatch_event_rule" "config_forward_dev" {
  for_each = { for r in local.forwarding_regions : r => r }

  provider    = aws.dev_eucentral1
  region      = each.value
  name        = "lttm-config-forward-to-eu-central-1"
  description = "Forwards AWS Config events from ${each.value} to eu-central-1 for LTTM pipeline"

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
  target_id = "forward-to-eu-central-1"
  arn       = "arn:aws:events:${var.project_region}:${var.dev_account_id}:event-bus/default"
  role_arn  = aws_iam_role.config_cross_region_dev.arn
}

# --- Prod account forwarding rules (16 regions) ---

resource "aws_cloudwatch_event_rule" "config_forward_prod" {
  for_each = { for r in local.forwarding_regions : r => r }

  provider    = aws.prod_eucentral1
  region      = each.value
  name        = "lttm-config-forward-to-eu-central-1"
  description = "Forwards AWS Config events from ${each.value} to eu-central-1 for LTTM pipeline"

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
  target_id = "forward-to-eu-central-1"
  arn       = "arn:aws:events:${var.project_region}:${var.prod_account_id}:event-bus/default"
  role_arn  = aws_iam_role.config_cross_region_prod.arn
}

data "aws_iam_policy_document" "config_cross_region_main_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "config_cross_region_main_policy" {
  statement {
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
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "config_cross_region_dev_policy" {
  statement {
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
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "config_cross_region_prod_policy" {
  statement {
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
