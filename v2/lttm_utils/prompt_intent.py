# lttm_utils/prompt_intent.py (Lambda Layer for LTTMv2)

"""
Prompt builder for the lambda_intent Lambda in LTTMv2.

- Defines the intent catalog with descriptions.
- Generates the structured prompt for Bedrock intent classification and slot extraction.
- Used by lambda_intent to ensure consistent, clear, and controlled prompting.
"""


# Maps intent names to their descriptions for user question classification
intent_descriptions = {
    "FindFailedOperationsIntent": "Detect failed or denied AWS operations.",
    "FindSlowApiCallsIntent": "Detect unusually long AWS API execution durations.",
    "FindUnauthorizedAccessIntent": "Identify events where users received AccessDenied or Unauthorized errors.",
    "ListAccountContactChangesIntent": "Display changes to account alternate contacts or address/phone.",
    "ListApiCallsByUserIntent": "Find who performed specific AWS API operations.",
    "ListApiGatewayKeyChangesIntent": "Track creation or deletion of API Gateway keys & usage plans.",
    "ListAssumeRoleEventsIntent": "Show who assumed IAM roles and when.",
    "ListAthenaQueryExecutionsIntent": "Show Athena query execution events (useful for data-exfil tracking).",
    "ListAutoscalingGroupActivityIntent": "Display scaling activities (manual or policy-driven) in Auto Scaling groups.",
    "ListAuroraFailoversIntent": "Find automated or manual Aurora failover events.",
    "ListBackupVaultDeletionsIntent": "Track deletions of AWS Backup vaults or recovery points.",
    "ListBatchJobSubmissionsIntent": "Track AWS Batch job submissions and queue modifications.",
    "ListBillingAnomaliesIntent": "Identify unusual billing or cost allocation tag changes.",
    "ListBucketDeletionsIntent": "Track who deleted S3 buckets.",
    "ListCertificateManagerChangesIntent": "Track SSL/TLS certificate requests, imports, or deletions in ACM.",
    "ListChangesByIpIntent": "Show AWS activity from a specific IP address.",
    "ListChangesByUserIntent": "Show actions performed by a specific IAM user.",
    "ListCloudFormationStackChangesIntent": "Track changes in CloudFormation stacks.",
    "ListCloudFrontDistributionChangesIntent": "Show creation, deletion, or config changes to CloudFront distributions.",
    "ListCloudTrailConfigChangesIntent": "Track changes made to CloudTrail trails and settings.",
    "ListCloudTrailStopStartLoggingIntent": "Catch StartLogging or StopLogging calls on CloudTrail trails.",
    "ListCloudWatchAlarmChangesIntent": "Track CloudWatch alarm creation, modification, or state changes.",
    "ListConfigRecorderChangesIntent": "Show updates to AWS Config recorders or rules.",
    "ListConfigServiceStopIntent": "Identify when AWS Config recording was stopped or started.",
    "ListConsoleLoginsIntent": "Show successful AWS Console login events.",
    "ListCostAnomalyDetectorChangesIntent": "Identify changes to Cost Anomaly Detectors or budgets.",
    "ListDataSyncTaskRunsIntent": "Track DataSync task executions (potential bulk data movement).",
    "ListDirectConnectChangesIntent": "Track Direct Connect virtual interface or gateway changes.",
    "ListDocumentDbClusterChangesIntent": "Track DocumentDB cluster modifications or snapshot operations.",
    "ListDynamoDbTableChangesIntent": "Show DynamoDB table creation, deletion, or backup/restore operations.",
    "ListEcsDeploymentsIntent": "List ECS deployment events.",
    "ListEcsTaskDefinitionChangesIntent": "Show ECS task definition updates or service modifications.",
    "ListEksAccessKubeApiIntent": "Show AssumeRole or STS calls used to access the Kubernetes API.",
    "ListEksClusterChangesIntent": "Track creation or deletion of EKS clusters, node groups, and IAM roles.",
    "ListElasticsearchDomainChangesIntent": "Show OpenSearch/Elasticsearch domain modifications or access policy changes.",
    "ListElbTargetGroupChangesIntent": "Track load-balancer target-group modifications or health-check changes.",
    "ListEmrClusterActivitiesIntent": "Display EMR cluster launches, terminations, and step executions.",
    "ListEventBridgeRuleChangesIntent": "Identify creation, deletion, or modification of EventBridge rules and targets.",
    "ListFailedLoginAttemptsIntent": "Show failed login attempts to the AWS Console.",
    "ListFargateTaskLaunchesIntent": "Identify Fargate task launches and their configurations.",
    "ListGlacierVaultDeletionsIntent": "Track deletion of Glacier vaults or archives.",
    "ListGlobalAcceleratorChangesIntent": "Show Global Accelerator endpoint or listener modifications.",
    "ListGlueJobRunsIntent": "Show Glue job runs and who initiated them.",
    "ListGuardDutyDisableIntent": "Detect GuardDuty being disabled for an account or region.",
    "ListGuardDutySuppressionIntent": "Identify GuardDuty findings that were archived or suppressed.",
    "ListHighCostActionsIntent": "Identify API calls related to costly AWS operations.",
    "ListIamInlinePolicyEditsIntent": "Detect changes to IAM inline policies.",
    "ListIamPassRoleUsageIntent": "Show when iam:PassRole was used and by whom.",
    "ListIamPermissionBoundaryChangesIntent": "Track the addition or removal of IAM permission boundaries.",
    "ListIamRoleTrustPolicyChangesIntent": "Show modifications to role trust relationships.",
    "ListInstanceLaunchesIntent": "List EC2 instance launches and the initiators.",
    "ListKinesisStreamChangesIntent": "Track creation, deletion, or scaling of Kinesis data streams.",
    "ListKmsKeyUsageIntent": "Identify KMS key usage for encryption/decryption operations and key policy changes.",
    "ListLakeFormationPermissionChangesIntent": "Track data lake permission grants or revocations in Lake Formation.",
    "ListLambdaFunctionChangesIntent": "Track Lambda function creation, updates, or permission changes.",
    "ListLambdaInvocationsIntent": "Show invocations of Lambda functions.",
    "ListLightsailInstanceChangesIntent": "Show Lightsail instance creation, deletion, or snapshot operations.",
    "ListMacieFindingEventsIntent": "Retrieve Macie finding events (PII discovery).",
    "ListMarketplaceSubscriptionChangesIntent": "Track AWS Marketplace subscription changes or software installations.",
    "ListMfaChangesIntent": "Track enabling/disabling of MFA for IAM users.",
    "ListNatGatewayChangesIntent": "Show NAT Gateway creation, deletion, or route table associations.",
    "ListNetworkChangesIntent": "Track changes in VPCs, route tables, subnets, and gateways.",
    "ListNitroEnclaveCreationsIntent": "Detect creation of Nitro Enclaves.",
    "ListObjectLevelAccessIntent": "Show GetObject / PutObject API calls on sensitive S3 buckets.",
    "ListOrganizationAccountChangesIntent": "Track new AWS accounts joining or leaving the organization.",
    "ListParameterStoreAccessIntent": "Show access to SSM Parameter Store SecureString parameters.",
    "ListPersonalizeModelTrainingIntent": "Track Amazon Personalize model training activities and dataset imports.",
    "ListPolicyChangesIntent": "Display changes to IAM policies or permissions.",
    "ListPresignedUrlCreationsIntent": "Identify generation of S3 presigned URLs.",
    "ListPubliclyExposedBucketsIntent": "Detect S3 buckets that were made public or had ACL changes allowing public access.",
    "ListQuicksightShareChangesIntent": "Detect new QuickSight dashboards or datasets shared externally.",
    "ListRedshiftClusterChangesIntent": "Track creation, resize, or deletion of Redshift clusters.",
    "ListResourceCreationEventsIntent": "List resource creation events that could impact cost.",
    "ListResourceGroupChangesIntent": "Show resource group creation or tag-based group modifications.",
    "ListResourceTagChangesIntent": "Track tagging changes on AWS resources.",
    "ListRootUserActivityIntent": "Display all actions taken by the AWS root user.",
    "ListRoute53HostedZoneTransfersIntent": "Identify transfers or deletions of Route 53 hosted zones.",
    "ListS3BucketEncryptionChecksIntent": "Identify buckets missing default encryption (meta-intent, optional).",
    "ListSageMakerNotebookStartsIntent": "Identify who started or stopped SageMaker notebooks.",
    "ListSavingsPlanPurchasesIntent": "Track who purchased or modified Savings Plans or Reserved Instances.",
    "ListSecretsAccessIntent": "Track access to secrets in AWS Secrets Manager.",
    "ListSecretsManagerSecretAccessIntent": "Track access to AWS Secrets Manager secrets, including GetSecretValue calls.",
    "ListSecurityGroupChangesIntent": "Show modifications to security groups.",
    "ListSecurityHubFindingsDismissedIntent": "Track Security Hub findings that were set to SUPPRESSED.",
    "ListServiceErrorsIntent": "Show API calls that resulted in AWS service errors.",
    "ListServiceLinkedRoleCreationsIntent": "Identify creation of service-linked roles.",
    "ListSnapshotSharingIntent": "Identify snapshots shared outside the AWS account.",
    "ListSsmRunCommandExecutionsIntent": "Show SSM Run Command / Session Manager activities.",
    "ListSupportCaseCreationsIntent": "Show creation of new AWS Support cases.",
    "ListThrottlingEventsIntent": "List events where AWS APIs returned throttling errors.",
    "ListTransitGatewayAttachmentChangesIntent": "Track new or removed TGW attachments.",
    "ListUnauthorizedS3AccessIntent": "Detect S3 GetObject calls from unrecognized IP ranges (example custom intent).",
    "ListUserCreationIntent": "Identify who created new IAM users.",
    "ListUsersInAccountIntent": "List IAM users or identities in an AWS account.",
    "ListVpnConnectionChangesIntent": "Display creation, deletion, or modification of Site-to-Site VPN connections.",
    "ListWafRuleChangesIntent": "Show modifications to WAF rules, IP sets, or web ACLs.",
    "ListWorkspacesActivityIntent": "Track WorkSpaces creation, termination, or user session activities.",
    "ListXrayTracingChangesIntent": "Show X-Ray tracing configuration changes or sampling rule updates.",
    "ListResourcesIntent": "List resources (e.g., S3 buckets, Lambda functions, EC2 instances, IAM users, DynamoDB tables, RDS instances, CloudFormation stacks, CloudFront distributions, VPCs, Security Groups, Load Balancers, Elastic IPs, EBS volumes, SNS topics, SQS queues, KMS keys, Secrets Manager secrets, API Gateway APIs, EventBridge rules, CloudWatch alarms, Route 53 hosted zones, ECR repositories, Transit Gateways, Elasticache clusters) in an AWS account."
}

