# Copyright (c) 2026 Michal Salanci
# SPDX-License-Identifier: MIT


# Minimum versions are:
# terraform >= 1.10.0 is required for S3 native locking
# aws provider >= 6.43 to support episodic memory

terraform {
  required_version = ">= 1.14.0" 
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.43"
    }
    time = {
      source  = "hashicorp/time"
      version = "~> 0.12"
    }
  }
}
