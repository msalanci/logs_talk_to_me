# Copyright (c) 2026 Michal Salanci
# SPDX-License-Identifier: MIT


# =============================================================================
# athena.tf - Creates the query layer for the LTTM data lake, by Athena and Glue Data Catalog
# =============================================================================


resource "aws_athena_workgroup" "lttm" {
  name          = "lttm-athena-workgroup"
  state         = "ENABLED"
  force_destroy = true

  configuration {
    enforce_workgroup_configuration = true

    result_configuration {
      output_location = "s3://${var.prefix}/athena-results/"

      encryption_configuration {
        encryption_option = "SSE_S3"
      }
    }
  }
}

resource "aws_glue_catalog_database" "lttm_logs" {
  name = "lttm_logs"
}

resource "aws_glue_catalog_table" "cloudtrail_logs" {
  name          = "cloudtrail_logs"
  database_name = aws_glue_catalog_database.lttm_logs.name
  table_type    = "EXTERNAL_TABLE"

  storage_descriptor {
    location      = "s3://${var.prefix}/cloudtrail/"
    input_format  = "com.amazon.emr.cloudtrail.CloudTrailInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"

    ser_de_info {
      serialization_library = "org.apache.hive.hcatalog.data.JsonSerDe"
      parameters = {
        "serialization.format" = "1"
      }
    }

    columns {
      name = "eventid"
      type = "string"
    }
    columns {
      name = "eventtime"
      type = "string"
    }
    columns {
      name = "eventname"
      type = "string"
    }
    columns {
      name = "eventsource"
      type = "string"
    }
    columns {
      name = "awsregion"
      type = "string"
    }
    columns {
      name = "sourceipaddress"
      type = "string"
    }
    columns {
      name = "useridentity"
      type = "struct<type:string,principalid:string,arn:string,accountid:string>"
    }
    columns {
      name = "requestparameters"
      type = "string"
    }
    columns {
      name = "responseelements"
      type = "string"
    }
    columns {
      name = "errorcode"
      type = "string"
    }
    columns {
      name = "errormessage"
      type = "string"
    }
  }

  partition_keys {
    name = "account_id"
    type = "string"
  }
  partition_keys {
    name = "region"
    type = "string"
  }
  partition_keys {
    name = "year"
    type = "string"
  }
  partition_keys {
    name = "month"
    type = "string"
  }
  partition_keys {
    name = "day"
    type = "string"
  }

  parameters = {
    "projection.enabled"           = "true"
    "projection.account_id.type"   = "enum"
    "projection.account_id.values" = "${var.main_account_id},${var.dev_account_id},${var.prod_account_id}"
    "projection.region.type"       = "enum"
    "projection.region.values"     = "eu-central-1,us-east-1,us-west-2,eu-west-1,eu-west-2,eu-north-1,ap-southeast-1,ap-northeast-1,ap-south-1,sa-east-1,ca-central-1,us-east-2,us-west-1,af-south-1,ap-east-1,ap-southeast-2,me-south-1"
    "projection.year.type"         = "integer"
    "projection.year.range"        = "2023,2030"
    "projection.month.type"        = "integer"
    "projection.month.range"       = "1,12"
    "projection.month.digits"      = "2"
    "projection.day.type"          = "integer"
    "projection.day.range"         = "1,31"
    "projection.day.digits"        = "2"
    "storage.location.template"    = "s3://${var.prefix}/cloudtrail/AWSLogs/o-ty6mq0l4qa/$${account_id}/CloudTrail/$${region}/$${year}/$${month}/$${day}/"
    "classification"               = "json"
  }
}