# Prepares the intent block as a JSON snippet for the Bedrock prompt
intent_block = ",\n".join(
    [f'  "{k}": "{v}"' for k, v in intent_descriptions.items()]
)


def build_intent_prompt(user_question: str, main_account: str) -> str:
    """
    Constructs a structured prompt for Bedrock to classify a user's question into a predefined intent
    and to extract slots for the LTTMv2 lambda_intent Lambda.

    Args:
        user_question (str): The user's natural language question.
        main_account (str): The default AWS account ID to use if none is provided by the user.

    Returns:
        str: A fully structured prompt string to be passed to Bedrock for intent detection and slot extraction.
    """
    prompt = (
        "You are an AWS security assistant. You have two tasks:\n\n"
        "TASK 1 – Intent Classification:\n"
        "Classify the user's natural language question into **one** of these predefined intents:\n"
        "{\n"
        f"{intent_block}\n"
        "}\n"
        "- Respond only with an exact match from the list.\n"
        "- If no intent fits, use: \"NO_INTENT\"\n\n"
        "TASK 2 – Slot Extraction:\n"
        "Extract the following AWS related information from the question (if available):\n"
        f"- account_id (12-digit AWS Account ID). If none provided, default to {main_account}\n"
        "- username (e.g., 'Alice', 'bob', UserManagement)\n"
        "- ip_address (IPv4 format)\n"
        "- timeframe (e.g., 'last 3 days', 'in past hour')\n"
        "- service_name (optional, e.g., 'S3', 'EC2', 'VPC', 'security group', 'lambda function')\n"
        "- resource_name (optional, any AWS resource name or identifier mentioned by the user — for example:\n"
        "  S3 bucket: 'my_bucket',\n"
        "  Lambda function: 'lambda-user-tracker',\n"
        "  VPC: 'vpc-prod-eu',\n"
        "  IAM user: 'alice',\n"
        "  EC2 instance ID: 'i-1234567890abcdef0')\n\n"

        "If the user request contains words like 'kids', 'kid',  'child', 'children', 'funny', 'joke', 'humor', 'hilarious', 'laugh', these words ake style keywords - indicating presentation style and must not be included in slots like username, account_id, ip_address, etc.\n\n"

        "Return your result as a JSON object. Do not wrap it in backticks, markdown, or code blocks.\n"
        "Do NOT include any explanation, comments, or formatting. Just return the raw JSON object, nothing else.\n"
        "Follow this structure:\n"
        "{\n"
        "  \"intent\": \"<intent_name>\",\n"
        "  \"slots\": {\n"
        "    \"account_id\": \"<value>\",\n"
        "    \"username\": \"<value>\",\n"
        "    \"ip_address\": \"<value>\",\n"
        "    \"timeframe\": \"<value>\",\n"
        "    \"service_name\": \"<value>\",\n"
        "    \"resource_name\": \"<value>\"\n"
        "  }\n"
        "}\n"
        "Leave slot values blank or null if not found. Do not invent values.\n\n"
        f"User question: \"{user_question}\""
    )
    return prompt
