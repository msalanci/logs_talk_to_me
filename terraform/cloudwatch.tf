# Copyright (c) 2026 Michal Salanci
# SPDX-License-Identifier: MIT


# =============================================================================
# cloudwatch.tf - Creates ClpudWatch -> Datalake pipeline
# Defines five Kinesis Firehose delivery streams and five CloudWatch Logs account-level subscription 
# filter policies — one pair per AWS each AWS account: AWS region 
#   - Stream 1 : lttm-firehose-main          (main account, default provider, eu-central-1)
#   - Stream 2 : lttm-firehose-dev           (dev account,  provider = aws.dev_eucentral1, eu-central-1)
#   - Stream 3 : lttm-firehose-prod          (prod account, provider = aws.prod_eucentral1, eu-central-1)
#   - Stream 4 : lttm-firehose-main-uswest2  (main account, provider = aws.default_uswest2, us-west-2)
#   - Stream 5 : lttm-firehose-main-useast1  (main account, provider = aws.default_useast1, us-east-1)
# =============================================================================


resource "aws_kinesis_firehose_delivery_stream" "main" {
  name        = "lttm-firehose-main"
  destination = "extended_s3"

  extended_s3_configuration {
    role_arn            = aws_iam_role.firehose_main.arn
    bucket_arn          = "arn:aws:s3:::${var.prefix}"
    prefix              = "cloudwatch/log_group=!{partitionKeyFromQuery:log_group}/account_id=!{partitionKeyFromQuery:account_id}/year=!{timestamp:yyyy}/month=!{timestamp:MM}/"
    error_output_prefix = "cloudwatch-errors/!{firehose:error-output-type}/year=!{timestamp:yyyy}/month=!{timestamp:MM}/"
    compression_format  = "UNCOMPRESSED"
    buffering_size      = 64 # MB — minimum required when dynamic partitioning is enabled
    buffering_interval  = 60 # seconds

    dynamic_partitioning_configuration {
      enabled = true
    }

    processing_configuration {
      enabled = true

      processors {
        type = "Decompression"
        parameters {
          parameter_name  = "CompressionFormat"
          parameter_value = "GZIP"
        }
      }

      processors {
        type = "MetadataExtraction"
        parameters {
          parameter_name  = "JsonParsingEngine"
          parameter_value = "JQ-1.6"
        }
        parameters {
          parameter_name  = "MetadataExtractionQuery"
          parameter_value = "{log_group:.logGroup,account_id:.owner}"
        }
      }
    }
  }
}

resource "aws_kinesis_firehose_delivery_stream" "dev" {
  provider    = aws.dev_eucentral1
  name        = "lttm-firehose-dev"
  destination = "extended_s3"

  extended_s3_configuration {
    role_arn            = aws_iam_role.firehose_cross_account_dev.arn
    bucket_arn          = "arn:aws:s3:::${var.prefix}"
    prefix              = "cloudwatch/log_group=!{partitionKeyFromQuery:log_group}/account_id=!{partitionKeyFromQuery:account_id}/year=!{timestamp:yyyy}/month=!{timestamp:MM}/"
    error_output_prefix = "cloudwatch-errors/!{firehose:error-output-type}/year=!{timestamp:yyyy}/month=!{timestamp:MM}/"
    compression_format  = "UNCOMPRESSED"
    buffering_size      = 64 # MB — minimum required when dynamic partitioning is enabled
    buffering_interval  = 60 # seconds

    dynamic_partitioning_configuration {
      enabled = true
    }

    processing_configuration {
      enabled = true

      processors {
        type = "Decompression"
        parameters {
          parameter_name  = "CompressionFormat"
          parameter_value = "GZIP"
        }
      }

      processors {
        type = "MetadataExtraction"
        parameters {
          parameter_name  = "JsonParsingEngine"
          parameter_value = "JQ-1.6"
        }
        parameters {
          parameter_name  = "MetadataExtractionQuery"
          parameter_value = "{log_group:.logGroup,account_id:.owner}"
        }
      }
    }
  }

  depends_on = [aws_iam_role.firehose_cross_account_dev]
}

resource "aws_kinesis_firehose_delivery_stream" "prod" {
  provider    = aws.prod_eucentral1
  name        = "lttm-firehose-prod"
  destination = "extended_s3"

  extended_s3_configuration {
    role_arn            = aws_iam_role.firehose_cross_account_prod.arn
    bucket_arn          = "arn:aws:s3:::${var.prefix}"
    prefix              = "cloudwatch/log_group=!{partitionKeyFromQuery:log_group}/account_id=!{partitionKeyFromQuery:account_id}/year=!{timestamp:yyyy}/month=!{timestamp:MM}/"
    error_output_prefix = "cloudwatch-errors/!{firehose:error-output-type}/year=!{timestamp:yyyy}/month=!{timestamp:MM}/"
    compression_format  = "UNCOMPRESSED"
    buffering_size      = 64 # MB — minimum required when dynamic partitioning is enabled
    buffering_interval  = 60 # seconds

    dynamic_partitioning_configuration {
      enabled = true
    }

    processing_configuration {
      enabled = true

      processors {
        type = "Decompression"
        parameters {
          parameter_name  = "CompressionFormat"
          parameter_value = "GZIP"
        }
      }

      processors {
        type = "MetadataExtraction"
        parameters {
          parameter_name  = "JsonParsingEngine"
          parameter_value = "JQ-1.6"
        }
        parameters {
          parameter_name  = "MetadataExtractionQuery"
          parameter_value = "{log_group:.logGroup,account_id:.owner}"
        }
      }
    }
  }

  depends_on = [aws_iam_role.firehose_cross_account_prod]
}

