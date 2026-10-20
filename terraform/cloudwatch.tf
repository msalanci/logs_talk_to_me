# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

# cloudwatch.tf — CloudWatch Logs into the lake: a Firehose stream and an account-level subscription filter policy for
# each account and Region with logs: main, dev and prod in eu-central-1, and main in us-west-2 and in us-east-1, where
# Route 53 writes its query logs. Each stream files the records under cloudwatch/log_group=…/account_id=…/ by dynamic
# partitioning, which needs a buffer of at least 64 MB. An account and Region take one subscription filter policy:
# import an existing one before the apply.

resource "aws_kinesis_firehose_delivery_stream" "main" {
  name        = "lttm-firehose-main"
  destination = "extended_s3"

  extended_s3_configuration {
    role_arn = aws_iam_role.firehose_main.arn

    bucket_arn = "arn:aws:s3:::${var.prefix}"

    prefix              = "cloudwatch/log_group=!{partitionKeyFromQuery:log_group}/account_id=!{partitionKeyFromQuery:account_id}/year=!{timestamp:yyyy}/month=!{timestamp:MM}/"
    error_output_prefix = "cloudwatch-errors/!{firehose:error-output-type}/year=!{timestamp:yyyy}/month=!{timestamp:MM}/"
    compression_format  = "UNCOMPRESSED"
    buffering_size      = 64
    buffering_interval  = 60

    dynamic_partitioning_configuration {
      enabled = true
    }

    # Decompression: CloudWatch Logs sends gzip. RecordDeAggregation is for KPL records and leaves the gzip as it
    # is, and CloudWatchLogProcessing drops logGroup and owner, the partition keys.
    processing_configuration {
      enabled = true

      processors {
        type = "Decompression"
        parameters {
          parameter_name  = "CompressionFormat"
          parameter_value = "GZIP"
        }
      }

      # Without a newline after each record, Athena's JSON reader sees only the first record of each file.
      processors {
        type = "AppendDelimiterToRecord"
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
    role_arn = aws_iam_role.firehose_cross_account_dev.arn

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
        type = "AppendDelimiterToRecord"
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

resource "aws_kinesis_firehose_delivery_stream" "prod" {
  provider    = aws.prod_eucentral1
  name        = "lttm-firehose-prod"
  destination = "extended_s3"

  extended_s3_configuration {
    role_arn = aws_iam_role.firehose_cross_account_prod.arn

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
        type = "AppendDelimiterToRecord"
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

resource "aws_cloudwatch_log_account_policy" "main" {
  policy_name = "lttm-account-policy-main"
  policy_type = "SUBSCRIPTION_FILTER_POLICY"
  policy_document = jsonencode({
    DestinationArn = aws_kinesis_firehose_delivery_stream.main.arn
    FilterPattern  = ""
    Distribution   = "Random"
    RoleArn        = aws_iam_role.cwl_to_firehose_main.arn
  })
  selection_criteria = length(var.main_log_groups_not_ingested) > 0 ? "LogGroupName NOT IN ${jsonencode(sort(var.main_log_groups_not_ingested))}" : null

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
        type = "AppendDelimiterToRecord"
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

# The AgentCore log groups hold every question, the generated SQL and internal names: they stay out of the lake. An
# account policy takes exact names only, so this lists the groups that exist at plan time. The evaluation results
# group is added by name: the service creates it during the apply that creates its configuration, after this list
# is read.
data "aws_cloudwatch_log_groups" "agentcore_runtimes" {
  provider              = aws.default_uswest2
  log_group_name_prefix = "/aws/bedrock-agentcore/"
}

locals {
  agentcore_log_groups = sort(distinct(concat(
    tolist(data.aws_cloudwatch_log_groups.agentcore_runtimes.log_group_names),
    [for config in aws_bedrockagentcore_online_evaluation_config.quality : config.output_config[0].cloudwatch_config[0].log_group_name],
    var.uswest2_telemetry_log_groups,
  )))
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
  selection_criteria = length(local.agentcore_log_groups) > 0 ? "LogGroupName NOT IN ${jsonencode(local.agentcore_log_groups)}" : null

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
        type = "AppendDelimiterToRecord"
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