resource "aws_glue_catalog_table" "cloudwatch_logs" {
  name          = "cloudwatch_logs"
  database_name = aws_glue_catalog_database.lttm_logs.name
  table_type    = "EXTERNAL_TABLE"

  storage_descriptor {
    location      = "s3://${var.prefix}/cloudwatch/"
    input_format  = "org.apache.hadoop.mapred.TextInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"

    ser_de_info {
      serialization_library = "org.openx.data.jsonserde.JsonSerDe"
      parameters = {
        "serialization.format" = "1"
      }
    }

    columns {
      name = "logevents"
      type = "array<struct<timestamp:bigint,message:string>>"
    }
    columns {
      name = "loggroup"
      type = "string"
    }
    columns {
      name = "logstream"
      type = "string"
    }
    columns {
      name = "owner"
      type = "string"
    }
    columns {
      name = "subscriptionfilters"
      type = "array<string>"
    }
  }

  partition_keys {
    name = "log_group"
    type = "string"
  }
  partition_keys {
    name = "account_id"
    type = "string"
  }
  partition_keys {
    name = "year"
    type = "string"
  }
  partition_keys {
    name = "month"
    type = "string"
  }

  parameters = {
    "projection.enabled"           = "true"
    "projection.log_group.type"    = "injected"
    "projection.account_id.type"   = "enum"
    "projection.account_id.values" = "${var.main_account_id},${var.dev_account_id},${var.prod_account_id}"
    "projection.year.type"         = "integer"
    "projection.year.range"        = "2024,2030"
    "projection.month.type"        = "integer"
    "projection.month.range"       = "1,12"
    "projection.month.digits"      = "2"
    "storage.location.template"    = "s3://${var.prefix}/cloudwatch/log_group=$${log_group}/account_id=$${account_id}/year=$${year}/month=$${month}/"
    "classification"               = "json"
  }
}

resource "aws_glue_catalog_table" "config_logs" {
  name          = "config_logs"
  database_name = aws_glue_catalog_database.lttm_logs.name
  table_type    = "EXTERNAL_TABLE"

  storage_descriptor {
    location      = "s3://${var.prefix}/config/"
    input_format  = "org.apache.hadoop.mapred.TextInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"

    ser_de_info {
      serialization_library = "org.openx.data.jsonserde.JsonSerDe"
      parameters = {
        "serialization.format" = "1"
      }
    }

    columns {
      name = "configurationitemcapturetime"
      type = "string"
    }
    columns {
      name = "resourcetype"
      type = "string"
    }
    columns {
      name = "resourceid"
      type = "string"
    }
    columns {
      name = "awsregion"
      type = "string"
    }
    columns {
      name = "availabilityzone"
      type = "string"
    }
    columns {
      name = "resourcecreationtime"
      type = "string"
    }
    columns {
      name = "configuration"
      type = "string"
    }
    columns {
      name = "supplementaryconfiguration"
      type = "string"
    }
    columns {
      name = "configurationitemstatus"
      type = "string"
    }
    columns {
      name = "configurationstateid"
      type = "string"
    }
    columns {
      name = "arn"
      type = "string"
    }
    columns {
      name = "awsaccountid"
      type = "string"
    }
    columns {
      name = "configurationitemdiff"
      type = "string"
    }
    columns {
      name = "relatedevents"
      type = "array<string>"
    }
  }

  partition_keys {
    name = "account_id"
    type = "string"
  }
  partition_keys {
    name = "year"
    type = "string"
  }
  partition_keys {
    name = "month"
    type = "string"
  }
  partition_keys {
    name = "day"
    type = "string"
  }

  parameters = {
    "projection.enabled"           = "true"
    "projection.account_id.type"   = "enum"
    "projection.account_id.values" = "${var.main_account_id},${var.dev_account_id},${var.prod_account_id}"
    "projection.year.type"         = "integer"
    "projection.year.range"        = "2024,2030"
    "projection.month.type"        = "integer"
    "projection.month.range"       = "1,12"
    "projection.month.digits"      = "2"
    "projection.day.type"          = "integer"
    "projection.day.range"         = "1,31"
    "projection.day.digits"        = "2"
    "storage.location.template"    = "s3://${var.prefix}/config/account_id=$${account_id}/year=$${year}/month=$${month}/day=$${day}/"
    "classification"               = "json"
  }
}

