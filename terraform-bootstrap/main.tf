# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

# main.tf — the S3 bucket that holds the remote state of terraform/: versioned, SSE-S3 encrypted, never public, and
# protected from destroy. This root keeps its state locally, so it can be applied before the bucket exists: apply it
# once, before terraform/. Its terraform.tfstate is the only record of the bucket: never delete it. If it is lost,
# import the four resources by the bucket's name (terraform import aws_s3_bucket.tf_backend <bucket>, and so on).

provider "aws" {
  region  = var.backend_region
  profile = var.backend_region_profile

  default_tags {
    tags = {
      project   = "lttm-v3-bootstrap"
      terraform = "yes"
    }
  }
}

resource "aws_s3_bucket" "tf_backend" {
  bucket = var.backend_bucket

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "tf_backend" {
  bucket = aws_s3_bucket.tf_backend.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tf_backend" {
  bucket = aws_s3_bucket.tf_backend.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "tf_backend" {
  bucket = aws_s3_bucket.tf_backend.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
