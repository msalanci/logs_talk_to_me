# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

# cognito.tf — the Cognito user pool of LTTM's users, the CLI's app client, the API Gateway authorizer that checks the
# JWT on every route, and the first user. Only an admin creates users. The app client has no secret, because a shell
# script cannot keep one. The first user's temporary password is in the state in plain text: fine for a demo user,
# not for real ones, which are created with the CLI or the console.

resource "aws_cognito_user_pool" "lttm" {
  name = "${var.prefix}-users"

  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  password_policy {
    minimum_length                   = 12
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = true
    temporary_password_validity_days = 7
  }

  auto_verified_attributes = ["email"]

}

resource "aws_cognito_user_pool_client" "lttm_cli" {
  name         = "${var.prefix}-cli"
  user_pool_id = aws_cognito_user_pool.lttm.id

  explicit_auth_flows = [
    "ALLOW_USER_PASSWORD_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH",
  ]

  generate_secret = false

  id_token_validity      = 60
  refresh_token_validity = 30

  token_validity_units {
    id_token      = "minutes"
    refresh_token = "days"
  }
}

resource "aws_api_gateway_authorizer" "cognito" {
  name            = "${var.prefix}-cognito-auth"
  rest_api_id     = aws_api_gateway_rest_api.lttm_stream.id
  type            = "COGNITO_USER_POOLS"
  identity_source = "method.request.header.Authorization"
  provider_arns   = [aws_cognito_user_pool.lttm.arn]
}

resource "aws_cognito_user" "initial" {
  user_pool_id = aws_cognito_user_pool.lttm.id
  username     = var.cognito_initial_user

  temporary_password = var.cognito_initial_temp_password

  attributes = {
    email          = var.cognito_initial_email
    email_verified = "true"
  }

  lifecycle {
    # Don't reset the password if the user has already changed it.
    ignore_changes = [temporary_password]
  }
}
