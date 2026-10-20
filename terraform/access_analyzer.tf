# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

# access_analyzer.tf — trusted access for IAM Access Analyzer in AWS Organizations, and an ORGANIZATION analyzer named
# lttm-org-analyzer in eu-central-1, us-east-1 and us-west-2, all in the main account, the management account. With the
# organization as the zone of trust, a finding is access from outside it, not main, dev and prod reaching each other.
# The name and the Regions must match ACCESS_ANALYZER_NAME and ACCESS_ANALYZER_REGIONS in agents/utils/agent_vars.py.

resource "aws_organizations_aws_service_access" "access_analyzer" {
  service_principal = "access-analyzer.amazonaws.com"
}

resource "aws_accessanalyzer_analyzer" "org_eucentral1" {
  analyzer_name = "lttm-org-analyzer"
  type          = "ORGANIZATION"

  depends_on = [aws_organizations_aws_service_access.access_analyzer]
}

resource "aws_accessanalyzer_analyzer" "org_useast1" {
  provider      = aws.default_useast1
  analyzer_name = "lttm-org-analyzer"
  type          = "ORGANIZATION"

  depends_on = [aws_organizations_aws_service_access.access_analyzer]
}

resource "aws_accessanalyzer_analyzer" "org_uswest2" {
  provider      = aws.default_uswest2
  analyzer_name = "lttm-org-analyzer"
  type          = "ORGANIZATION"

  depends_on = [aws_organizations_aws_service_access.access_analyzer]
}
