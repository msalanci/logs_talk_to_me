# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

# cloudtrail.tf — the organization trail: one multi-region trail in the main account that records the API calls of
# every account in the organization, global services included, into the lake under cloudtrail/, with digest files
# for validation. The bucket's default SSE-S3 encryption covers the logs: a KMS key would also need a key policy that
# lets CloudTrail use it.

resource "aws_cloudtrail" "lttm_org_trail" {
  name                          = "lttm-org-trail"
  s3_bucket_name                = var.prefix
  s3_key_prefix                 = "cloudtrail"
  is_organization_trail         = true
  include_global_service_events = true
  is_multi_region_trail         = true
  enable_log_file_validation    = true
}
