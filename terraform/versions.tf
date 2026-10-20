# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

# versions.tf — the Terraform and provider versions this root is tested with. Each provider is pinned to its tested
# minor version, so `terraform init -upgrade` takes only patch releases and the archive provider's Lambda zips stay
# the same. The time provider makes the IAM propagation wait in evaluations.tf. Terraform 1.10 is the first with S3
# native locking (backend.tf).

terraform {
  required_version = ">= 1.14.0"
  required_providers {
    aws = {
      source = "hashicorp/aws"
      # 6.58: namespace_templates and reflection_configuration on memory strategies (agents.tf), and the fix for
      # updating one strategy of a memory that has strategies of other types.
      version = "~> 6.58.0"
    }
    time = {
      source  = "hashicorp/time"
      version = "~> 0.13.1"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.7.1"
    }
  }
}
