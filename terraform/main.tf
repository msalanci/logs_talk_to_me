# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

# main.tf — the AWS providers, one per account and Region the configuration manages: the default (no alias) is the
# main account in var.project_region, and the eight aliases are the other pairs of main, dev or prod with one of the
# three Regions, each with its account's CLI profile. Every provider puts local.common_tags on every resource through
# default_tags.

locals {
  common_tags = {
    project   = "lttm-v3"
    terraform = "yes"
  }
}

provider "aws" {
  profile = var.main_profile
  region  = var.project_region

  default_tags {
    tags = local.common_tags
  }
}

provider "aws" {
  alias   = "dev_eucentral1"
  profile = var.dev_profile
  region  = var.project_region

  default_tags {
    tags = local.common_tags
  }
}

provider "aws" {
  alias   = "prod_eucentral1"
  profile = var.prod_profile
  region  = var.project_region

  default_tags {
    tags = local.common_tags
  }
}

provider "aws" {
  alias   = "default_uswest2"
  profile = var.main_profile
  region  = var.agentcore_region

  default_tags {
    tags = local.common_tags
  }
}

provider "aws" {
  alias   = "default_useast1"
  profile = var.main_profile
  region  = var.global_region

  default_tags {
    tags = local.common_tags
  }
}

provider "aws" {
  alias   = "dev_useast1"
  profile = var.dev_profile
  region  = var.global_region

  default_tags {
    tags = local.common_tags
  }
}

provider "aws" {
  alias   = "dev_uswest2"
  profile = var.dev_profile
  region  = var.agentcore_region

  default_tags {
    tags = local.common_tags
  }
}

provider "aws" {
  alias   = "prod_useast1"
  profile = var.prod_profile
  region  = var.global_region

  default_tags {
    tags = local.common_tags
  }
}

provider "aws" {
  alias   = "prod_uswest2"
  profile = var.prod_profile
  region  = var.agentcore_region

  default_tags {
    tags = local.common_tags
  }
}
