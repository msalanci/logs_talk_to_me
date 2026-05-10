# Copyright (c) 2026 Michal Salanci
# SPDX-License-Identifier: MIT


# =============================================================================
# dynamodb.tf — Enables DynamoDb as conversation metadata store
# =============================================================================

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

  tags = { Project = var.prefix }
}
