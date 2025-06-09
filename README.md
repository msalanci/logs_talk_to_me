# Logs talk to me
This is the bot to have a chat with, about logs.

Analyzing CloudTrail logs can be complex, but what if you could simply ask, "Tell me about the last 10 unsuccessful login attempts"? This repo builds a conversational interface for CloudTrail logs using Amazon Bedrock's LLM (Antropic Claude Sonnet 3.7), Amazon Lex for intent, AWS Lambda to connect it all and querry the logs from CloudTrail lake.

It currentluy works only for CloudTrail logs regarding user management, but I am already working on more


## Architectural overview and description

<img width="735" alt="Screenshot 2025-06-09 at 13 20 03" src="https://github.com/user-attachments/assets/d390c2f1-a4b6-425e-bd88-a05df5b42243" />

User makes a prompt to Amazon Lex.  
Lex takes the user imput, understands the user intentions and prepares it for processing by Lambda.  
Lambda receives the user input from lex with the idea of user intention.  
Based on the intent, lambda queries cloudtrail lake and receive the query response, creates the prompt and send it to Bedrock.  
Bedrock then routes the prompt to the Claude 3.7 Sonnet foundation model.  
Foundation Model process it, creates an output and sends it back to Bedrock, which returns it back to the Lambda.  
Lambda builds the Lex response, send it to Lex, which then returns the output to the user and this is how it works in general.  

For invoking Amazon Lex, user can use various methods, such as:
1. Directly in AWS Console
2. Create a web frontned
3. Use a script

This project is using a bash script called Alaxandra, to invoke the Amazon Lex


### Usage example
