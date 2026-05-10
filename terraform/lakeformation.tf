# Copyright (c) 2026 Michal Salanci
# SPDX-License-Identifier: MIT

# =============================================================================
# lakeformation.tf — Lake Formation permissions for LTTM v3
#
# Grants lttm-agent-role access to query the lttm_logs Glue database and its tables via Athena
# =============================================================================

resource "aws_lakeformation_permissions" "agent_lttm_db" {
  principal   = aws_iam_role.agent.arn
  permissions = ["DESCRIBE"]

  database {
    name = "lttm_logs"
  }

  depends_on = [aws_glue_catalog_database.lttm_logs]
}

resource "aws_lakeformation_permissions" "agent_cloudtrail_table" {
  principal   = aws_iam_role.agent.arn
  permissions = ["SELECT", "DESCRIBE"]

  table {
    database_name = "lttm_logs"
    name          = "cloudtrail_logs"
  }

  depends_on = [aws_glue_catalog_table.cloudtrail_logs]
}

resource "aws_lakeformation_permissions" "agent_cloudwatch_table" {
  principal   = aws_iam_role.agent.arn
  permissions = ["SELECT", "DESCRIBE"]

  table {
    database_name = "lttm_logs"
    name          = "cloudwatch_logs"
  }

  depends_on = [aws_glue_catalog_table.cloudwatch_logs]
}

resource "aws_lakeformation_permissions" "agent_config_table" {
  principal   = aws_iam_role.agent.arn
  permissions = ["SELECT", "DESCRIBE"]

  table {
    database_name = "lttm_logs"
    name          = "config_logs"
  }

  depends_on = [aws_glue_catalog_table.config_logs]
}

resource "aws_lakeformation_permissions" "agent_config_snapshot_table" {
  principal   = aws_iam_role.agent.arn
  permissions = ["SELECT", "DESCRIBE"]

  table {
    database_name = "lttm_logs"
    name          = "config_snapshot"
  }

  depends_on = [aws_glue_catalog_table.config_snapshot]
}

resource "aws_lakeformation_permissions" "agent_cur_table" {
  principal   = aws_iam_role.agent.arn
  permissions = ["SELECT", "DESCRIBE"]

  table {
    database_name = "lttm_logs"
    name          = "cur_data"
  }

  depends_on = [aws_glue_catalog_table.cur_data]
}

resource "aws_lakeformation_permissions" "agent_flowlogs_table" {
  principal   = aws_iam_role.agent.arn
  permissions = ["SELECT", "DESCRIBE"]

  table {
    database_name = "lttm_logs"
    name          = "flowlogs"
  }

  depends_on = [aws_glue_catalog_table.flowlogs]
}

resource "aws_lakeformation_permissions" "agent_guardduty_table" {
  principal   = aws_iam_role.agent.arn
  permissions = ["SELECT", "DESCRIBE"]

  table {
    database_name = "lttm_logs"
    name          = "guardduty_findings"
  }

  depends_on = [aws_glue_catalog_table.guardduty_findings]
}
