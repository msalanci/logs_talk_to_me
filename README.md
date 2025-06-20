# Logs talk to me
This is the bot to have a chat with, about logs.

Analyzing CloudTrail logs can be complex, but what if you could simply ask, **_Tell me about the last 10 unsuccessful login attempts_**. 
This project builds a conversational interface for **CloudTrail** logs using **Amazon Bedrock's LLM (Antropic Claude Sonnet 3.7)**, **Amazon Lex** for intent, **AWS Lambda** to connect it all and querry the logs from **CloudTrail lake**.

It currently works only for CloudTrail logs regarding **_user management_**, but I am already working on more.

## Prerequisites & good to knows
1. You have to have your account(s) in **AWS Organizations**, with **Control Tower**. That's because it creates **Baseline Trail** and **S3 bucket** where all account(s) can send the CloudTrail logs

2. Everything (except the creating the AWS Organization with Control Tower) is in CloudFormation template in folder `/codes`

3. Later, the code in this repo creates the **CloudTrail Lake (Event Data Store)** to gather all the logs. This project needs in, because it issues **SQL querries** towards it.

4. I suggest to deploy templates in right order:
`1-iam.yaml`
`2-cloudtrail-lake.yaml`
`3-lambda.yaml`
`4-lex.yaml`

5. Make some logs :) Please understand that in CloudTrail Lake you will see only logs that were created **after** the lake was deployed. 

## Architectural overview and description

<img width="735" alt="Screenshot 2025-06-09 at 13 20 03" src="https://github.com/user-attachments/assets/d390c2f1-a4b6-425e-bd88-a05df5b42243" />

1. User makes a prompt to **Amazon Lex**.

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

This project is using a `bash` script called `alexandra.sh`, to invoke the **Amazon Lex**.


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