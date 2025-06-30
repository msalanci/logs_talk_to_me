# Logs talk to me
This is the bot to have a chat with, about logs.

Analyzing CloudTrail logs can be complex, but what if you could simply ask, **_Tell me about the last 10 unsuccessful login attempts_**.  
This project builds a conversational interface for **CloudTrail** logs using **Amazon Bedrock's LLM (Antropic Claude Sonnet 3.7)**, **Amazon Lex** for intent, **AWS Lambda** to connect it all and querry the logs from **CloudTrail lake**.

It currently works only for CloudTrail logs regarding **_user management_**, but I am already working on more.

### Prerequisites & good to knows
1. You have to have your account(s) in **AWS Organizations**, with **Control Tower**. That's because it creates **Baseline Trail** and **S3 bucket** where all account(s) can send the CloudTrail logs

2. Everything (except the creating the AWS Organization with Control Tower) is in CloudFormation template in folder `/codes`

3. Later, the code in this repo creates the **CloudTrail Lake (Event Data Store)** to gather all the logs. This project needs it, because it issues **SQL querries** towards it.

4. Make some logs :) Please understand that in CloudTrail Lake you will see only logs that were created **after** the lake was deployed.

5. Having AWS CLI and User in the AWS account is recommended.  


Currently there are 2 versions available to download and work with.
v1 - using Amazon Lex, single AWS Lambda function and Amazon Bedrock on 1 ivocation
v2 - using AWS API Gateway, 3 AWS Lambda functions and Amazon Bedrock on 3 sepparate invocations 


## V1 - Lex, Lambda, Bedrock
### Introduction to v1
This version is using **Amazon Lex** to: 
- Get the intent of the user's question
- Get the summary response from Lambda function and forwards it back to the user.

It also uses **AWS Lamnda function** to: 
- Create SQL query
- Query the CloudTrail Event Data Store
- Send query output to Bedrock model for summary
- Wait for Bedrock response and forward it to Lex


### Architectural overview and description of v1

<img width="735" alt="Screenshot 2025-06-09 at 13 20 03" src="https://github.com/user-attachments/assets/d390c2f1-a4b6-425e-bd88-a05df5b42243" />

1. User creates a question, which is sent to **Amazon Lex**.

2. **Amazon Lex** takes the user imput, understands the user intentions and prepares it for processing by **AWS Lambda**.

3. **AWS Lambda** receives the user input from **Amazon Lex**  with the idea of user intention.  
Based on the intent, **AWS Lambda** queries **cloudtrail lake** and receive the query response, creates the prompt and send it to **Amazon Bedrock**.

4. **Amazon Bedrock** then routes the prompt to the **Antropic Claude 3.7 Sonnet** foundation model.

5. Foundation Model process it, creates an output and sends it back to **Amazon Bedrock**, which returns it back to the **AWS Lambda**.

6. **AWS Lambda** builds the **Amazon Lex** response, send it to **Amazon Lex**, which then returns the output to the user and this is how it works in general.  

For invoking **Amazon Lex**, user can use various methods, such as:
- **Directly in AWS Console**
- **Create a web frontned**
- **Use a script**

This version is using a `bash` script called `alexandra.sh`, to invoke the **Amazon Lex**.

When deploying CloudFormation template, check for parameters and add everything it needs:
- To `1-iam.yaml` - nothing to add.

- To `2-cloudtrail-lake.yaml` - nothing to add.

- To `3-lambda.yaml` - add Bedrock Model ID. This code works with Antropic Cloude 3.7 and it's a default option in Cloud Formation parameters.

- To `4-lex.yaml` - add AWS account IDs you are using (make sure to have AWS Organizations and Control Tower).
This project is using 3 accounts, but if you have more or less, please update  
**Parameters (lines 5-21)**:
```yaml
Parameters:
  # Use as many account IDs as you have. Read the readme.md of you have more or less then 3
  AccountId1:
    Description: Account ID 1
    Type: String

  AccountId2:
    Description: Account ID 2
    Type: String

  AccountId3:
    Description: Account ID 3
    Type: String

  # AccountIdn:
  #   Description: Account ID 3
  #   Type: String
```

and **AccountIdSlotType (lines 66-77)**:
```yaml
- Name: AccountIdSlotType
   SlotTypeValues:
      - SampleValue:
         Value: !Sub "${AccountId1}"
      - SampleValue:
         Value: !Sub "${AccountId2}"
      - SampleValue:
         Value: !Sub "${AccountId3}"
      # - SampleValue:
      #     Value: !Sub "${AccountIdn}"
   ValueSelectionSetting:
      ResolutionStrategy: ORIGINAL_VALUE
```

- To `alexandra.sh`, add **Lex Bot ID** and **Lex Alias ID** - It's outputed in `4-lex.yaml`, just for this purpose. You have to manually copy them into `alexandra.sh`.


### Deployment
I suggest to deploy templates in right order:
`1-iam.yaml`
`2-cloudtrail-lake.yaml`
`3-lambda.yaml`
`4-lex.yaml`
So far, templated must be deployed manualy.

