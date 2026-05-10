<!-- Copyright (c) 2026 Michal Salanci -->
<!-- SPDX-License-Identifier: MIT -->

# Logs Talk To Me

**Logs Talk To Me** (**LTTM**) is a conversational interface for AWS infrastructure investigations.

Instead of writing Athena SQL, clicking through multiple AWS consoles, or stitching together `aws cli` commands across accounts, you ask a question in plain English and the system gathers evidence from logs, APIs, and security services.

```bash
./alexandra.sh "show me the last 20 failed console logins across all accounts this week"
./alexandra.sh "what changed on security group sg-0a1b2c3d yesterday and who changed it?"
./alexandra.sh "how much did Bedrock cost in February and who ran the expensive queries?"
./alexandra.sh "any GuardDuty findings in prod that involve publicly accessible resources?"
```

This is the **v3** release: a full rewrite using **Amazon Bedrock AgentCore**, **Strands Agents**, **Amazon Athena**, **AWS Glue Data Catalog**, **Amazon S3**, **API Gateway**, **Cognito**, and a multi-account AWS log data lake.

> Earlier versions (`v1` using Amazon Lex and a single Lambda, `v2` using API Gateway with three Lambdas) live on older branches of the original repository and are documented there. This version uses a supervisor/sub-agent architecture running in Bedrock AgentCore Runtime.

## Contents

