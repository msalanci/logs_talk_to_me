# Copyright (c) 2026 Michal Salanci
# SPDX-License-Identifier: MIT


# =============================================================================
# flowlogs.tf - Enables VPC Flow Logs on every VPC across all three LTTM accounts and regions
# =============================================================================


data "aws_vpcs" "main_eu" {
}

data "aws_vpcs" "main_uswest2" {
  provider = aws.default_uswest2
}

data "aws_vpcs" "dev_eu" {
  provider = aws.dev_eucentral1
}

data "aws_vpcs" "prod_eu" {
  provider = aws.prod_eucentral1
}

resource "aws_flow_log" "main_eu" {
  for_each             = toset(data.aws_vpcs.main_eu.ids)
  vpc_id               = each.value
  log_destination_type = "s3"
  log_destination      = "arn:aws:s3:::${var.prefix}/flowlogs/"
  traffic_type         = "ALL"

  destination_options {
    file_format                = "parquet"
    per_hour_partition         = true
    hive_compatible_partitions = true
  }

  tags = { Project = var.prefix }
}

resource "aws_flow_log" "main_uswest2" {
  for_each             = toset(data.aws_vpcs.main_uswest2.ids)
  provider             = aws.default_uswest2
  vpc_id               = each.value
  log_destination_type = "s3"
  log_destination      = "arn:aws:s3:::${var.prefix}/flowlogs/"
  traffic_type         = "ALL"

  destination_options {
    file_format                = "parquet"
    per_hour_partition         = true
    hive_compatible_partitions = true
  }

  tags = { Project = var.prefix }
}

resource "aws_flow_log" "dev_eu" {
  for_each             = toset(data.aws_vpcs.dev_eu.ids)
  provider             = aws.dev_eucentral1
  vpc_id               = each.value
  log_destination_type = "s3"
  log_destination      = "arn:aws:s3:::${var.prefix}/flowlogs/"
  traffic_type         = "ALL"

  destination_options {
    file_format                = "parquet"
    per_hour_partition         = true
    hive_compatible_partitions = true
  }

  tags = { Project = var.prefix }
}

resource "aws_flow_log" "prod_eu" {
  for_each             = toset(data.aws_vpcs.prod_eu.ids)
  provider             = aws.prod_eucentral1
  vpc_id               = each.value
  log_destination_type = "s3"
  log_destination      = "arn:aws:s3:::${var.prefix}/flowlogs/"
  traffic_type         = "ALL"

  destination_options {
    file_format                = "parquet"
    per_hour_partition         = true
    hive_compatible_partitions = true
  }

  tags = { Project = var.prefix }
}
