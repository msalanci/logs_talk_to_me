# -----------------------------------------------------------------------------
# versions.tf — Bootstrap_Root version constraints
#
# What: Declares the minimum Terraform version and required AWS provider version
#       for the bootstrap layer (terraform-bootstrap/).
# Why:  Terraform >= 1.14.0 is required (installed: 1.14.6); >= 1.10.0 is the
#       minimum for S3 native locking (use_lockfile = true). Pinning the AWS
#       provider to ~> 6.43 matches Terraform_Root (terraform/) and prevents
#       accidental provider drift between the two configs. Version 6.43 is
#       the minimum that supports aws_bedrockagentcore_memory_strategy with
#       the EPISODIC type used in agents.tf.
# How:  Evaluated by `terraform init`. If the running Terraform binary is older
#       than 1.14.0, init will fail with a version constraint error before any
#       resources are touched.
# -----------------------------------------------------------------------------
terraform {
  required_version = ">= 1.14.0" # installed version is 1.14.6; >= 1.10.0 required for S3 native locking

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.43" # >= 6.43 required for aws_bedrockagentcore_memory_strategy EPISODIC
    }
  }
}