- [What LTTM can answer](#what-lttm-can-answer)
- [Account model](#account-model)
- [Architecture overview](#architecture-overview)
- [Why multiple agents?](#why-multiple-agents)
- [Safety and anti-hallucination layers](#safety-and-anti-hallucination-layers)
- [Memory](#memory)
- [Streaming CLI](#streaming-cli)
- [CLI flags](#cli-flags)
- [Repository layout](#repository-layout)
- [Important files](#important-files)
- [Prerequisites](#prerequisites)
- [Configuration checklist](#configuration-checklist)
- [Deployment order](#deployment-order)
- [Glue tables](#glue-tables)
- [Development notes](#development-notes)
- [Known limitations](#known-limitations)
- [Usage examples](#usage-examples)
- [License](#license)

## What LTTM can answer

LTTM has one **supervisor agent** (Claude Sonnet 4) and twelve specialized sub-agents. The supervisor decides which sub-agent, or combination of sub-agents, is needed for each question.

| Domain          | Data source              | Good for                                                     |
| --------------- | ------------------------ | ------------------------------------------------------------ |
| CloudTrail      | Athena over S3 data lake | Who did what, API calls, IAM activity, failed console logins |
| CloudWatch      | Athena over S3 data lake | Lambda/application logs, log group errors, runtime messages  |
| Config          | Athena over S3 data lake | Resource changes, current inventory, configuration history   |
| CUR             | Athena over S3 data lake | Cost, spend trends, expensive services/resources             |
| VPC Flow Logs   | Athena over S3 data lake | Network traffic, rejected packets, source/destination IPs    |
| GuardDuty       | API + Athena archive     | Current and historical threat findings                       |
| Macie           | API                      | Sensitive data findings in S3 (PII, credentials)             |
| Inspector       | API                      | Vulnerabilities, CVEs, EC2/Lambda/ECR findings               |
| Access Analyzer | API                      | Public or cross-account access findings                      |
| Health          | API                      | AWS Health events, outages, scheduled maintenance            |
| Organizations   | API                      | AWS accounts, OUs, SCPs, org structure                       |
| Service Quotas  | API                      | Service limits and quota values                              |

The supervisor can call multiple sub-agents for one question. For example:

```text
Who created the most expensive resources last month?
```

uses:

```text
CUR        → find expensive services/resources
CloudTrail → find who created or modified them
```

## Account model

LTTM runs across **three AWS accounts in one AWS Organization** (main, dev, prod).

- The **main** account holds the data lake, the API Gateway, the Lambdas, Cognito, DynamoDB, and the AgentCore runtime IAM role.
- **Dev** and **prod** are member accounts. Their CloudTrail events and Firehose-delivered logs land in the main-account data lake; the agents read account-local services (Health, GuardDuty, Macie, Inspector, Access Analyzer, Quotas) by assuming a per-service `LTTM<Service>ReadRole` that Terraform creates in each member account.
- **Organizations** is queried directly from the management account — no role assumption.

Control Tower was used for the original setup but is not a hard requirement; an AWS Organization with three accounts and the CLI profiles to drive them is enough.

## Architecture overview

```text
User / alexandra.sh
        ↓ Cognito JWT
API Gateway REST API
        ↓
Node.js streaming Lambda
        ↓ SigV4 InvokeAgentRuntime
Amazon Bedrock AgentCore Runtime
        ↓
Supervisor Agent
        ↓
Sub-agents
        ↓
Athena / AWS APIs / AgentCore Memory
```

There are two main data paths.

### 1. Historical log data path

Historical data is stored in an S3 data lake and queried with Athena.

```text
AWS services / accounts
        ↓
CloudTrail / Firehose / EventBridge / direct S3 delivery
        ↓
S3 data lake (eu-central-1)
        ↓
AWS Glue Data Catalog tables
        ↓
Amazon Athena
        ↓
Athena-based sub-agents
```

Athena-based sub-agents:

```text
CloudTrail, CloudWatch, Config, CUR, Flow Logs, GuardDuty archive
```

### 2. Current-state API path

For services where current state matters more than long-term history, agents call AWS APIs directly. Cross-account reads use STS `AssumeRole` into a dedicated `LTTM<Service>ReadRole` in dev and prod.

```text
Sub-agent
        ↓ boto3 API wrapper
AWS API (+ optional STS AssumeRole for non-main accounts)
        ↓
Formatted raw result
        ↓
Supervisor summary
```

API-based sub-agents:

```text
Access Analyzer, Health, Organizations, Quotas, Macie, Inspector, GuardDuty current findings
```

## Why multiple agents?

This project uses a **sub-agent-as-tool** pattern.

The supervisor agent does not contain every schema, every API reference, and every service rule in one giant prompt. Instead, each sub-agent owns one domain.

```text
Supervisor
├── query_cloudtrail
├── query_cloudwatch
├── query_config
├── query_cur
├── query_flowlogs
├── query_guardduty
├── query_macie
├── query_inspector
├── query_access_analyzer
├── query_health
├── query_organizations
└── query_quotas
```

Benefits:

- smaller prompts per agent
- easier debugging
- easier service expansion
- better SQL/tool quality
- different models can be used per agent if needed
- supervisor can combine multiple sources into one answer

## Safety and anti-hallucination layers

LTTM does not rely on a system prompt alone. The runtime uses multiple guardrails and checks.

| Layer                       | Purpose                                                               |
| --------------------------- | --------------------------------------------------------------------- |
| Cognito + API Gateway       | Authenticated public entry point                                      |
| IAM roles                   | Least-privilege backend access                                        |
| Lake Formation              | Table-level access to Athena data                                     |
| Bedrock managed guardrail   | Prompt-attack filter + "off-topic" topic denial                       |
| `ArchitectureGuardHook`     | Blocks architecture probing and internal-name leakage                 |
| `SQLValidatorHook`          | Blocks unsafe/malformed Athena SQL before execution                   |
| `SQLRewriteHook`            | Enforces row limits and prevents retry loops                          |
| `OutputIntegrityHook`       | Detects "no results" contradictions and follow-up detours             |
| `SupervisorSteeringHandler` | Claude Haiku as judge for routing and final-answer integrity          |
| Raw result extraction       | Sub-agents return raw tool results; supervisor is the only summarizer |

The key idea:

> Agents may generate text, but deterministic checks decide what can execute.

## Memory

LTTM uses **AgentCore Memory** so follow-up questions can use previous context.

Configured strategies:

| Strategy      | Meaning                                             |
| ------------- | --------------------------------------------------- |
| Semantic      | Reusable facts across sessions                      |
| Summarization | Compressed session history                          |
| Episodic      | Lessons/reflections from previous query experiences |

A custom `LTTMMemoryHook` writes messages to AgentCore Memory and retrieves relevant memory before the supervisor answers.

Memory is intentionally treated as **context**, not authority. It should help with follow-up questions, but it should not silently modify SQL filters or routing decisions.

Use `--clean` to skip memory retrieval for one request:

```bash
./alexandra.sh --clean "show me CloudTrail events today"
```

## Streaming CLI

The CLI client is `alexandra.sh`, which ships in this repository.

It:

- authenticates against Cognito
- caches and refreshes the ID token
- manages conversation/session IDs
- sends questions to API Gateway
- reads Server-Sent Events from the streaming API
- prints live status while the agent works

Example output:

```text
💬 Alexandra (stream) [session: 7d44200c] asking AgentCore: show me last 3 CloudTrail events today
⏳ Analyzing question...
⏳ CloudTrail agent processing...
⏳ Querying Athena...
⏳ CloudTrail agent returning results to supervisor.
⏳ Summarizing results...

<final answer>
```

Optional waiting-screen arcade game (`lttm_game.py`) while the agent works:

```bash
./alexandra.sh --notboring "show me CloudWatch errors from the last hour"
```

## CLI flags

| Flag             | What it does                                         |
| ---------------- | ---------------------------------------------------- |
| `--new`          | Starts a new session/investigation                   |
| `--session <id>` | Continues a specific session                         |
| `--clean`        | Skips AgentCore Memory retrieval for this request    |
| `--history`      | Lists stored conversation metadata from DynamoDB     |
| `--delete <id>`  | Deletes conversation metadata for a session          |
| `--health`       | Checks AgentCore runtime health                      |
| `--services`     | Lists supported service domains                      |
| `--notboring`    | Runs the arcade waiting screen while the agent works |

Use `--new` for a new investigation. Do not use it for every follow-up question — otherwise session-level summary and episodic memory have very little context to work with.

## Repository layout

```text
.
├── agents/                     # Supervisor + 12 sub-agents deployed to AgentCore
│   ├── supervisor_agent.py
│   ├── *_agent.py              # Twelve sub-agent wrappers
│   ├── hooks/                  # Deterministic safety hooks
│   ├── plugins/                # Strands plugins (logging + LLM judge)
│   ├── tools/                  # Athena executor + API boto3 wrappers
│   ├── utils/                  # Shared helpers (agent_vars, sse_emitter, …)
│   └── requirements.txt
│
├── terraform-bootstrap/        # One-time remote-state S3 bucket
│
├── terraform/                  # Main infrastructure
│   ├── agents.tf               # Agent IAM role + AgentCore Memory strategies
│   ├── apigw.tf                # REST API and Cognito authorizer
│   ├── athena.tf               # Athena workgroup + seven Glue tables
│   ├── cognito.tf              # User pool, app client, initial user
│   ├── lambda.tf               # Five Lambdas behind the API
│   ├── lakeformation.tf        # Lake Formation grants for the agent role
│   ├── s3.tf                   # Data lake bucket and policy
│   ├── config_pipeline.tf      # Config → EventBridge → Firehose → S3
│   ├── config_snapshot.tf      # S3-triggered snapshot transformer
│   ├── config_multiregion.tf   # Multi-region Config recorders
│   ├── cloudwatch.tf           # CloudWatch → Firehose pipelines
│   ├── cloudtrail.tf           # Organization trail
│   ├── cur.tf                  # CUR 2.0 export
│   ├── flowlogs.tf             # VPC Flow Logs (Parquet, direct to S3)
│   ├── dns.tf                  # Route 53 DNS query logging
│   ├── guardduty.tf            # Org-wide GuardDuty + EventBridge archive
│   ├── inspector.tf            # Inspector v2 enablement
│   ├── macie.tf                # Macie enablement + auto discovery
│   ├── guardrails.tf           # Bedrock managed guardrail
│   ├── dynamodb.tf             # Conversations metadata table
│   └── lambda/                 # Lambda source directories (zips built by Terraform)
│
├── alexandra.sh                # CLI client
├── lttm_game.py                # Optional waiting-screen arcade game
├── README.md                   # This file
└── LICENSE                     # MIT
```

## Important files

For the full per-file description (what each module does and what it connects to), see [`docs/FILE_REFERENCE.md`](./docs/FILE_REFERENCE.md).

Quick map of the most important files:

| File                         | Purpose                                                      |
| ---------------------------- | ------------------------------------------------------------ |
| `agents/supervisor_agent.py` | AgentCore entrypoint; routes questions and streams events    |
| `agents/*_agent.py`          | Twelve sub-agent wrappers exposed to the supervisor as tools |
| `agents/tools/*_tool.py`     | Shared Athena executor + six API boto3 wrappers              |
| `agents/hooks/*.py`          | Deterministic guardrails and AgentCore Memory hook           |
| `agents/plugins/*.py`        | Logging plugin and Claude Haiku LLM-judge steering plugin    |
| `agents/utils/agent_vars.py` | Account IDs, model IDs, Glue partition rules, internal names |
| `terraform/athena.tf`        | Athena workgroup + Glue database and tables                  |
| `terraform/agents.tf`        | Agent IAM role + AgentCore Memory + memory strategies        |
| `terraform/apigw.tf`         | REST API (five Cognito-authorized routes)                    |
| `terraform/lambda.tf`        | Five Lambdas behind the API Gateway                          |
| `terraform/cognito.tf`       | User pool, app client, initial admin user                    |
| `terraform/dynamodb.tf`      | Conversations metadata table with TTL                        |
| `alexandra.sh`               | Bash CLI (Cognito auth + SSE stream)                         |

## Prerequisites

You need:

- three AWS accounts in one AWS Organization (the original setup used main, dev, prod accounts managed through Control Tower; Control Tower itself is not a hard code dependency)
- AWS CLI profiles for all three accounts
- Terraform `>= 1.14`
- AWS provider `~> 6.43`
- Python 3.12 for local scripts and AgentCore runtime packaging
- AWS CLI v2
- Bedrock AgentCore CLI/tooling installed locally
- access to the Bedrock models configured in `agents/utils/agent_vars.py` (Claude Sonnet 4 and Claude Haiku 4.5 by default, via inference profile in us-west-2)

> **Note on the AWS Health agent:** the AWS Health API only returns account-specific events for accounts on Business or Enterprise Support. Without it, `query_health` still works — it just returns no events. The rest of LTTM is unaffected.

The project was originally built across three regions:

```text
eu-central-1  → main infrastructure, Lambda, API Gateway, data lake
us-west-2     → Bedrock AgentCore Runtime and Bedrock models
us-east-1     → global-service resources (Route 53 query logging, CUR export)
```

You can change regions in `terraform/terraform.tfvars` and `agents/utils/agent_vars.py`, but make sure every hardcoded service dependency is updated consistently.

## Configuration checklist

Before deploying, edit these files.

### 1. `terraform-bootstrap/terraform.tfvars`

Set the state bucket name, region, and CLI profile used to create it.

```hcl
backend_bucket         = "your-unique-tf-state-bucket"
backend_region         = "eu-central-1"
backend_region_profile = "main"
```

Then **open `terraform/backend.tf`** and hardcode the same bucket name there. Terraform's S3 backend block cannot use variables, so this is a manual edit:

```hcl
terraform {
  backend "s3" {
    bucket       = "your-unique-tf-state-bucket"   # ← edit this line
    key          = "lttm/terraform.tfstate"
    region       = "eu-central-1"
    use_lockfile = true
  }
}
```

### 2. `terraform/terraform.tfvars`

```hcl
project_name = "lttm-cia"

main_account_id = "123456789012"
dev_account_id  = "234567890123"
prod_account_id = "345678901234"

dev_account_email  = "dev@example.com"
prod_account_email = "prod@example.com"

project_region   = "eu-central-1"
global_region    = "us-east-1"
agentcore_region = "us-west-2"

main_profile = "main"
dev_profile  = "dev"
prod_profile = "prod"

prefix = "your-unique-datalake-bucket"

hosted_zone_ids = {
  # "Z0123456789ABCDEF" = "example.com"  # optional DNS query logging
}

cognito_initial_user          = "admin"
cognito_initial_email         = "admin@example.com"
cognito_initial_temp_password = "ChangeMeOnFirstLogin!42"
```

Leave `cli_runtime_arn` / `cli_stream_runtime_arn` at their placeholder defaults for the **first** `terraform apply`. Fill in the real AgentCore runtime ARN after the agent is deployed, then re-apply.

### 3. `agents/utils/agent_vars.py`

Set the same account IDs, labels, emails, default region, and data lake bucket.

```python
ACC1_ID = "123456789012"
ACC1_LABEL = "main"
ACC1_EMAIL = "main@example.com"

# … ACC2_*, ACC3_* …

DATALAKE_BUCKET = "your-unique-datalake-bucket"
DEFAULT_REGION = "eu-central-1"
```

## Deployment order

The order matters because the Lambda permissions need the AgentCore runtime ARN, and AgentCore needs the IAM role and memory created by Terraform. The guardrail also needs two `agentcore launch` calls — one to create the runtime, and one to reinject the guardrail env vars after Terraform has created the guardrail.

### 1. Bootstrap Terraform state

```bash
cd terraform-bootstrap
terraform init
terraform apply
```

Then **open `terraform/backend.tf`** and update the `bucket` value to match the bucket you just created. Terraform's S3 backend block cannot interpolate variables — this is a manual edit every time.

### 2. First Terraform apply — infrastructure with placeholder runtime ARNs

```bash
cd ../terraform
terraform init
terraform apply
```

This creates:

- S3 data lake
- Glue / Athena / Lake Formation
- data ingestion pipelines (CloudTrail / CloudWatch / Config / CUR / Flow Logs / GuardDuty / DNS)
- Cognito
- API Gateway
- Lambda functions (with placeholder AgentCore runtime ARN)
- DynamoDB conversations table
- Bedrock guardrail
- AgentCore Memory + strategies
- AgentCore execution IAM role

### 3. First `agentcore launch` — create the runtime

From inside `agents/`:

```bash
source .venv/bin/activate        # must be inside the venv
cd agents
agentcore launch --auto-update-on-conflict
```

This produces the real AgentCore runtime ARN (and stream runtime ARN if you use both). Keep the output; you'll paste the ARNs into Terraform next.

### 4. Second Terraform apply — wire the real runtime ARN

In `terraform/terraform.tfvars`, uncomment and update:

```hcl
cli_runtime_arn        = "arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/lttm_supervisor-XXXXXXXXXX"
cli_stream_runtime_arn = "arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/lttm_supervisor_stream-XXXXXXXXXX"
```

Then:

```bash
cd ../terraform
terraform apply
```

This tightens Lambda permissions from the placeholder runtime ARN to the real AgentCore runtime ARN.

### 5. Second `agentcore launch` — bake the guardrail env vars into the runtime

The supervisor reads `LTTM_GUARDRAIL_ID` and `LTTM_GUARDRAIL_VERSION` from env vars at startup. Those env vars are set at `agentcore launch` time, so the agent must be redeployed once the guardrail is known:

```bash
export LTTM_GUARDRAIL_ID=$(terraform -chdir=terraform output -raw guardrail_id)
export LTTM_GUARDRAIL_VERSION=$(terraform -chdir=terraform output -raw guardrail_version)

cd ../agents
agentcore launch --auto-update-on-conflict \
  --env LTTM_GUARDRAIL_ID=$LTTM_GUARDRAIL_ID \
  --env LTTM_GUARDRAIL_VERSION=$LTTM_GUARDRAIL_VERSION
```

Without this step the guardrail exists in AWS but the supervisor ignores it.

### 6. Export CLI variables and smoke-test

```bash
export LTTM_STREAM_API_URL=$(terraform -chdir=terraform output -raw lttm_stream_api_url)
export COGNITO_USER_POOL_ID=$(terraform -chdir=terraform output -raw cognito_user_pool_id)
export COGNITO_CLIENT_ID=$(terraform -chdir=terraform output -raw cognito_app_client_id)
```

Then:

```bash
./alexandra.sh --health
./alexandra.sh --services
./alexandra.sh --new "show me last 3 CloudTrail events today in main"
```

## Glue tables

Athena queries these Glue tables in database `lttm_logs`.

| Table                | Partition keys                                         |
| -------------------- | ------------------------------------------------------ |
| `cloudtrail_logs`    | `account_id`, `region`, `year`, `month`, `day`         |
| `cloudwatch_logs`    | `log_group`, `account_id`, `year`, `month`             |
| `config_logs`        | `account_id`, `year`, `month`, `day`                   |
| `config_snapshot`    | `account_id`, `year`, `month`, `day`                   |
| `cur_data`           | `billing_period`                                       |
| `flowlogs`           | `aws_account_id`, `aws_region`, `year`, `month`, `day` |
| `guardduty_findings` | `account_id`, `year`, `month`, `day`                   |

If you change table partitioning in `terraform/athena.tf`, also update `agents/utils/agent_vars.py` — `SQLValidatorHook` uses that mapping to validate SQL before Athena runs.

## Development notes

### Lambda zips

Terraform builds Lambda zip files from source directories using `archive_file`. The `.zip` artifacts are excluded from git (`terraform/lambda/*.zip` in `.gitignore`); the source directories are the source of truth.

### Files intentionally excluded from the repo

`.gitignore` excludes developer-local concerns:

```text
.terraform/
*.tfstate*
terraform/lambda/*.zip
__pycache__/
.pytest_cache/
agents/hooks/retired/
tests/
scripts/
.bedrock_agentcore.yaml       # AgentCore CLI writes this per deployment
.bedrock_agentcore/           # AgentCore CLI state directory
```

That means a fresh clone gives you a runnable repo without any leftover local state, credentials, or deployment-specific config. The AgentCore CLI deployment flow will generate the deployment-specific AgentCore config for your account.

### Basic checks

```bash
python3 -m compileall agents
node --check terraform/lambda/invoke_agent_stream/index.mjs
node --check terraform/lambda/list_conversations/index.mjs
node --check terraform/lambda/delete_conversation/index.mjs
node --check terraform/lambda/health_check/index.mjs
node --check terraform/lambda/list_services/index.mjs
```

## Known limitations

- The project assumes a three-account model (main / dev / prod). Adapting to one or two accounts is mostly edits in `terraform/` provider aliases and `agents/utils/agent_vars.py`.
- Some services are regional and must be enabled in every region you care about.
- The AWS Health API requires Business or Enterprise Support to return account-specific events. Without it, `query_health` simply returns no events; the rest of LTTM is unaffected.
- CloudWatch log queries depend on log-group partition discovery when the user doesn't provide an exact log group.
- Memory improves follow-up context but should not be treated as authorization or truth.
- AgentCore Runtime runs in `PUBLIC` network mode in the provided config; private networking would require VPC-mode runtime and VPC endpoints.

## Usage examples

Once deployed, drive everything through `alexandra.sh`. A few example questions covering each domain and showing how the supervisor combines sub-agents:

### Single-domain questions

```bash
# CloudTrail — who did what
./alexandra.sh --new "show me the last 20 failed console logins across all accounts this week"

# CUR — cost and spend
./alexandra.sh --new "what were the top 5 most expensive services in the main account last month?"

# Config — what changed
./alexandra.sh --new "what changed on security group sg-0a1b2c3d in the last 7 days?"

# GuardDuty — threats
./alexandra.sh --new "any high severity GuardDuty findings in prod in the last 30 days?"

# Inspector — vulnerabilities
./alexandra.sh --new "what critical CVEs affect my Lambda functions in main?"

# Macie — sensitive data
./alexandra.sh --new "are there any credentials or PII exposed in S3 buckets in main?"

# Organizations — org structure
./alexandra.sh --new "list all accounts in my organization and which OU they belong to"
```

### Multi-domain questions (the supervisor calls two or more sub-agents)

```bash
# CUR + CloudTrail
./alexandra.sh --new "who created the most expensive EC2 instance last month?"

# Config + CloudTrail
./alexandra.sh --new "who changed security group sg-0a1b2c3d yesterday and exactly what changed?"

# GuardDuty + Access Analyzer
./alexandra.sh --new "are any resources flagged by GuardDuty also publicly accessible?"

# Inspector + CloudTrail
./alexandra.sh --new "who deployed the Lambda that has the Log4Shell vulnerability?"
```

### Follow-ups in the same session

Drop `--new` and LTTM continues the conversation, pulling context from AgentCore Memory:

```bash
./alexandra.sh --new "show me the last 10 IAM API calls in dev"
./alexandra.sh "any of those denied?"
./alexandra.sh "who made the denied ones and from which IP?"
```

### Memory and session controls

```bash
# Skip memory retrieval for this one question
./alexandra.sh --clean "show me CloudTrail events in the last hour"

# See past sessions
./alexandra.sh --history

# Health and service catalog
./alexandra.sh --health
./alexandra.sh --services

# Arcade game while you wait for a slow query
./alexandra.sh --notboring "count all API calls across all three accounts this month"
```

## License

MIT. See [`LICENSE`](./LICENSE).
