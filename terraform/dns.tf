# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

# dns.tf — Route 53 query logging for each zone in var.hosted_zone_ids: a log group /aws/route53/<zone ID> in
# us-east-1, where Route 53 sends every zone's query logs, the resource policy that lets Route 53 write there, and the
# query log itself. The us-east-1 account subscription policy (cloudwatch.tf) sends the groups to the lake. Logging
# starts when the query log is created: there is no history to backfill.

resource "aws_cloudwatch_log_group" "dns_query_log" {
  for_each = var.hosted_zone_ids
  provider = aws.default_useast1
  name     = "/aws/route53/${each.key}"
}

data "aws_iam_policy_document" "dns_query_log_resource_policy" {
  statement {
    sid    = "AllowRoute53QueryLogWrite"
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