resource "aws_glue_catalog_table" "config_snapshot" {
  name          = "config_snapshot"
  database_name = aws_glue_catalog_database.lttm_logs.name
  table_type    = "EXTERNAL_TABLE"

  storage_descriptor {
    location      = "s3://${var.prefix}/config-snapshot/"
    input_format  = "org.apache.hadoop.mapred.TextInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"

    ser_de_info {
      serialization_library = "org.openx.data.jsonserde.JsonSerDe"
      parameters = {
        "serialization.format" = "1"
      }
    }

    columns {
      name = "configurationitemcapturetime"
      type = "string"
    }
    columns {
      name = "resourcetype"
      type = "string"
    }
    columns {
      name = "resourceid"
      type = "string"
    }
    columns {
      name = "awsregion"
      type = "string"
    }
    columns {
      name = "availabilityzone"
      type = "string"
    }
    columns {
      name = "resourcecreationtime"
      type = "string"
    }
    columns {
      name = "configuration"
      type = "string"
    }
    columns {
      name = "supplementaryconfiguration"
      type = "string"
    }
    columns {
      name = "configurationitemstatus"
      type = "string"
    }
    columns {
      name = "configurationstateid"
      type = "string"
    }
    columns {
      name = "arn"
      type = "string"
    }
    columns {
      name = "awsaccountid"
      type = "string"
    }
    columns {
      name = "configurationitemdiff"
      type = "string"
    }
    columns {
      name = "relatedevents"
      type = "array<string>"
    }
  }

  partition_keys {
    name = "account_id"
    type = "string"
  }
  partition_keys {
    name = "year"
    type = "string"
  }
  partition_keys {
    name = "month"
    type = "string"
  }
  partition_keys {
    name = "day"
    type = "string"
  }

  parameters = {
    "projection.enabled"           = "true"
    "projection.account_id.type"   = "enum"
    "projection.account_id.values" = "${var.main_account_id},${var.dev_account_id},${var.prod_account_id}"
    "projection.year.type"         = "integer"
    "projection.year.range"        = "2024,2030"
    "projection.month.type"        = "integer"
    "projection.month.range"       = "1,12"
    "projection.month.digits"      = "2"
    "projection.day.type"          = "integer"
    "projection.day.range"         = "1,31"
    "projection.day.digits"        = "2"
    "storage.location.template"    = "s3://${var.prefix}/config-snapshot/account_id=$${account_id}/year=$${year}/month=$${month}/day=$${day}/"
    "classification"               = "json"
  }
}

resource "aws_glue_catalog_table" "cur_data" {
  name          = "cur_data"
  database_name = aws_glue_catalog_database.lttm_logs.name
  table_type    = "EXTERNAL_TABLE"

  storage_descriptor {
    location      = "s3://${var.prefix}/cur/"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"

    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
      parameters = {
        "serialization.format" = "1"
      }
    }

    columns {
      name = "identity_line_item_id"
      type = "string"
    }
    columns {
      name = "identity_time_interval"
      type = "string"
    }

    columns {
      name = "bill_billing_period_start_date"
      type = "timestamp"
    }

    columns {
      name = "bill_billing_period_end_date"
      type = "timestamp"
    }
    columns {
      name = "bill_payer_account_id"
      type = "string"
    }
    columns {
      name = "line_item_usage_account_id"
      type = "string"
    }
    columns {
      name = "line_item_line_item_type"
      type = "string"
    }
    columns {
      name = "line_item_usage_start_date"
      type = "timestamp"
    }
    columns {
      name = "line_item_usage_end_date"
      type = "timestamp"
    }
    columns {
      name = "line_item_product_code"
      type = "string"
    }
    columns {
      name = "line_item_usage_type"
      type = "string"
    }
    columns {
      name = "line_item_operation"
      type = "string"
    }
    columns {
      name = "line_item_resource_id"
      type = "string"
    }
    columns {
      name = "line_item_usage_amount"
      type = "double"
    }
    columns {
      name = "line_item_unblended_cost"
      type = "double"
    }
    columns {
      name = "line_item_blended_cost"
      type = "double"
    }
    columns {
      name = "product_product_family"
      type = "string"
    }
    columns {
      name = "product_region_code"
      type = "string"
    }
    columns {
      name = "product_instance_type"
      type = "string"
    }
    columns {
      name = "pricing_term"
      type = "string"
    }
    columns {
      name = "pricing_unit"
      type = "string"
    }
    columns {
      name = "resource_tags"
      type = "map<string,string>"
    }
  }

  partition_keys {
    name = "billing_period"
    type = "string"
  }

  parameters = {
    "projection.enabled"               = "true"
    "projection.billing_period.type"   = "date"
    "projection.billing_period.format" = "yyyy-MM"
    "projection.billing_period.range"  = "2024-01,2030-12"
    "storage.location.template"        = "s3://${var.prefix}/cur/lttm-cur-export/data/BILLING_PERIOD=$${billing_period}/"
    "classification"                   = "parquet"
  }
}

