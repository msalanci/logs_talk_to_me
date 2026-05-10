# Copyright (c) 2026 Michal Salanci
# SPDX-License-Identifier: MIT


# =============================================================================
# dns.tf
# Defines Route 53 DNS Query Logging resources for the LTTM data pipeline
# Enables CloudWatch Logs log groups in us-east-1 that receive DNS query log entries from Route 53 for each hosted zone in var.hosted_zone_ids
# Links each Route 53 hosted zone to its corresponding CloudWatch log group, enabling DNS query logging for that zone
# =============================================================================


resource "aws_cloudwatch_log_group" "dns_query_log" {
  for_each = var.hosted_zone_ids
  provider = aws.default_useast1
  name     = "/aws/route53/${each.key}"
}

data "aws_iam_policy_document" "dns_query_log_resource_policy" {
  statement {
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]

    principals {
      type        = "Service"
      identifiers = ["route53.amazonaws.com"]
    }

    resources = ["arn:aws:logs:${var.global_region}:${var.main_account_id}:log-group:/aws/route53/*"]
  }
}

resource "aws_cloudwatch_log_resource_policy" "dns_query_log" {
  provider        = aws.default_useast1
  policy_name     = "lttm-route53-dns-query-log"
  policy_document = data.aws_iam_policy_document.dns_query_log_resource_policy.json
}

resource "aws_route53_query_log" "main" {
  for_each = var.hosted_zone_ids
  provider = aws.default_useast1

  zone_id                  = each.key
  cloudwatch_log_group_arn = aws_cloudwatch_log_group.dns_query_log[each.key].arn

  depends_on = [aws_cloudwatch_log_resource_policy.dns_query_log]
}
