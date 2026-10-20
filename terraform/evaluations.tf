# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

# evaluations.tf — the AgentCore online evaluation of the supervisor (us-west-2, next to the runtime) and the role the
# service runs as: built-in evaluators score var.evaluation_sampling_percentage % of the questions, read from the
# runtime's spans and log group, whose names come from var.cli_stream_runtime_arn (…:runtime/<name>-<suffix>; a
# runtime name has no "-"). While that is the placeholder, no configuration is made. The spans need CloudWatch
# Transaction Search, which agentcore launch turns on.

locals {
  stream_runtime_id   = element(split("/", var.cli_stream_runtime_arn), 1)
  stream_runtime_name = element(split("-", local.stream_runtime_id), 0)
  evaluate_answers    = !strcontains(var.cli_stream_runtime_arn, "placeholder") && var.evaluation_sampling_percentage > 0
}

data "aws_iam_policy_document" "evaluation_trust" {
  provider = aws.default_uswest2

  statement {
    sid     = "AllowAgentCoreEvaluationAssumeRole"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["bedrock-agentcore.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.main_account_id]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:ResourceAccount"
      values   = [var.main_account_id]
    }

    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values = [
        "arn:aws:bedrock-agentcore:${var.agentcore_region}:${var.main_account_id}:evaluator/*",
        "arn:aws:bedrock-agentcore:${var.agentcore_region}:${var.main_account_id}:online-evaluation-config/*",
      ]
    }
  }
}

resource "aws_iam_role" "evaluation" {
  provider           = aws.default_uswest2
  name               = "lttm-evaluation-role"
  description        = "AgentCore online evaluation of the LTTM supervisor"
  assume_role_policy = data.aws_iam_policy_document.evaluation_trust.json
}

data "aws_iam_policy_document" "evaluation" {
  provider = aws.default_uswest2

  statement {
    sid    = "ReadTraces"
    effect = "Allow"
    actions = [
      "logs:DescribeLogGroups",
      "logs:DescribeLogStreams",
      "logs:StartQuery",
      "logs:GetQueryResults",
      "cloudwatch:GenerateQuery",
      "cloudwatch:GenerateQueryResultsSummary",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "WriteScores"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:GetLogEvents",
    ]
    resources = ["arn:aws:logs:${var.agentcore_region}:${var.main_account_id}:log-group:/aws/bedrock-agentcore/evaluations/*"]
  }

  statement {
    sid     = "IndexSpans"
    effect  = "Allow"
    actions = ["logs:DescribeIndexPolicies", "logs:PutIndexPolicy"]
    resources = [
      "arn:aws:logs:${var.agentcore_region}:${var.main_account_id}:log-group:aws/spans",
      "arn:aws:logs:${var.agentcore_region}:${var.main_account_id}:log-group:aws/spans:*",
    ]
  }

  # The judge models of the built-in evaluators are chosen by the service
  statement {
    sid       = "InvokeJudgeModels"
    effect    = "Allow"
    actions   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "evaluation" {
  provider = aws.default_uswest2
  name     = "lttm-evaluation-policy"
  role     = aws_iam_role.evaluation.id
  policy   = data.aws_iam_policy_document.evaluation.json
}

# The service checks the role when the configuration is created; a new role takes a few
# seconds to reach every IAM endpoint (the CLI waits 10 s too).
resource "time_sleep" "evaluation_role_propagation" {
  depends_on      = [aws_iam_role_policy.evaluation]
  create_duration = "15s"
}

resource "aws_bedrockagentcore_online_evaluation_config" "quality" {
  count                         = local.evaluate_answers ? 1 : 0
  provider                      = aws.default_uswest2
  online_evaluation_config_name = "${replace(var.prefix, "-", "_")}_quality_eval"
  description                   = "LTTM answer quality: built-in evaluators on a sample of the supervisor's questions"
  evaluation_execution_role_arn = aws_iam_role.evaluation.arn
  enable_on_create              = true

  data_source_config {
    cloudwatch_logs {
      log_group_names = ["/aws/bedrock-agentcore/runtimes/${local.stream_runtime_id}-DEFAULT"]
      service_names   = ["${local.stream_runtime_name}.DEFAULT"]
    }
  }

  dynamic "evaluator" {
    for_each = toset(var.online_evaluators)
    content {
      evaluator_id = evaluator.value
    }
  }

  rule {
    sampling_config {
      sampling_percentage = var.evaluation_sampling_percentage
    }
  }

  depends_on = [time_sleep.evaluation_role_propagation]
}