resource "aws_glue_catalog_table" "flowlogs" {
  name          = "flowlogs"
  database_name = aws_glue_catalog_database.lttm_logs.name
  table_type    = "EXTERNAL_TABLE"

  storage_descriptor {
    location      = "s3://${var.prefix}/flowlogs/"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"

    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
      parameters = {
        "serialization.format" = "1"
      }
    }

    columns {
      name = "version"
      type = "int"
    }
    columns {
      name = "account_id"
      type = "string"
    }
    columns {
      name = "interface_id"
      type = "string"
    }
    columns {
      name = "srcaddr"
      type = "string"
    }
    columns {
      name = "dstaddr"
      type = "string"
    }
    columns {
      name = "srcport"
      type = "int"
    }
    columns {
      name = "dstport"
      type = "int"
    }
    columns {
      name = "protocol"
      type = "bigint"
    }
    columns {
      name = "packets"
      type = "bigint"
    }
    columns {
      name = "bytes"
      type = "bigint"
    }
    columns {
      name = "start"
      type = "bigint"
    }
    columns {
      name = "end"
      type = "bigint"
    }
    columns {
      name = "action"
      type = "string"
    }
    columns {
      name = "log_status"
      type = "string"
    }
  }

  partition_keys {
    name = "aws_account_id"
    type = "string"
  }
  partition_keys {
    name = "aws_region"
    type = "string"
  }
  partition_keys {
    name = "year"
    type = "string"
  }
  partition_keys {
    name = "month"
    type = "string"
  }
  partition_keys {
    name = "day"
    type = "string"
  }

  parameters = {
    "projection.enabled"               = "true"
    "projection.aws_account_id.type"   = "enum"
    "projection.aws_account_id.values" = "${var.main_account_id},${var.dev_account_id},${var.prod_account_id}"
    "projection.aws_region.type"       = "enum"
    "projection.aws_region.values"     = "${var.project_region},${var.agentcore_region}"
    "projection.year.type"             = "integer"
    "projection.year.range"            = "2024,2030"
    "projection.month.type"            = "integer"
    "projection.month.range"           = "1,12"
    "projection.month.digits"          = "2"
    "projection.day.type"              = "integer"
    "projection.day.range"             = "1,31"
    "projection.day.digits"            = "2"
    "storage.location.template"        = "s3://${var.prefix}/flowlogs/AWSLogs/aws-account-id=$${aws_account_id}/aws-service=vpcflowlogs/aws-region=$${aws_region}/year=$${year}/month=$${month}/day=$${day}/"
    "classification"                   = "parquet"
  }
}

resource "aws_glue_catalog_table" "guardduty_findings" {
  name          = "guardduty_findings"
  database_name = aws_glue_catalog_database.lttm_logs.name
  table_type    = "EXTERNAL_TABLE"

  storage_descriptor {
    location      = "s3://${var.prefix}/guardduty/"
    input_format  = "org.apache.hadoop.mapred.TextInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"

    ser_de_info {
      serialization_library = "org.openx.data.jsonserde.JsonSerDe"
      parameters = {
        "serialization.format" = "1"
      }
    }

    columns {
      name = "version"
      type = "string"
    }
    columns {
      name = "id"
      type = "string"
    }
    columns {
      name = "detail-type"
      type = "string"
    }
    columns {
      name = "source"
      type = "string"
    }
    columns {
      name = "account"
      type = "string"
    }
    columns {
      name = "time"
      type = "string"
    }
    columns {
      name = "region"
      type = "string"
    }
    columns {
      name = "detail"
      type = "string"
    }
  }

  partition_keys {
    name = "account_id"
    type = "string"
  }
  partition_keys {
    name = "year"
    type = "string"
  }
  partition_keys {
    name = "month"
    type = "string"
  }
  partition_keys {
    name = "day"
    type = "string"
  }

  parameters = {
    "projection.enabled"           = "true"
    "projection.account_id.type"   = "enum"
    "projection.account_id.values" = "${var.main_account_id},${var.dev_account_id},${var.prod_account_id}"
    "projection.year.type"         = "integer"
    "projection.year.range"        = "2024,2030"
    "projection.month.type"        = "integer"
    "projection.month.range"       = "1,12"
    "projection.month.digits"      = "2"
    "projection.day.type"          = "integer"
    "projection.day.range"         = "1,31"
    "projection.day.digits"        = "2"
    "storage.location.template"    = "s3://${var.prefix}/guardduty/account_id=$${account_id}/year=$${year}/month=$${month}/day=$${day}/"
    "classification"               = "json"
  }
}
