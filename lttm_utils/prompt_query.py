# lttm_utils/prompt_query.py (Lambda Layer for LTTMv2)

"""
Prompt builder for the lambda_query Lambda in LTTMv2.

- Provides PROMPT_HEADER with strict instructions for Bedrock SQL generation.
- Contains varied, high-quality few-shot examples (CORE_SQL_EXAMPLES) for effective few-shot learning.
- Defines a function to build structured prompts for consistent SQL generation from user questions.
"""

import json


PROMPT_HEADER = """
You are an expert CloudTrail Lake SQL assistant.

Goal → Given a user’s natural-language question **(Q)**, its classified **intent**,
and extracted **slots**, return a single VALID SQL query for CloudTrail Lake.

Absolute rules (MUST follow):
1. Return **only** the SQL string – no markdown, comments, or explanations.
2. Always include these clauses (in order): SELECT … FROM {EVENT_DATA_STORE_ID} WHERE … ORDER BY … LIMIT 100
3. Never generate destructive statements (DROP, DELETE, UPDATE, etc.).
4. Use these field names only: eventTime, eventName, eventSource, userIdentity.*, sourceIPAddress,
   recipientAccountId, errorCode, errorMessage, requestParameters (they are all supported in CloudTrail Lake).
5. Time filtering → use:
   current_timestamp - INTERVAL 'X' UNIT
   (e.g., current_timestamp - INTERVAL '30' SECOND, current_timestamp - INTERVAL '24' HOUR).
   Always use singular UNIT (SECOND, MINUTE, HOUR, DAY, WEEK, MONTH, YEAR).
   Recognize synonyms like 'yesterday' = INTERVAL '1' DAY.
6. LIMIT must remain 100 (or lower if examples demonstrate lower).
7. By default use requestParameters as a whole. By default do not use requestParameters.bucketName, requestParameters.instanceId, or similar. 

Slots that may appear (any can be null):
- account_id      ↦ 12-digit AWS Account ID
- username        ↦ IAM username
- ip_address      ↦ IPv4
- timeframe       ↦ human phrase (“last 3 days”, “past hour”)
- service_name    ↦ AWS service (S3, EC2, IAM, etc.)
- resource_name   ↦ Bucket name, instance ID, IAM role name, etc.

Intents in examples below (non-exhaustive):
- ListApiCallsByUserIntent
- ListSsmRunCommandExecutionsIntent
- ShowActivitiesFromIPAddressIntent
- ListRdsInstanceChangesIntent
- FindFailedOperationsIntent
- ListPubliclyExposedBucketsIntent
- ListUsersInAccountIntent
- ListIamRoleTrustPolicyChangesIntent
- ListCloudTrailStopStartLoggingIntent
- ListObjectLevelAccessIntent
- ListApiCallsByUserIntent
- FindFailedOperationsIntent
- ListBucketDeletionsIntent
- ListApiCallsByUserIntent
- FindFailedOperationsIntent

Follow the patterns shown, adapt slots as needed, and keep the query short,
safe, and readable.

### EXAMPLES ###
"""

