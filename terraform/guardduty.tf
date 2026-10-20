# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

# guardduty.tf — GuardDuty in the three accounts: a detector in each account's var.project_region and one in the
# main account's var.agentcore_region, the main account as the organization's delegated administrator with
# auto-enable for every member, and dev and prod as its members. Their findings' archive is in guardduty_archive.tf.

resource "aws_guardduty_detector" "main_eu" {
  enable = true
}

# Off on purpose, the owner's decision: S3 Protection bills every S3 data event, and each Athena read of the lake's
# small files is one. Foundational detection still covers S3 management events.
resource "aws_guardduty_detector_feature" "main_eu_s3_protection" {
  detector_id = aws_guardduty_detector.main_eu.id
  name        = "S3_DATA_EVENTS"
  status      = "DISABLED"
}

resource "aws_guardduty_detector" "main_uswest2" {
  provider = aws.default_uswest2
  enable   = true
}

resource "aws_guardduty_detector" "dev_eu" {
  provider = aws.dev_eucentral1
  enable   = true
}

resource "aws_guardduty_detector" "prod_eu" {
  provider = aws.prod_eucentral1
  enable   = true
}

resource "aws_guardduty_organization_admin_account" "main" {
  admin_account_id = var.main_account_id
  depends_on       = [aws_guardduty_detector.main_eu]
}

resource "aws_guardduty_organization_configuration" "main" {
  detector_id                      = aws_guardduty_detector.main_eu.id
  auto_enable_organization_members = "ALL"
  depends_on                       = [aws_guardduty_organization_admin_account.main]
}

resource "aws_guardduty_member" "dev" {
  detector_id = aws_guardduty_detector.main_eu.id
  account_id  = var.dev_account_id
  email       = var.dev_account_email
  invite      = false
  depends_on  = [aws_guardduty_organization_configuration.main]

  # email is ForceNew, and AWS refuses to delete an organization-managed member: ignore_changes keeps Terraform from
  # trying to replace it. A new deployment still creates the member with its email.
  lifecycle {
    ignore_changes = [email, invite]
  }
}

resource "aws_guardduty_member" "prod" {
  detector_id = aws_guardduty_detector.main_eu.id
  account_id  = var.prod_account_id
  email       = var.prod_account_email
  invite      = false
  depends_on  = [aws_guardduty_organization_configuration.main]

  # email is ForceNew, and AWS refuses to delete an organization-managed member: ignore_changes keeps Terraform from
  # trying to replace it. A new deployment still creates the member with its email.
  lifecycle {
    ignore_changes = [email, invite]
  }
}
