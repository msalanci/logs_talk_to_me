# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

# backend.tf — the remote state of this root, with S3 native locking (use_lockfile), so no DynamoDB table is needed.
# terraform-bootstrap/ creates the bucket and is applied first. A backend block can't read variables, so the bucket
# and its Region come from backend.hcl: terraform init -backend-config=backend.hcl (copy backend.hcl.example).

terraform {
  backend "s3" {
    key          = "lttm/terraform.tfstate"
    use_lockfile = true
  }
}