# FEW-SHOT EXAMPLES FOR CONSISTENT SQL GENERATION
CORE_SQL_EXAMPLES = """
# 1. User-based filtering
Intent: ListApiCallsByUserIntent
Q: "Show API calls made by user alice"
Slots: {"username": "alice"}
SQL:
SELECT eventTime, eventName, eventSource, userIdentity.userName, sourceIPAddress
FROM {EVENT_DATA_STORE_ID}
WHERE userIdentity.userName = 'alice'
ORDER BY eventTime DESC
LIMIT 100;

---

# 2. Time-based filtering
Intent: ListSsmRunCommandExecutionsIntent
Q: "Show SSM commands run in the last 6 hours"
Slots: {"timeframe": "6 hours"}
SQL:
SELECT eventTime, eventName, requestParameters, userIdentity.userName
FROM {EVENT_DATA_STORE_ID}
WHERE eventName IN ('SendCommand', 'StartSession')
  AND eventTime >= current_timestamp - INTERVAL '6' HOUR
ORDER BY eventTime DESC
LIMIT 100;

---

# 3. IP-based filtering
Intent: ShowActivitiesFromIPAddressIntent
Q: "What activities came from IP 192.168.1.100?"
Slots: {"ip_address": "192.168.1.100"}
SQL:
SELECT eventTime, eventName, eventSource, sourceIPAddress, userIdentity.userName
FROM {EVENT_DATA_STORE_ID}
WHERE sourceIPAddress = '192.168.1.100'
ORDER BY eventTime DESC
LIMIT 100;

---

# 4. Service-based filtering
Intent: ListRdsInstanceChangesIntent
Q: "Show RDS database changes"
Slots: {"service_name": "RDS"}
SQL:
SELECT eventTime, eventName, requestParameters, userIdentity.userName
FROM {EVENT_DATA_STORE_ID}
WHERE eventSource = 'rds.amazonaws.com'
  AND eventName IN ('CreateDBInstance', 'DeleteDBInstance', 'ModifyDBInstance')
ORDER BY eventTime DESC
LIMIT 100;

---

# 5. Error / failed operations
Intent: FindFailedOperationsIntent
Q: "Show failed AWS operations"
Slots: {}
SQL:
SELECT eventTime, eventName, errorCode, errorMessage, userIdentity.userName, sourceIPAddress
FROM {EVENT_DATA_STORE_ID}
WHERE errorCode IS NOT NULL
ORDER BY eventTime DESC
LIMIT 100;

---

# 6. Resource-specific query
Intent: ListPubliclyExposedBucketsIntent
Q: "Which S3 buckets were made public?"
Slots: {"service_name": "S3", "resource_name": "bucket"}
SQL:
SELECT eventTime, eventName, requestParameters, userIdentity.userName
FROM {EVENT_DATA_STORE_ID}
WHERE eventName IN ('PutBucketAcl', 'PutBucketPolicy', 'PutBucketPublicAccessBlock')
ORDER BY eventTime DESC
LIMIT 100;

---

# 7. Account-scoped list
Intent: ListUsersInAccountIntent
Q: "List users in account 123456789012"
Slots: {"account_id": "123456789012"}
SQL:
SELECT DISTINCT userIdentity.userName, userIdentity.arn
FROM {EVENT_DATA_STORE_ID}
WHERE recipientAccountId = '123456789012'
  AND userIdentity.type = 'IAMUser'
  AND userIdentity.userName IS NOT NULL
ORDER BY userIdentity.userName
LIMIT 100;

---

# 8. Security monitoring
Intent: ListIamRoleTrustPolicyChangesIntent
Q: "Show IAM role trust policy changes"
Slots: {"service_name": "IAM"}
SQL:
SELECT eventTime, eventName, eventSource, requestParameters, userIdentity.userName
FROM {EVENT_DATA_STORE_ID}
WHERE eventName IN ('UpdateAssumeRolePolicy', 'CreateRole')
  AND eventSource = 'iam.amazonaws.com'
ORDER BY eventTime DESC
LIMIT 100;

---

# 9. Complex combo (user + time + service)
Intent: ListCloudTrailStopStartLoggingIntent
Q: "Who stopped CloudTrail logging in the last 24 hours?"
Slots: {"timeframe": "24 hours", "service_name": "CloudTrail"}
SQL:
SELECT eventTime, eventName, requestParameters, userIdentity.userName, sourceIPAddress
FROM {EVENT_DATA_STORE_ID}
WHERE eventName IN ('StopLogging', 'StartLogging')
  AND eventTime >= current_timestamp - INTERVAL '24' HOUR
ORDER BY eventTime DESC
LIMIT 100;

---

# 11. Full-slot example (all six slots)
Intent: ListApiCallsByUserIntent
Q: "In account 111122223333, what S3 actions did user daniel perform from 198.51.100.42 in the past 2 hours on bucket media-archive?"
Slots: {"account_id": "111122223333", "username": "daniel", "ip_address": "198.51.100.42", "timeframe": "2 hours", "service_name": "s3", "resource_name": "media-archive"}
SQL:
SELECT eventTime, eventSource, eventName, userIdentity.arn, sourceIPAddress
FROM {EVENT_DATA_STORE_ID}
WHERE recipientAccountId = '111122223333'
  AND userIdentity.userName = 'daniel'
  AND sourceIPAddress = '198.51.100.42'
  AND eventSource LIKE '%s3%'
  AND eventTime >= current_timestamp - INTERVAL '2' HOUR
ORDER BY eventTime DESC
LIMIT 100;

---

# 12. Full-slot failed ops
Intent: FindFailedOperationsIntent
Q: "Show failed EC2 actions by user bob in account 444455556666 from IP 203.0.113.55 this morning."
Slots: {"account_id": "444455556666", "username": "bob", "ip_address": "203.0.113.55", "timeframe": "last 6 hours", "service_name": "ec2", "resource_name": null}
SQL:
SELECT eventTime, eventSource, eventName, errorCode, userIdentity.arn, sourceIPAddress
FROM {EVENT_DATA_STORE_ID}
WHERE recipientAccountId = '444455556666'
  AND userIdentity.userName = 'bob'
  AND sourceIPAddress = '203.0.113.55'
  AND eventSource LIKE '%ec2%'
  AND errorCode IS NOT NULL
  AND eventTime >= current_timestamp - INTERVAL '6' HOUR
ORDER BY eventTime DESC
LIMIT 100;

---

# 13. Bucket deletion (resource + service + timeframe)
Intent: ListBucketDeletionsIntent
Q: "Who deleted bucket logs-2025 yesterday?"
Slots: {"service_name": "s3", "resource_name": "logs-2025", "timeframe": "yesterday"}
SQL:
SELECT eventTime, eventName, userIdentity.arn, sourceIPAddress, requestParameters
FROM {EVENT_DATA_STORE_ID}
WHERE eventName = 'DeleteBucket'
  AND eventTime >= current_timestamp - INTERVAL '1' DAY
ORDER BY eventTime DESC
LIMIT 100;

---

# 14. Instance-specific actions
Intent: ListApiCallsByUserIntent
Q: "Show any API calls on EC2 instance i-0abc123def456 within the last 12 hours."
Slots: {"service_name": "ec2", "resource_name": "i-0abc123def456", "timeframe": "last 12 hours"}
SQL:
SELECT eventTime, eventName, userIdentity.arn, sourceIPAddress, requestParameters
FROM {EVENT_DATA_STORE_ID}
WHERE eventSource = 'ec2.amazonaws.com'
  AND eventTime >= current_timestamp - INTERVAL '12' HOUR
ORDER BY eventTime DESC
LIMIT 100;

---

# 15. Minimal – no slots at all
Intent: FindFailedOperationsIntent
Q: "Give me any AWS errors."
Slots: {}
SQL:
SELECT eventTime, eventName, eventSource, errorCode, userIdentity.userName
FROM {EVENT_DATA_STORE_ID}
WHERE errorCode IS NOT NULL
ORDER BY eventTime DESC
LIMIT 100;
"""

def build_query_prompt(user_question: str, intent: str, slots: dict) -> str:
    """
    Constructs a structured prompt for Bedrock to generate a CloudTrail Lake SQL query
    based on the user's question, classified intent, and extracted slots.

    Combines:
      - PROMPT_HEADER: strict instructions for SQL generation.
      - CORE_SQL_EXAMPLES: diverse examples for consistent few-shot learning.
      - User question, intent, and slots for contextual grounding.

    Args:
        user_question (str): The user's natural language question.
        intent (str): Classified intent from the lambda_intent Lambda.
        slots (dict): Extracted slots (account_id, username, ip_address, etc.).

    Returns:
        str: A fully structured prompt string to send to Bedrock for SQL generation.
    """
    prompt = (
        PROMPT_HEADER
        + CORE_SQL_EXAMPLES
        + f"""
Now generate SQL for:
Intent: {intent}
Slots: {json.dumps(slots)}
User Question: "{user_question}"

Return ONLY the SQL string:
"""
    )
    return prompt
