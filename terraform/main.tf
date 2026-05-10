# Copyright (c) 2026 Michal Salanci
# SPDX-License-Identifier: MIT


# =====================================================================================
# main.tf - Configures the AWS providers
# =====================================================================================


provider "aws" {
  profile = var.main_profile
  region  = var.project_region
}

provider "aws" {
  alias   = "dev_eucentral1"
  profile = var.dev_profile
  region  = var.project_region
}

provider "aws" {
  alias   = "prod_eucentral1"
  profile = var.prod_profile
  region  = var.project_region
}

provider "aws" {
  alias   = "default_uswest2"
  profile = var.main_profile
  region  = var.agentcore_region
}

provider "aws" {
  alias   = "default_useast1"
  profile = var.main_profile
  region  = var.global_region
}

provider "aws" {
  alias   = "dev_useast1"
  profile = var.dev_profile
  region  = var.global_region
}

provider "aws" {
  alias   = "dev_uswest2"
  profile = var.dev_profile
  region  = var.agentcore_region
}

provider "aws" {
  alias   = "prod_useast1"
  profile = var.prod_profile
  region  = var.global_region
}

provider "aws" {
  alias   = "prod_uswest2"
  profile = var.prod_profile
  region  = var.agentcore_region
}
