# Copyright (c) 2026 Michal Salanci
# SPDX-License-Identifier: MIT


# =====================================================================================
# macie.tf — Amazon Macie sensitive data discovery for LTTM v3.
#
# Enables Macie across all three LTTM accounts (main, dev, prod) in three regions: 
# (eu-central-1, us-east-1, us-west-2) for automated sensitive data discovery in S3 buckets
# =====================================================================================


# Macie Account Enablement
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


# Automated Sensitive Data Discovery
resource "terraform_data" "macie_discovery_main_eu" {
  depends_on = [aws_macie2_account.main_eu]

  input = {
    region  = var.project_region
    profile = "default"
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
    profile = "default"
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
    profile = "default"
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
    profile = "dev-cli"
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
    profile = "dev-cli"
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
    profile = "dev-cli"
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
    profile = "prod-cli"
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
    profile = "prod-cli"
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
    profile = "prod-cli"
  }

  provisioner "local-exec" {
    command = "aws macie2 update-automated-discovery-configuration --status ENABLED --region ${self.input.region} --profile ${self.input.profile}"
  }

  provisioner "local-exec" {
    when    = destroy
    command = "aws macie2 update-automated-discovery-configuration --status DISABLED --region ${self.input.region} --profile ${self.input.profile}"
  }
}
