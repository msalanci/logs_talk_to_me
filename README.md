# Logs talk to me
This is the bot you can chat with, about your AWS environment.

Currently there are 3 versions available to fork and work with.
v1 - using Amazon Lex, single AWS Lambda function and Amazon Bedrock on 1 ivocation - **reading CloudTrail only**
v2 - using AWS API Gateway, 3 AWS Lambda functions and Amazon Bedrock on 3 separate invocations - **reading CloudTrail only**
v3 - reading multiple datasources and using AI agents - **Reading multiple sources**

All versons using local script `alexandra.sh` to interconnet between user's CLI and AWS

---

## LTTMv1 - Lex, Lambda, Bedrock

### Introduction to LTTMv1
LTTMv1 was moved to sepparate branch https://github.com/msalanci/logs_talk_to_me/tree/v1

This version is using **Amazon Lex** to: 
- Get the intent of the user's question
- Get the summary response from Lambda function and forwards it back to the user.

It also uses **AWS Lambda function** to: 
- Create SQL query
- Query the CloudTrail Event Data Store
- Send query output to Bedrock model for summary
- Wait for Bedrock response and forward it to Lex

### Deployment
using **CloudFormation**

---

## LTTMv2 - API GW, Lambdas, Bedrock

### Introduction to LTTMv2
LTTMv2 was moved to sepparate branch https://github.com/msalanci/logs_talk_to_me/tree/v2

This is more robust version than v1.
The main differences against v1 are:
- **Intent**, **SQL query** and **summarization** arw being done by sepparate AWS lambda functions.
- **No Amazon Lex** is used - for Intent we are now using Amazon Bedrock model


### Architectural overview
<img width="838" alt="Screenshot 2025-06-30 at 16 34 59" src="https://github.com/user-attachments/assets/4b28a8cd-fa98-444e-9174-8edfca920022" />

LTTMv2 uses API GW, 3 Lambda functions, each invoking Amazon Bedrock sepparately.

AWS Lambda function `lttm-v2-lambda-intent`:
- Is invoked by **API Gateway**.
- Reads the the user's question (input) and parsing it to appropriate format.
- Injects the input into a promt and send to **Amazon Bedrock model** to get the intent.
- Receives the intent from **Amazon Bedrock model** and sends it to AWS Lambda Function `lttm-v2-lambda-query`.
- Sends the final summary forwarded from AWS Lambda function `lttm-v2-lambda-query` back to **API Gateway**.

AWS Lambda function `lttm-v2-lambda-query`:
- Is invoked by **AWS Lambda function** `lttm-v2-lambda-intent`.
- Receives the original user input and intent from AWS Lambda function `lttm-v2-lambda-intent`.
- Parses all the data to appropriate format, injects into a prompt and sends to **Amazon Bedrock model** to determine the SQL query.
- Receives the SQL query created by **Amazon Bedrock model** and queries **CloudTrail Data Event Store**.
- Receives the SQL query output and sends it to AWS Lambda function `lttm-v2-lambda-summarizer`.
- Receives the final summarization from AWS Lambda function `lttm-v2-lambda-summarizer` and forwarding it to `lttm-v2-lambda-intent`.

AWS Lambda function `lttm-v2-lambda-summarizer`:
- Is invoked by AWS Lambda function `lttm-v2-lambda-query`.
- Receives original user's input and query output from AWS Lambda function `lttm-v2-lambda-query`.
- Determines the user's style (standard, funny, kids), injects all the data into prompt and sends to **Amazon Bedrock model** to explain the SQL query output.
- Receives the **Amazon Bedrock model** output and forwards it to AWS Lambda function `lttm-v2-lambda-query`, which then forwards it to AWS Lambda function `lttm-v2-lambda-intent`, which then forwards it to AWS API Gateway, from where it gets to the user.


### Deployment
with **CloudFormation** either:
- using `deploy_cf.sh` script, directly from CLI 
- manual deployment in CloudFormation console

---

### Introduction to v3

### Introduction to LTTMv2
LTTMv1 was moved to sepparate branch https://github.com/msalanci/logs_talk_to_me/tree/v3




### Architectural overview



### Deployment