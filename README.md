# Logs talk to me
This is the bot you can chat with, about logs.

Analyzing CloudTrail logs can be complex, but what if you could simply ask, **_Tell me about the last 10 unsuccessful login attempts_**.  
This project provides a conversational interface for analyzing **CloudTrail** logs using **Amazon Bedrock's LLM (Antropic Claude Sonnet 3.7)**, **Amazon Lex** for intent, **AWS Lambda** to connect it all and query the logs from **CloudTrail lake**.

It currently supports only CloudTrail logs related **_user management_**. For more broader version, please check LTTMv2.

### Prerequisites & good to knows
1. You have to have your account(s) in **AWS Organizations**, with **Control Tower**. That's because it creates **Baseline Trail** and **S3 bucket** where all account(s) can send the CloudTrail logs

2. Everything (except for creating the AWS Organization with Control Tower) is in CloudFormation template in folder `/codes`

3. Later, the code in this repo creates the **CloudTrail Lake (Event Data Store)** to gather all the logs. This project requires it, as it uses **SQL querries** to retrieve logs.

4. Make some logs :) Please understand that in CloudTrail Lake you will see only logs that were created **after** the lake was deployed.

5. Having AWS CLI and User in the AWS account is recommended.  

---

## V2 - API GW, Lambdas, Bedrock
LTTMv2 staus currently in branch **master** https://github.com/msalanci/logs_talk_to_me/tree/v2

### Introduction to v2
This is more robust version than v1.
The main differences against v1 are:
- **Intent**, **SQL query** and **summarization** arw being done by sepparate AWS lambda functions.
- **No Amazon Lex** is used - for Intent we are now using Amazon Bedrock model

### Architectural overview and description of v2

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

### Folder and file structure
Files and folders are structured as follows:

```
├── infrastructure
│   ├── 1-artifacts-bucket.yaml
│   ├── 2-iam.yaml
│   ├── 3-cloudtrail-lake.yaml
│   ├── 4-layer-utils.yaml
│   ├── 5-lambda-summarizer.yaml
│   ├── 6-lambda-query.yaml
│   ├── 7-lambda-intent.yaml
│   └── 8-api-gw.yaml
├── lambdas
│   ├── lttm_intent
│   │   └── lambda_function.py
│   ├── lttm_query
│   │   └── lambda_function.py
│   └── lttm_summarizer
│       └── lambda_function.py
├── lttm_utils
│   ├── prompt_intent.py
│   ├── prompt_query.py
│   ├── prompt_summarizer.py
│   └── utils.py
└── scripts
    ├── alexandra.sh
    └── deploy_cf.sh
```

##### Folders
`infrastructure/` contains AWS CloudFormation templates.  

`lambdas/` contains lambda functions codes.  

`lttm_utils/` contains lambda layers and helper functions.  

`scripts/` contains scripts to deploy or run the project.  


##### Files
`infrastructure/1-artifacts-bucket.yaml` - CloudFormation template to create S3 bucket for all artifacts, such as lambda function codes.  
`infrastructure/2-iam.yaml` - CloudFormation template for all IAM roles needed in this project.  
`infrastructure/3-cloudtrail-lake.yaml` - CloudFormation template to create CloudTrail Event Data Store.  
`infrastructure/4-layer-utils.yaml` - CloudFormation template to create Lambda Layer.  
`infrastructure/5-lambda-summarizer.yaml` - CloudFormation template to create lttm-v2-lambda-summarizer function.  
`infrastructure/6-lambda-query.yaml` - CloudFormation template to create lttm-v2-lambda-query function.  
`infrastructure/7-lambda-intent.yaml` - CloudFormation template to create lttm-v2-lambda-intent function.  
`infrastructure/8-api-gw.yaml` - CloudFormation template to create API Gateway.  

`lambdas/lttm_intent/lambda_function.py` - Python 3.12 code for lttm-v2-lambda-intent function, being stored in S3 bucket for artifacts.  
`lambdas/lttm_query/lambda_function.py` - Python 3.12 code for lttm-v2-lambda-query function, being stored in S3 bucket for artifacts.  
`lambdas/lttm_summarizer/lambda_function.py` - Python 3.12 code for lttm-v2-lambda-summarize function, being stored in S3 bucket for artifacts.  

`lttm_utils/prompt_intent.py` - Helper module to create Bedrock prompt for lttm-v2-lambda-intent function.  
`lttm_utils/prompt_query.py` - Helper module to create Bedrock prompt for lttm-v2-lambda-query function.  
`lttm_utils/prompt_summarizer.py` - Helper module to create Bedrock prompt for lttm-v2-lambda-summarizer function.  
`lttm_utils/utils.py` - Reusable content for all lambnda functions.  

`scripts/alexandra.sh` - Bash script for users to ask the questions.  
`scripts/deploy_cf.sh` - Bash script to deploy CloudFormation template.   


### Deployment
Deployment is being done by CloudFormation, with `deploy_cf.sh` script, directly from CLI.  
If you don't have the AWS CLI and IAM User in the AWS account, you can deploy it manually


### Usage example
0. **Prerequisites**
- Make sure to have AWS Organizations with AWS Control Tower

1. **Initial deployment**
- Use script `deploy_cf.sh` to deploy CloudFormation template
- Run it from the root folder of the project
- Follow this principle and order:

Deploy S3 bucket for artifacts:
```bash
./scripts/deploy_cf.sh s3
```

Deploy IAM roles:
```bash
./scripts/deploy_cf.sh iam
```

Deploy CloudTrail Event Data Store:
```bash
./scripts/deploy_cf.sh lake
```

Deploy Lambda layers:
```bash
./scripts/deploy_cf.sh utils
```

Deploy Lambda function lttm-v2-lambda-summarizer:
```bash
./scripts/deploy_cf.sh summarizer
```

Deploy Lambda function lttm-v2-lambda-query:
```bash
./scripts/deploy_cf.sh query
```

Deploy Lambda function lttm-v2-lambda-intent:
```bash
./scripts/deploy_cf.sh intent
```

Deploy API Gateway:
```bash
./scripts/deploy_cf.sh intent
```

2. **Run the project**  
From commandline of root directory, run:
```bash
./script/alexandra.sh "<your question>"
```

---
