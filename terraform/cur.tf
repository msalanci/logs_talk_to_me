# Copyright (c) 2025-2026 Michal Salanci
# SPDX-License-Identifier: MIT

# cur.tf — the CUR 2.0 export lttm-cur-export (BCM Data Exports): the line items of all the accounts, as Parquet in the
# lake under cur/, which the cur_data table reads (athena.tf). The BCM Data Exports API is in us-east-1 only, hence
# the provider; the bucket stays in var.project_region.

resource "aws_bcmdataexports_export" "cur" {
  provider = aws.default_useast1

  export {
    name = "lttm-cur-export"

    data_query {
      query_statement = <<-EOT
        SELECT
          identity_line_item_id,
          identity_time_interval,
          bill_billing_period_start_date,
          bill_billing_period_end_date,
          bill_payer_account_id,
          line_item_usage_account_id,
          line_item_line_item_type,
          line_item_usage_start_date,
          line_item_usage_end_date,
          line_item_product_code,
          line_item_usage_type,
          line_item_operation,
          line_item_resource_id,
          line_item_usage_amount,
          line_item_unblended_cost,
          line_item_blended_cost,
          product_product_family,
          product_region_code,
          product_instance_type,
          pricing_term,
          pricing_unit,
          resource_tags
        FROM COST_AND_USAGE_REPORT
      EOT

      table_configurations = {
        "COST_AND_USAGE_REPORT" = {
          "BILLING_VIEW_ARN"                      = "arn:aws:billing::${var.main_account_id}:billingview/primary"
          "TIME_GRANULARITY"                      = "MONTHLY"
          "INCLUDE_RESOURCES"                     = "TRUE"
          "INCLUDE_MANUAL_DISCOUNT_COMPATIBILITY" = "FALSE"
          "INCLUDE_SPLIT_COST_ALLOCATION_DATA"    = "FALSE"
        }
      }
    }

    destination_configurations {
      s3_destination {
        s3_bucket = var.prefix
        s3_region = var.project_region
        s3_prefix = "cur"

        s3_output_configurations {
          output_type = "CUSTOM"
          format      = "PARQUET"
          compression = "PARQUET"
          overwrite   = "OVERWRITE_REPORT"
        }
      }
    }

    refresh_cadence {
      frequency = "SYNCHRONOUS"
    }
  }
}
