# Copyright (c) 2026 Michal Salanci
# SPDX-License-Identifier: MIT


# =============================================================================
# inspector.tf — Amazon Inspector v2 vulnerability scanning for LTTM v3
#
# Enables Inspector v2 across all three LTTM accounts (main, dev, prod) in three 
# regions (eu-central-1, us-east-1, us-west-2) for automated vulnerability scanning 
# of EC2 instances, Lambda functions, and ECR images
# =============================================================================


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