### Usage example
From your terminal call `alexandra.sh` and state your question:

```
./alexandra.sh "last 3 user names to account 123456789012"
```

The answer will follow:

```
Alexandra asking Lex: last 3 user names to account 123456789012
# Users in Account Summary

1. User: HIDDEN_DUE_TO_SECURITY_REASONS
   - Source IP: 65.10.8.46
   - Login Status: SUCCESSFUL

2. User: JohnDOE
   - ARN: arn:aws:iam::123456789012:user/JohnDOE
   - Source IP: 65.10.8.46
   - Login Status: SUCCESSFUL

3. User: AccountAdmin
   - ARN: arn:aws:iam::123456789012:user/AccountAdmin
   - Source IP: 158.23.57.101
   - Login Status: SUCCESSFUL
```

### V1 Limitations
This version can only work with **limited amount of intents**, so only questions regarding **user management** are valid.
For more robust version, please proceed to **v2**.

## V2 - API GW, Lambdas, Bedrock
### Introduction to v2
This is more robust version than v1.
The main differences against v1 are:
- **Intent**, **SQL querry** and **summarization** arw being done by sepparate AWS Lamnda functions.
- **No Amazon Lex** is used - for Intent we are now using Amazon Bedrock model


### Architectural overview and description of v2
LTTMv2 uses API GW, 3 Lambda functions, each invoking Amazon Bedrock sepparately.

AWS Lambda function `lttm-v2-lambda-intent`:
- Is invoked by **API Gateway**.
- Reads the the user's question (input) and parsing it to appropriate format.
- Injects the input into a promt and send to **Amazon Bedrock model** to get the intent.
- Recveives the intent from **Amazon Bedrock model** and sends it to AWS Lambda Function `lttm-v2-lambda-query`.
- Sends the final summary forwarded from AWS Lambda function `lttm-v2-lambda-query` back to **API Gateway**.

AWS Lambda function `lttm-v2-lambda-query`:
- Is invoked by **AWS Lambda function** `lttm-v2-lambda-intent`.
- Receives the original user input and intent from AWS Lambda function `lttm-v2-lambda-intent`.
- Parses all the data to appropriate format, injects into a prompt and sends to **Amazon Bedrock model** to determine the SQL querry.
- Receives the SQL query created by **Amazon Bedrock model** and queries **CloudTrail Data Event Store**.
- Receives the SQL query output and sends it to AWS Lambda function `lttm-v2-lambda-summarizer`.
- Receives the final summarization from AWS Lambda function `lttm-v2-lambda-summarizer` and forwarding it to `lttm-v2-lambda-intent`.

AWS Lambda function `lttm-v2-lambda-summarier`:
- Is invoked by AWS Lambda function `lttm-v2-lambda-query`.
- Receives original user's input and querry output from AWS Lambda function `lttm-v2-lambda-query`.
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
`infrastructure/1-artifacts-bucket.yaml` - CloudFormation template to create S3 bucket for all artifacts, such as lamnda function codes.
`infrastructure/2-iam.yaml` - CloudFormation template for all IAM roles needed in this project.
`infrastructure/3-cloudtrail-lake.yaml` - CloudFormation template to create CloudTrail Event Data Store.
`infrastructure/4-layer-utils.yaml` - CloudFormation template to create Lambda Layer.
`infrastructure/5-lambda-summarizer.yaml` - CloudFormation template to create lttm-v2-lamnda-summarize function.
`infrastructure/6-lambda-query.yaml` - CloudFormation template to create lttm-v2-lamnda-query function.
`infrastructure/7-lambda-intent.yaml` - CloudFormation template to create lttm-v2-lamnda-intent function.
`infrastructure/8-api-gw.yaml` - CloudFormation template to create API Gateway.

`lambdas/lttm_intent/lambda_function.py` - Python 3.12 code for lttm-v2-lamnda-intent function, being stored in S3 bucket for artifacts.
`lambdas/lttm_query/lambda_function.py` - Python 3.12 code for lttm-v2-lamnda-query function, being stored in S3 bucket for artifacts.
`lambdas/lttm_summarizer/lambda_function.py` - Python 3.12 code for lttm-v2-lamnda-summarize function, being stored in S3 bucket for artifacts.

`lttm_utils/prompt_intent.py` - Helper module to create Bedrock prompt for lttm-v2-lamnda-intent function.
`lttm_utils/prompt_query.py` - Helper module to create Bedrock prompt for lttm-v2-lamnda-query function.
`lttm_utils/prompt_summarizer.py` - Helper module to create Bedrock prompt for lttm-v2-lamnda-summarizer function.
`lttm_utils/utils.py` - Reusable content for all lambnda functions.

`scripts/alexandra.sh` - Bash script for users to ask the questions.  
`scripts/deploy_cf.sh` - Bash script to deploy CloudFormation template.  


### Deployment
Deployment is being done by CloudFormation, with `deploy_cf.sh` script, directly from CLI. 
If you don't have the AWS CLI and IAM User in the AWS account, you can deploy it manually



### Usage example


