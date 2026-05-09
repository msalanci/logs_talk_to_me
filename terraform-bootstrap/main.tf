# Copyright (c) 2026 Michal Salanci
# SPDX-License-Identifier: MIT

# VARIABLES MUST BE DECLARD IN TERRAFORM-BOOTSTRAP/TERRAFORM.TFVARS

# Provider config
provider "aws" {
  region  = var.backend_region
  profile = var.backend_region_profile
}

# Backend provder
resource "aws_s3_bucket" "tf_backend" {
  bucket = var.backend_bucket
  lifecycle {
    prevent_destroy = true
  }
}

# Versioning for backend bucket
resource "aws_s3_bucket_versioning" "tf_backend" {
  bucket = aws_s3_bucket.tf_backend.id
  versioning_configuration {
    status = "Enabled"
  }
}

# Encryption for backend bucket
resource "aws_s3_bucket_server_side_encryption_configuration" "tf_backend" {
  bucket = aws_s3_bucket.tf_backend.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Backend bucket public access
resource "aws_s3_bucket_public_access_block" "tf_backend" {
  bucket = aws_s3_bucket.tf_backend.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
