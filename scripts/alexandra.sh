#!/bin/bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Michal Salanci

# use it for LTTMV2 - aka: 3 models - by invoking API GW
# Usage: ./alexandra.sh "your question here"

# Start timer
START_TIME=$(date +%s)

# Retrieve the API GW URL
API_URL=$(aws cloudformation list-exports \
  --query "Exports[?Name=='LttmV2ApiInvokeUrl'].Value" \
  --output text)

# echo -e "\n\n API URL: $API_URL\n\n "

if [ -z "$API_URL" ]; then
    echo "ERROR: Could not retrieve API Gateway invoke URL from stack export LttmV2ApiInvokeUrl."
    exit 1
fi

TEMP_RESPONSE_FILE="/tmp/lambda_response.json"
QUESTION="$1"

if [ -z "$QUESTION" ]; then
    echo "Usage: $0 \"Your question here\""
    exit 1
fi

echo "Alexandra asking Bedrock model via API Gateway: $QUESTION"

# Create JSON payload
PAYLOAD=$(jq -n --arg q "$QUESTION" '{user_question: $q}')

# Invoke API Gateway (POST) and store response
curl -s -X POST "$API_URL" \
     -H "Content-Type: application/json" \
     -d "$PAYLOAD" > "$TEMP_RESPONSE_FILE"

# Extract and print only the summary
SUMMARY=$(jq -r '.summary' "$TEMP_RESPONSE_FILE")

# Stop timer
END_TIME=$(date +%s)
ELAPSED_TIME=$((END_TIME - START_TIME))

# Display summary
echo -e "\n$SUMMARY"

# Display time taken
echo -e "\n\nSummary took ${ELAPSED_TIME}s"




############# DIRECT INVOCATION #############
# #!/bin/bash

# # use it for LTTMV2 - aka: 3 models - by invoking the lambda function directly
# # Usage: ./alexandra.sh "your question here"

# # Retrieve lambda function name
# LAMBDA_NAME=$(aws cloudformation describe-stack-resources \
#   --stack-name lttm-v2-lambda-intent \
#   --query "StackResources[?ResourceType=='AWS::Lambda::Function'].PhysicalResourceId" \
#   --output text)

# REGION="eu-central-1"
# TEMP_RESPONSE_FILE="/tmp/lambda_response.json"

# QUESTION="$1"

# if [ -z "$QUESTION" ]; then
#     echo "Usage: $0 \"Your question here\""
#     exit 1
# fi

# echo "Alexandra asking Bedrock model via Lambda: $QUESTION"

# # Create JSON payload
# PAYLOAD=$(jq -n --arg q "$QUESTION" '{user_question: $q}')

# # Call Lambda and store output in file
# aws lambda invoke \
#   --function-name "$LAMBDA_NAME" \
#   --region "$REGION" \
#   --payload "$PAYLOAD" \
#   --cli-binary-format raw-in-base64-out \
#   "$TEMP_RESPONSE_FILE" > /dev/null

# # Extract and print only the summary
# SUMMARY=$(jq -r '.body' "$TEMP_RESPONSE_FILE" | jq -r '.summary')

# echo -e "\n$SUMMARY"
