# Copyright (c) 2026 Michal Salanci
# SPDX-License-Identifier: MIT


# =============================================================================
# backend.tf — Configures remote state for Terraform_Root to store terraform state in the S3 bucket
# =============================================================================


terraform {
  backend "s3" {
    bucket       = "lttm-tf-backend" # MUST BE ADDED MANUALLY, AS PER TERRAFORM-BOOTSTRAP/TERRAFORM.TFVARS
    key          = "lttm/terraform.tfstate"
    region       = "eu-central-1"
    use_lockfile = true
  }
}
