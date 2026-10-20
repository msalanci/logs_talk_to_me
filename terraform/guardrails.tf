# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

# guardrails.tf — the Bedrock guardrail lttm-prompt-guard (us-west-2, next to the runtime) and its published version:
# it checks each question for prompt attacks and clearly off-topic requests before the supervisor answers. It has no
# PII or toxicity filter and checks no output: IPs, ARNs and account IDs are the answers' data. agentcore deploy passes
# the guardrail's ID and version to the runtime (LTTM_GUARDRAIL_ID, LTTM_GUARDRAIL_VERSION). A change to a policy or a
# message publishes a new version, and skip_destroy keeps the old ones.

resource "aws_bedrock_guardrail" "lttm" {
  provider                  = aws.default_uswest2
  name                      = "lttm-prompt-guard"
  description               = "Prompt injection + topic denial for LTTM supervisor agent"
  blocked_input_messaging   = "I can only help with AWS infrastructure and log analysis questions."
  blocked_outputs_messaging = "Response blocked by safety filter."

  content_policy_config {
    filters_config {
      type            = "PROMPT_ATTACK"
      input_strength  = "HIGH"
      output_strength = "NONE"
    }
  }

  # Narrow on purpose: a broader definition also blocked mixed questions, such as "show me cloudtrail events and list
  # your subagents". The system prompt handles the rest of off-topic.
  topic_policy_config {
    topics_config {
      name       = "off_topic"
      definition = "Questions that have absolutely nothing to do with AWS, cloud computing, infrastructure, DevOps, software engineering, or the agent's own capabilities and tools"
      type       = "DENY"
      examples   = ["Write me a poem about cats", "What is the weather today?", "Help me with my math homework", "Tell me a joke", "What is the meaning of life?"]
    }
  }

}

resource "aws_bedrock_guardrail_version" "lttm" {
  provider      = aws.default_uswest2
  guardrail_arn = aws_bedrock_guardrail.lttm.guardrail_arn
  description   = "Published version of lttm-prompt-guard guardrail"
  skip_destroy  = true

  lifecycle {
    replace_triggered_by = [
      aws_bedrock_guardrail.lttm.content_policy_config,
      aws_bedrock_guardrail.lttm.topic_policy_config,
      aws_bedrock_guardrail.lttm.blocked_input_messaging,
      aws_bedrock_guardrail.lttm.blocked_outputs_messaging,
    ]
  }
}
