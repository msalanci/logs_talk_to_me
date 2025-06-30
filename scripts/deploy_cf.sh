#!/bin/bash

# Usage:
#   ./scripts/deploy_cf.sh intent
#   ./scripts/deploy_cf.sh query
#   ./scripts/deploy_cf.sh summarizer
#   ./scripts/deploy_cf.sh utils
#   ./scripts/deploy_cf.sh bucket
#   ./scripts/deploy_cf.sh iam
#   ./scripts/deploy_cf.sh lake
#   ./scripts/deploy_cf.sh apigw

set -e

DEPLOY_TARGET=$1

if [ -z "$DEPLOY_TARGET" ]; then
  echo "Usage: $0 [intent|query|summarizer|utils|bucket|iam|lake]"
  exit 1
fi

# Infrastructure stacks (lttm-bucket, iam, lake)
if [ "$DEPLOY_TARGET" = "bucket" ]; then
    TEMPLATE="infrastructure/1-artifacts-bucket.yaml"
    STACK_NAME="lttm-v2-artifacts-bucket"
    echo "Deploying CloudFormation stack: $STACK_NAME using template: $TEMPLATE"
    aws cloudformation deploy \
        --stack-name "$STACK_NAME" \
        --template-file "$TEMPLATE" \
        --capabilities CAPABILITY_NAMED_IAM
    echo "Deployment of $STACK_NAME completed successfully."
    exit 0
elif [ "$DEPLOY_TARGET" = "iam" ]; then
    TEMPLATE="infrastructure/2-iam.yaml"
    STACK_NAME="iam"
    echo "Deploying CloudFormation stack: $STACK_NAME using template: $TEMPLATE"
    aws cloudformation deploy \
        --stack-name "$STACK_NAME" \
        --template-file "$TEMPLATE" \
        --capabilities CAPABILITY_NAMED_IAM
    echo "Deployment of $STACK_NAME completed successfully."
    exit 0
elif [ "$DEPLOY_TARGET" = "lake" ]; then
    TEMPLATE="infrastructure/3-cloudtrail-lake.yaml"
    STACK_NAME="lake"
    echo "Deploying CloudFormation stack: $STACK_NAME using template: $TEMPLATE"
    aws cloudformation deploy \
        --stack-name "$STACK_NAME" \
        --template-file "$TEMPLATE" \
        --capabilities CAPABILITY_NAMED_IAM
    echo "Deployment of $STACK_NAME completed successfully."
    exit 0
elif [ "$DEPLOY_TARGET" = "apigw" ]; then
    TEMPLATE="infrastructure/8-api-gw.yaml"
    STACK_NAME="lttm-v2-api-gw"
    echo "Deploying CloudFormation stack: $STACK_NAME using template: $TEMPLATE"
    aws cloudformation deploy \
        --stack-name "$STACK_NAME" \
        --template-file "$TEMPLATE" \
        --capabilities CAPABILITY_NAMED_IAM
    echo "Deployment of $STACK_NAME completed successfully."
    exit 0
fi

# Lambda & Layer deployments (lambda-intent, lambda-query, lambda-summarizer, layer-utils)
case "$DEPLOY_TARGET" in
  intent)
    ZIP="lttm_intent.zip"
    STACK_NAME="lttm-v2-lambda-intent"
    TEMPLATE="infrastructure/7-lambda-intent.yaml"
    S3_PATH="lttmv2/lambdas/$ZIP"
    ZIP_PATH="lambdas/lttm_intent"
    ;;
  query)
    ZIP="lttm_query.zip"
    STACK_NAME="lttm-v2-lambda-query"
    TEMPLATE="infrastructure/6-lambda-query.yaml"
    S3_PATH="lttmv2/lambdas/$ZIP"
    ZIP_PATH="lambdas/lttm_query"
    ;;
  summarizer)
    ZIP="lttm_summarizer.zip"
    STACK_NAME="lttm-v2-lambda-summarizer"
    TEMPLATE="infrastructure/5-lambda-summarizer.yaml"
    S3_PATH="lttmv2/lambdas/$ZIP"
    ZIP_PATH="lambdas/lttm_summarizer"
    ;;
  utils)
    ZIP="lttm_utils.zip"
    STACK_NAME="lttm-v2-layer-utils"
    TEMPLATE="infrastructure/4-layer-utils.yaml"
    S3_PATH="lttmv2/layers/$ZIP"
    ZIP_PATH="layer_build"
    ;;
  *)
    echo "Invalid argument: $DEPLOY_TARGET"
    echo "Usage: $0 [intent|query|summarizer|utils|bucket|iam|lake]"
    exit 1
    ;;
esac

echo "Zipping: $DEPLOY_TARGET"

rm -f $ZIP

if [ "$DEPLOY_TARGET" = "utils" ]; then
    # For layers, zip the python folder only
    cd $ZIP_PATH
    zip -r9 ../$ZIP python
    cd ..
else
    cd $ZIP_PATH
    zip -r9 ../../$ZIP .
    cd ../..
fi

ARTIFACTS_BUCKET=$(aws cloudformation list-exports --query "Exports[?Name=='LttmArtifactsBucket'].Value" --output text)

if [ -z "$ARTIFACTS_BUCKET" ]; then
  echo "ERROR: Could not retrieve LttmArtifactsBucket export. Ensure the bucket stack is deployed and exported."
  exit 1
fi
echo "Uploading to S3: s3://$ARTIFACTS_BUCKET/$S3_PATH"
aws s3 cp $ZIP s3://$ARTIFACTS_BUCKET/$S3_PATH

# echo "Uploading to S3: s3://lttm-artifacts-bucket/$S3_PATH"
# aws s3 cp $ZIP s3://lttm-artifacts-bucket/$S3_PATH

echo "Retrieving S3 VersionId..."
# VERSION_ID=$(aws s3api head-object --bucket lttm-artifacts-bucket --key "$S3_PATH" --query VersionId --output text)
VERSION_ID=$(aws s3api head-object --bucket "$ARTIFACTS_BUCKET" --key "$S3_PATH" --query VersionId --output text)
echo "Retrieved VersionId: $VERSION_ID"

echo "Deploying CloudFormation stack: $STACK_NAME"
aws cloudformation deploy \
  --stack-name $STACK_NAME \
  --template-file $TEMPLATE \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides LambdaZipVersion=$VERSION_ID

echo "Deployment of $DEPLOY_TARGET completed successfully."

rm -f $ZIP
echo "Cleaned up local zip: $ZIP"
