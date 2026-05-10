# Copyright (c) 2026 Michal Salanci
# SPDX-License-Identifier: MIT


# =============================================================================
# cloudtrail.tf
# Creates the CloudTrail organization trail for the LTTM data pipeline.
# =============================================================================


resource "aws_cloudtrail" "lttm_org_trail" {
  name                          = "lttm-org-trail"
  s3_bucket_name                = var.prefix
  s3_key_prefix                 = "cloudtrail"
  is_organization_trail         = true
  include_global_service_events = true
  is_multi_region_trail         = true
  enable_log_file_validation    = true
}
