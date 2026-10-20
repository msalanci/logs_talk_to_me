# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

# versions.tf — the Terraform and AWS provider versions of the bootstrap root, which creates only the S3 bucket for the
# remote state of terraform/ (main.tf).

terraform {
  required_version = ">= 1.14.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.43"
    }
  }
}
