# Copyright (c) 2026 Michal Salanci
# SPDX-License-Identifier: MIT

# VARIABLES MUST BE DECLARD IN TERRAFORM-BOOTSTRAP/TERRAFORM.TFVARS

# backend variables
variable "backend_bucket" {
  type        = string
  description = "bucket for backend state file"
}

variable "backend_region_profile" {
  type        = string
  description = "AWS CLI profile for region where backend bucket is"
}

variable "backend_region" {
  type        = string
  description = "AWS region where backend bucket is"
}
