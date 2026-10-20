# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

# dynamodb.tf — the table lttm-conversations: one row of metadata per conversation (title, times, question count,
# the user's Cognito sub), which AgentCore Memory, holding the content, cannot list. The stream shim
# writes it, and the list and delete Lambdas read and delete it (lambda.tf). The shim sets expires_at to 30 days
# after the last question, and TTL deletes the row then.

resource "aws_dynamodb_table" "conversations" {
  name                        = "${var.prefix}-conversations"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "session_id"
  deletion_protection_enabled = true

  attribute {
    name = "session_id"
    type = "S"
  }

  attribute {
    name = "user_id"
    type = "S"
  }

  attribute {
    name = "last_active"
    type = "S"
  }

  global_secondary_index {
    name = "user_id-last_active-index"
    key_schema {
      attribute_name = "user_id"
      key_type       = "HASH"
    }
    key_schema {
      attribute_name = "last_active"
      key_type       = "RANGE"
    }
    projection_type = "ALL"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

}
