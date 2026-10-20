# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

# macie.tf — Macie in the three accounts and three Regions (eu-central-1, us-east-1, us-west-2): one aws_macie2_account
# each, and automated sensitive data discovery, which the provider has no resource for, switched on and off with the
# AWS CLI from a terraform_data. Each terraform_data keeps its Region and CLI profile in input, because a destroy-time
# provisioner can reference only self, not var.

resource "aws_macie2_account" "main_eu" {
  finding_publishing_frequency = "FIFTEEN_MINUTES"
  status                       = "ENABLED"
}

resource "aws_macie2_account" "main_useast1" {
  provider                     = aws.default_useast1
  finding_publishing_frequency = "FIFTEEN_MINUTES"
  status                       = "ENABLED"
}

resource "aws_macie2_account" "main_uswest2" {
  provider                     = aws.default_uswest2
  finding_publishing_frequency = "FIFTEEN_MINUTES"
  status                       = "ENABLED"
}

resource "aws_macie2_account" "dev_eu" {
  provider                     = aws.dev_eucentral1
  finding_publishing_frequency = "FIFTEEN_MINUTES"
  status                       = "ENABLED"
}

resource "aws_macie2_account" "dev_useast1" {
  provider                     = aws.dev_useast1
  finding_publishing_frequency = "FIFTEEN_MINUTES"
  status                       = "ENABLED"
}

resource "aws_macie2_account" "dev_uswest2" {
  provider                     = aws.dev_uswest2
  finding_publishing_frequency = "FIFTEEN_MINUTES"
  status                       = "ENABLED"
}

resource "aws_macie2_account" "prod_eu" {
  provider                     = aws.prod_eucentral1
  finding_publishing_frequency = "FIFTEEN_MINUTES"
  status                       = "ENABLED"
}

resource "aws_macie2_account" "prod_useast1" {
  provider                     = aws.prod_useast1
  finding_publishing_frequency = "FIFTEEN_MINUTES"
  status                       = "ENABLED"
}

resource "aws_macie2_account" "prod_uswest2" {
  provider                     = aws.prod_uswest2
  finding_publishing_frequency = "FIFTEEN_MINUTES"
  status                       = "ENABLED"
}

resource "terraform_data" "macie_discovery_main_eu" {
  depends_on = [aws_macie2_account.main_eu]

  input = {
    region  = var.project_region
    profile = var.main_profile
  }

  provisioner "local-exec" {
    command = "aws macie2 update-automated-discovery-configuration --status ENABLED --region ${self.input.region} --profile ${self.input.profile}"
  }

  provisioner "local-exec" {
    when    = destroy
    command = "aws macie2 update-automated-discovery-configuration --status DISABLED --region ${self.input.region} --profile ${self.input.profile}"
  }
}

resource "terraform_data" "macie_discovery_main_useast1" {
  depends_on = [aws_macie2_account.main_useast1]

  input = {
    region  = var.global_region
    profile = var.main_profile
  }

  provisioner "local-exec" {
    command = "aws macie2 update-automated-discovery-configuration --status ENABLED --region ${self.input.region} --profile ${self.input.profile}"
  }

  provisioner "local-exec" {
    when    = destroy
    command = "aws macie2 update-automated-discovery-configuration --status DISABLED --region ${self.input.region} --profile ${self.input.profile}"
  }
}

resource "terraform_data" "macie_discovery_main_uswest2" {
  depends_on = [aws_macie2_account.main_uswest2]

  input = {
    region  = var.agentcore_region
    profile = var.main_profile
  }

  provisioner "local-exec" {
    command = "aws macie2 update-automated-discovery-configuration --status ENABLED --region ${self.input.region} --profile ${self.input.profile}"
  }

  provisioner "local-exec" {
    when    = destroy
    command = "aws macie2 update-automated-discovery-configuration --status DISABLED --region ${self.input.region} --profile ${self.input.profile}"
  }
}

resource "terraform_data" "macie_discovery_dev_eu" {
  depends_on = [aws_macie2_account.dev_eu]

  input = {
    region  = var.project_region
    profile = var.dev_profile
  }

  provisioner "local-exec" {
    command = "aws macie2 update-automated-discovery-configuration --status ENABLED --region ${self.input.region} --profile ${self.input.profile}"
  }

  provisioner "local-exec" {
    when    = destroy
    command = "aws macie2 update-automated-discovery-configuration --status DISABLED --region ${self.input.region} --profile ${self.input.profile}"
  }
}

resource "terraform_data" "macie_discovery_dev_useast1" {
  depends_on = [aws_macie2_account.dev_useast1]

  input = {
    region  = var.global_region
    profile = var.dev_profile
  }

  provisioner "local-exec" {
    command = "aws macie2 update-automated-discovery-configuration --status ENABLED --region ${self.input.region} --profile ${self.input.profile}"
  }

  provisioner "local-exec" {
    when    = destroy
    command = "aws macie2 update-automated-discovery-configuration --status DISABLED --region ${self.input.region} --profile ${self.input.profile}"
  }
}

resource "terraform_data" "macie_discovery_dev_uswest2" {
  depends_on = [aws_macie2_account.dev_uswest2]

  input = {
    region  = var.agentcore_region
    profile = var.dev_profile
  }

  provisioner "local-exec" {
    command = "aws macie2 update-automated-discovery-configuration --status ENABLED --region ${self.input.region} --profile ${self.input.profile}"
  }

  provisioner "local-exec" {
    when    = destroy
    command = "aws macie2 update-automated-discovery-configuration --status DISABLED --region ${self.input.region} --profile ${self.input.profile}"
  }
}

resource "terraform_data" "macie_discovery_prod_eu" {
  depends_on = [aws_macie2_account.prod_eu]

  input = {
    region  = var.project_region
    profile = var.prod_profile
  }

  provisioner "local-exec" {
    command = "aws macie2 update-automated-discovery-configuration --status ENABLED --region ${self.input.region} --profile ${self.input.profile}"
  }

  provisioner "local-exec" {
    when    = destroy
    command = "aws macie2 update-automated-discovery-configuration --status DISABLED --region ${self.input.region} --profile ${self.input.profile}"
  }
}

resource "terraform_data" "macie_discovery_prod_useast1" {
  depends_on = [aws_macie2_account.prod_useast1]

  input = {
    region  = var.global_region
    profile = var.prod_profile
  }

  provisioner "local-exec" {
    command = "aws macie2 update-automated-discovery-configuration --status ENABLED --region ${self.input.region} --profile ${self.input.profile}"
  }

  provisioner "local-exec" {
    when    = destroy
    command = "aws macie2 update-automated-discovery-configuration --status DISABLED --region ${self.input.region} --profile ${self.input.profile}"
  }
}

resource "terraform_data" "macie_discovery_prod_uswest2" {
  depends_on = [aws_macie2_account.prod_uswest2]

  input = {
    region  = var.agentcore_region
    profile = var.prod_profile
  }

  provisioner "local-exec" {
    command = "aws macie2 update-automated-discovery-configuration --status ENABLED --region ${self.input.region} --profile ${self.input.profile}"
  }

  provisioner "local-exec" {
    when    = destroy
    command = "aws macie2 update-automated-discovery-configuration --status DISABLED --region ${self.input.region} --profile ${self.input.profile}"
  }
}