resource "aws_cloudwatch_log_account_policy" "main" {
  policy_name = "lttm-account-policy-main"
  policy_type = "SUBSCRIPTION_FILTER_POLICY"
  policy_document = jsonencode({
    DestinationArn = aws_kinesis_firehose_delivery_stream.main.arn
    FilterPattern  = ""
    Distribution   = "Random"
    RoleArn        = aws_iam_role.cwl_to_firehose_main.arn
  })

  depends_on = [aws_iam_role_policy.cwl_to_firehose_main]
}

resource "aws_cloudwatch_log_account_policy" "dev" {
  provider    = aws.dev_eucentral1
  policy_name = "lttm-account-policy-dev"
  policy_type = "SUBSCRIPTION_FILTER_POLICY"
  policy_document = jsonencode({
    DestinationArn = aws_kinesis_firehose_delivery_stream.dev.arn
    FilterPattern  = ""
    Distribution   = "Random"
    RoleArn        = aws_iam_role.cwl_to_firehose_dev.arn
  })

  depends_on = [aws_iam_role_policy.cwl_to_firehose_dev]
}

resource "aws_cloudwatch_log_account_policy" "prod" {
  provider    = aws.prod_eucentral1
  policy_name = "lttm-account-policy-prod"
  policy_type = "SUBSCRIPTION_FILTER_POLICY"
  policy_document = jsonencode({
    DestinationArn = aws_kinesis_firehose_delivery_stream.prod.arn
    FilterPattern  = ""
    Distribution   = "Random"
    RoleArn        = aws_iam_role.cwl_to_firehose_prod.arn
  })

  depends_on = [aws_iam_role_policy.cwl_to_firehose_prod]
}

resource "aws_kinesis_firehose_delivery_stream" "main_uswest2" {
  provider    = aws.default_uswest2
  name        = "lttm-firehose-main-uswest2"
  destination = "extended_s3"

  extended_s3_configuration {
    role_arn   = aws_iam_role.firehose_main_uswest2.arn
    bucket_arn = "arn:aws:s3:::${var.prefix}"

    prefix              = "cloudwatch/log_group=!{partitionKeyFromQuery:log_group}/account_id=!{partitionKeyFromQuery:account_id}/year=!{timestamp:yyyy}/month=!{timestamp:MM}/"
    error_output_prefix = "cloudwatch-errors/!{firehose:error-output-type}/year=!{timestamp:yyyy}/month=!{timestamp:MM}/"
    compression_format  = "UNCOMPRESSED"
    buffering_size      = 64
    buffering_interval  = 60

    dynamic_partitioning_configuration {
      enabled = true
    }

    processing_configuration {
      enabled = true

      processors {
        type = "Decompression"
        parameters {
          parameter_name  = "CompressionFormat"
          parameter_value = "GZIP"
        }
      }

      processors {
        type = "MetadataExtraction"
        parameters {
          parameter_name  = "JsonParsingEngine"
          parameter_value = "JQ-1.6"
        }
        parameters {
          parameter_name  = "MetadataExtractionQuery"
          parameter_value = "{log_group:.logGroup,account_id:.owner}"
        }
      }
    }
  }
}

resource "aws_cloudwatch_log_account_policy" "main_uswest2" {
  provider    = aws.default_uswest2
  policy_name = "lttm-account-policy-main-uswest2"
  policy_type = "SUBSCRIPTION_FILTER_POLICY"
  policy_document = jsonencode({
    DestinationArn = aws_kinesis_firehose_delivery_stream.main_uswest2.arn
    FilterPattern  = ""
    Distribution   = "Random"
    RoleArn        = aws_iam_role.cwl_to_firehose_main_uswest2.arn
  })

  depends_on = [aws_iam_role_policy.cwl_to_firehose_main_uswest2]
}

resource "aws_kinesis_firehose_delivery_stream" "main_useast1" {
  provider    = aws.default_useast1
  name        = "lttm-firehose-main-useast1"
  destination = "extended_s3"

  extended_s3_configuration {
    role_arn   = aws_iam_role.firehose_main_useast1.arn
    bucket_arn = "arn:aws:s3:::${var.prefix}"

    prefix              = "cloudwatch/log_group=!{partitionKeyFromQuery:log_group}/account_id=!{partitionKeyFromQuery:account_id}/year=!{timestamp:yyyy}/month=!{timestamp:MM}/"
    error_output_prefix = "cloudwatch-errors/!{firehose:error-output-type}/year=!{timestamp:yyyy}/month=!{timestamp:MM}/"
    compression_format  = "UNCOMPRESSED"
    buffering_size      = 64
    buffering_interval  = 60

    dynamic_partitioning_configuration {
      enabled = true
    }

    processing_configuration {
      enabled = true

      processors {
        type = "Decompression"
        parameters {
          parameter_name  = "CompressionFormat"
          parameter_value = "GZIP"
        }
      }

      processors {
        type = "MetadataExtraction"
        parameters {
          parameter_name  = "JsonParsingEngine"
          parameter_value = "JQ-1.6"
        }
        parameters {
          parameter_name  = "MetadataExtractionQuery"
          parameter_value = "{log_group:.logGroup,account_id:.owner}"
        }
      }
    }
  }
}

resource "aws_cloudwatch_log_account_policy" "main_useast1" {
  provider    = aws.default_useast1
  policy_name = "lttm-account-policy-main-useast1"
  policy_type = "SUBSCRIPTION_FILTER_POLICY"
  policy_document = jsonencode({
    DestinationArn = aws_kinesis_firehose_delivery_stream.main_useast1.arn
    FilterPattern  = ""
    Distribution   = "Random"
    RoleArn        = aws_iam_role.cwl_to_firehose_main_useast1.arn
  })

  depends_on = [aws_iam_role_policy.cwl_to_firehose_main_useast1]
}
