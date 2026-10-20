# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

# config_home_region.tf — AWS Config in the home Region (var.project_region) of main, dev and prod: a recorder that
# also records the global resources, a delivery channel to the lake and a recorder status each; config_pipeline.tf
# takes their changes to the lake. Config's own snapshot cycle is off unless var.config_snapshot_frequency is set:
# config_snapshot.tf triggers the daily snapshot. A Region holds one recorder: where the home Region already has
# another one (Control Tower's, say), remove it first.

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

  dynamic "snapshot_delivery_properties" {
    for_each = var.config_snapshot_frequency == null ? [] : [var.config_snapshot_frequency]
    content {
      delivery_frequency = snapshot_delivery_properties.value
    }
  }

  depends_on = [aws_config_configuration_recorder.main_home]
}

resource "aws_config_configuration_recorder_status" "main_home" {
  name       = aws_config_configuration_recorder.main_home.name
  is_enabled = true

  depends_on = [aws_config_delivery_channel.main_home]
}

resource "aws_config_configuration_recorder" "dev_home" {
  provider = aws.dev_eucentral1
  name     = var.config_home_name
  role_arn = "arn:aws:iam::${var.dev_account_id}:role/aws-service-role/config.amazonaws.com/AWSServiceRoleForConfig"

  recording_group {
    all_supported                 = true
    include_global_resource_types = true
  }
}

resource "aws_config_delivery_channel" "dev_home" {
  provider       = aws.dev_eucentral1
  name           = var.config_home_name
  s3_bucket_name = var.prefix

  dynamic "snapshot_delivery_properties" {
    for_each = var.config_snapshot_frequency == null ? [] : [var.config_snapshot_frequency]
    content {
      delivery_frequency = snapshot_delivery_properties.value
    }
  }

  depends_on = [aws_config_configuration_recorder.dev_home]
}

resource "aws_config_configuration_recorder_status" "dev_home" {
  provider   = aws.dev_eucentral1
  name       = aws_config_configuration_recorder.dev_home.name
  is_enabled = true

  depends_on = [aws_config_delivery_channel.dev_home]
}

resource "aws_config_configuration_recorder" "prod_home" {
  provider = aws.prod_eucentral1
  name     = var.config_home_name
  role_arn = "arn:aws:iam::${var.prod_account_id}:role/aws-service-role/config.amazonaws.com/AWSServiceRoleForConfig"

  recording_group {
    all_supported                 = true
    include_global_resource_types = true
  }
}

resource "aws_config_delivery_channel" "prod_home" {
  provider       = aws.prod_eucentral1
  name           = var.config_home_name
  s3_bucket_name = var.prefix

  dynamic "snapshot_delivery_properties" {
    for_each = var.config_snapshot_frequency == null ? [] : [var.config_snapshot_frequency]
    content {
      delivery_frequency = snapshot_delivery_properties.value
    }
  }

  depends_on = [aws_config_configuration_recorder.prod_home]
}

resource "aws_config_configuration_recorder_status" "prod_home" {
  provider   = aws.prod_eucentral1
  name       = aws_config_configuration_recorder.prod_home.name
  is_enabled = true

  depends_on = [aws_config_delivery_channel.prod_home]
}
