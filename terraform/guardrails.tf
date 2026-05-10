# Copyright (c) 2026 Michal Salanci
# SPDX-License-Identifier: MIT


# =============================================================================
# guardrails.tf
# Creates a Bedrock Guardrail that blocks prompt injection attacks and off-topic questions before the LLM processes them
# =============================================================================


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

  topic_policy_config {
    topics_config {
      name       = "off_topic"
      definition = "Questions that have absolutely nothing to do with AWS, cloud computing, infrastructure, DevOps, software engineering, or the agent's own capabilities and tools"
      type       = "DENY"
      examples   = ["Write me a poem about cats", "What is the weather today?", "Help me with my math homework", "Tell me a joke", "What is the meaning of life?"]
    }
  }

  tags = { Project = var.project_name }
}

resource "aws_bedrock_guardrail_version" "lttm" {
  provider      = aws.default_uswest2
  guardrail_arn = aws_bedrock_guardrail.lttm.guardrail_arn
  description   = "Published version of lttm-prompt-guard guardrail"
  skip_destroy  = true
}
