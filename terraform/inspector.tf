# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

# inspector.tf — Inspector v2 in the three accounts and three Regions (eu-central-1, us-east-1, us-west-2): one
# aws_inspector2_enabler each, for EC2, Lambda and ECR. Once enabled, Inspector scans the eligible resources itself;
# each account is enabled on its own, without an Organizations delegated administrator.

resource "aws_inspector2_enabler" "main_eu" {
  account_ids    = ["${var.main_account_id}"]
  resource_types = ["EC2", "LAMBDA", "ECR"]
}

resource "aws_inspector2_enabler" "main_useast1" {
  provider       = aws.default_useast1
  account_ids    = ["${var.main_account_id}"]
  resource_types = ["EC2", "LAMBDA", "ECR"]
}

resource "aws_inspector2_enabler" "main_uswest2" {
  provider       = aws.default_uswest2
  account_ids    = ["${var.main_account_id}"]
  resource_types = ["EC2", "LAMBDA", "ECR"]
}

resource "aws_inspector2_enabler" "dev_eu" {
  provider       = aws.dev_eucentral1
  account_ids    = ["${var.dev_account_id}"]
  resource_types = ["EC2", "LAMBDA", "ECR"]
}

resource "aws_inspector2_enabler" "dev_useast1" {
  provider       = aws.dev_useast1
  account_ids    = ["${var.dev_account_id}"]
  resource_types = ["EC2", "LAMBDA", "ECR"]
}

resource "aws_inspector2_enabler" "dev_uswest2" {
  provider       = aws.dev_uswest2
  account_ids    = ["${var.dev_account_id}"]
  resource_types = ["EC2", "LAMBDA", "ECR"]
}

resource "aws_inspector2_enabler" "prod_eu" {
  provider       = aws.prod_eucentral1
  account_ids    = ["${var.prod_account_id}"]
  resource_types = ["EC2", "LAMBDA", "ECR"]
}

resource "aws_inspector2_enabler" "prod_useast1" {
  provider       = aws.prod_useast1
  account_ids    = ["${var.prod_account_id}"]
  resource_types = ["EC2", "LAMBDA", "ECR"]
}

resource "aws_inspector2_enabler" "prod_uswest2" {
  provider       = aws.prod_uswest2
  account_ids    = ["${var.prod_account_id}"]
  resource_types = ["EC2", "LAMBDA", "ECR"]
}
