# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

# variables.tf — the inputs of the bootstrap root: the state bucket's name and Region, and the AWS CLI profile that
# creates it. A fork sets them in its own terraform.tfvars, from terraform.tfvars.example.

variable "backend_bucket" {
  type        = string
  description = "Name of the S3 bucket for the remote state of terraform/; its backend.tf names the same bucket"

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.backend_bucket))
    error_message = "The backend_bucket must be a valid S3 bucket name: 3 to 63 lower-case letters, digits, dots and hyphens."
  }
}

variable "backend_region_profile" {
  type        = string
  description = "AWS CLI profile that creates the bucket, in the main account"
}

variable "backend_region" {
  type        = string
  description = "AWS Region of the bucket; terraform/backend.tf names the same Region"

  validation {
    condition     = can(regex("^[a-z]{2}(-[a-z]+)+-[0-9]+$", var.backend_region))
    error_message = "The backend_region must be an AWS Region code."
  }
}
