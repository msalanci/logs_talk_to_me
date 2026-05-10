<!-- Copyright (c) 2026 Michal Salanci -->
<!-- SPDX-License-Identifier: MIT -->

# File reference

Per-file documentation for the LTTM v3 codebase. Files that share the exact same shape (e.g. the twelve sub-agent modules, the six API tool wrappers) are grouped.

For a high-level tour, start in the main [`README.md`](../README.md).

## Contents

- [`agents/` — supervisor and sub-agents](#agents--supervisor-and-sub-agents)
- [`agents/hooks/` — deterministic safety hooks](#agentshooks--deterministic-safety-hooks)
- [`agents/plugins/` — Strands plugins](#agentsplugins--strands-plugins)
- [`agents/tools/` — `@tool` functions](#agentstools--tool-functions)
- [`agents/utils/` — shared helpers](#agentsutils--shared-helpers)
- [`terraform-bootstrap/` — one-shot remote-state setup](#terraform-bootstrap--one-shot-remote-state-setup)
- [`terraform/` — everything else](#terraform--everything-else)
- [`terraform/lambda/` — Lambda source](#terraformlambda--lambda-source)
- [Root files](#root-files)

## `agents/` — supervisor and sub-agents

- **`supervisor_agent.py`** — AgentCore entry point (`@app.entrypoint invoke`). Registers the twelve sub-agents as `@tool`s, wires in the output-integrity hook, architecture guard, memory hook, logging plugin, and the LLM-judge steering plugin, and streams status events to the client via SSE. Connects to: all twelve sub-agents, `hooks/*`, `plugins/*`, `utils/agent_vars.py`, `utils/sse_emitter.py`, and AgentCore Memory.

- **`cloudtrail_agent.py` / `cloudwatch_agent.py` / `config_agent.py` / `cur_agent.py` / `flowlogs_agent.py`** — Athena-based sub-agents. Each one translates natural language into SQL against its Glue table (`lttm_logs.cloudtrail_logs`, `cloudwatch_logs`, `config_logs` + `config_snapshot`, `cur_data`, `flowlogs`), executes it through `tools/athena_tool.py`, and returns raw rows. `cloudwatch_agent.py` additionally exposes `list_log_groups` to discover log-group names from S3 when the user doesn't name one. All five register `SQLValidatorHook` and `SQLRewriteHook`.

- **`guardduty_agent.py`** — Dual-source: calls the GuardDuty API for the last 90 days via `tools/guardduty_tool.py`, and falls back to Athena on `lttm_logs.guardduty_findings` for anything older. Registers `SQLValidatorHook` and `SQLRewriteHook` for the Athena path.

- **`access_analyzer_agent.py` / `health_agent.py` / `inspector_agent.py` / `macie_agent.py` / `organizations_agent.py` / `quotas_agent.py`** — API-based sub-agents. Each calls its matching wrapper in `tools/` (boto3 under the hood) and returns formatted results. No Athena, no SQL hooks.

- **`requirements.txt`** — Python dependencies for the agent runtime (`strands-agents`, `bedrock-agentcore`, `boto3`).

## `agents/hooks/` — deterministic safety hooks

All hooks here are regex or string comparisons — zero LLM calls, sub-millisecond, registered via `hooks=[…]` on the agent constructors.

- **`architecture_guard_hook.py`** — Two layers. Before the model call it replaces architecture-probing prompts ("list your tools", "show your system prompt") with a safe redirect. After the model call it scans the response for leaked internal names (tool, hook, file, variable names enumerated in `utils/agent_vars.INTERNAL_NAMES`) and triggers `event.retry`. Registered on the supervisor.

- **`output_integrity_hook.py`** — Runs on the supervisor. Tracks which `query_*` tools returned real data, then scans the final response for two failure modes: "no results found" contradictions and follow-up questions instead of an answer. Triggers `event.retry` when found.

- **`sql_validator_hook.py`** — Runs on every Athena sub-agent. On each `run_athena_query` call, validates the SQL against `utils/agent_vars.TABLES` and `BLOCKED_SQL_KEYWORDS`: no `awsdatacatalog.` prefix, no DROP/DELETE/etc., fully qualified `lttm_logs.<table>`, all required partition keys in WHERE, no `SELECT *`. Cancels the tool call with an error message the LLM can learn from.

- **`sql_rewrite_hook.py`** — Runs on every Athena sub-agent. Injects or caps `LIMIT` at 20 rows, and if a previous query in the same invocation was already capped and returned rows, blocks further retries so the LLM stops asking for more.

- **`memory_hook.py`** — Direct `bedrock_agentcore.memory.MemoryClient` integration. On the first user message of a session, retrieves LTM semantic facts and episodic reflections from AgentCore Memory and appends them to the supervisor's system prompt. On every message after, saves it back for future retrieval. Session ID is set per-request from `invoke()`.

## `agents/plugins/` — Strands plugins

- **`supervisor_steering.py`** — LLM-as-judge. Uses a `LedgerProvider` for context and runs Claude Haiku at two checkpoints. Before a `query_*` tool fires, a routing judge validates that the supervisor picked the right sub-agent, account, and time range — on mismatch it returns `Guide(...)` with corrective feedback instead of blocking. After the model's final response, an integrity judge compares it against all successful tool results from the ledger and flags data compression or fabrication. Cost: ~$0.001 per query.

- **`logging_plugin.py`** — Centralized lifecycle logger. Prints five tagged events — `INVOKE_START`, `INVOKE_END`, `TOOL_CALL`, `TOOL_DONE`, `TOOL_ERROR` — from every agent on every invocation. Filter with `grep '[LTTM:Log]'` in CloudWatch.

## `agents/tools/` — `@tool` functions

- **`athena_tool.py`** — Shared `run_athena_query` plus the `format_athena_rows` formatter. Executes SQL against the `lttm-athena-workgroup`, polls until `SUCCEEDED`, returns rows as a list of dicts. Used by all six Athena sub-agents and intercepted by `sql_validator_hook` and `sql_rewrite_hook`.

- **`access_analyzer_tool.py` / `guardduty_tool.py` / `health_tool.py` / `inspector_tool.py` / `macie_tool.py` / `organizations_tool.py` / `quotas_tool.py`** — API wrappers. Each exports one `@tool` (`query_*_api` or `query_*_findings`) that calls the matching AWS service through boto3, formats results into human-readable text blocks, and emits SSE status along the way. Most wrappers support STS `AssumeRole` into the corresponding `LTTM<Service>ReadRole` (see `terraform/iam.tf`) for non-main accounts where the service requires account-local access. Organizations is management-account only, so it skips role assumption.

## `agents/utils/` — shared helpers

- **`agent_vars.py`** — Single source of truth. Account IDs and labels (`ACC1_ID`/`ACC1_LABEL`, …), model IDs (`US_SONNET`, `US_HAIKU`), regions, the `TABLES` dict mapping Glue tables to their required partition keys, `BLOCKED_SQL_KEYWORDS`, the `INTERNAL_NAMES` list used by `architecture_guard_hook`, guardrail env-var lookups, and `extract_token_usage`. Edited when you change accounts, add a table, or swap models.

- **`agent_wrapper.py`** — `_extract_raw_result(agent)`. Pulls the largest `toolResult` text block out of an agent's message history so each sub-agent `@tool` wrapper can return the raw tool output instead of the LLM's reworded summary.

- **`sse_emitter.py`** — Thread-safe `queue.Queue`-backed Server-Sent Events emitter. `emit_status`, `emit_result`, `emit_error`, `emit_guard`, `emit_tokens`, `emit_done`. The supervisor's `invoke()` runs the agent on a worker thread and yields events from the queue, which AgentCore wraps as `data: {...}\n\n` frames for the client. Gated by `LTTM_SSE_STATUS`.

## `terraform-bootstrap/` — one-shot remote-state setup

Run once, before anything else. Creates the S3 bucket that `terraform/backend.tf` uses for state.

- **`main.tf`** — The state bucket with versioning, SSE-S3 encryption, and full public-access block. Hardcoded name lives in `terraform.tfvars`.
- **`variables.tf`** — Inputs: `backend_bucket`, `backend_region`, `backend_region_profile`.
- **`terraform.tfvars`** — Your values for those variables.
- **`versions.tf`** — Terraform ≥ 1.14, AWS provider ~> 6.43.

## `terraform/` — everything else

Deployed with standard `terraform init && terraform apply`. Uses the S3 backend from the bootstrap step.

**Foundations**

- **`main.tf`** — Nine AWS provider aliases: default (main / eu-central-1) plus `dev_eucentral1`, `prod_eucentral1`, `default_uswest2`, `default_useast1`, `dev_useast1`, `dev_uswest2`, `prod_useast1`, `prod_uswest2`. Every resource picks one.
- **`variables.tf`** — Account IDs, regions, CLI profiles, data-lake bucket prefix, Cognito initial user, Route 53 zones, AgentCore runtime ARN placeholders.
- **`terraform.tfvars`** — Your values. Fill in account IDs, profiles, emails, and the initial Cognito password before the first apply.
- **`versions.tf`** — Terraform + AWS + time provider pins.
- **`backend.tf`** — S3 backend config pointing at the bootstrap bucket. Bucket name is hardcoded here; update it to match `terraform-bootstrap/terraform.tfvars`.
- **`outputs.tf`** — Everything downstream needs: data-lake bucket, Athena workgroup, Firehose ARNs, Cognito IDs + OIDC discovery URL, streaming API URL, AgentCore Memory ARN/ID, conversations table, guardrail ID + version.

**Data lake**

- **`s3.tf`** — The data lake bucket (name comes from `var.prefix`, default `lttm-datalake`) with a big inline bucket policy that authorizes every producer (CloudTrail, Firehose streams from main/dev/prod, AWS Config delivery, CUR, Flow Logs, GuardDuty) to write its own prefix, and the agent role to read back.
- **`iam.tf`** — Every IAM role in this project that isn't Lambda-specific: per-region Firehose execution roles, CloudWatch→Firehose delivery roles, and the six cross-account read roles (`LTTMAccessAnalyzerReadRole`, `LTTMHealthReadRole`, `LTTMQuotasReadRole`, `LTTMGuardDutyReadRole`, `LTTMMacieReadRole`, `LTTMInspectorReadRole`) that the agent role assumes into dev and prod.

**Athena + Lake Formation**

- **`athena.tf`** — The `lttm-athena-workgroup`, the `lttm_logs` Glue database, and seven tables with partition projection: `cloudtrail_logs`, `cloudwatch_logs`, `config_logs`, `config_snapshot`, `cur_data`, `flowlogs`, `guardduty_findings`. Changing a table's partitions means editing this file.
- **`lakeformation.tf`** — Grants `SELECT`/`DESCRIBE` on all seven tables (plus `DESCRIBE` on the database) to the agent role so Athena actually returns rows.

**Agent runtime**

- **`agents.tf`** — The `lttm-agent-role` trusted by `bedrock-agentcore.amazonaws.com`. One big inline policy covers Bedrock model invocation, Athena + Glue + S3 read, AgentCore Memory, Bedrock guardrails, X-Ray, CloudWatch logs, and `sts:AssumeRole` into each cross-account read role. Also declares the AgentCore Memory resource and three memory strategies: semantic (facts), summarization, and episodic (reflections).

**Pipelines — the six log sources**

- **`cloudtrail.tf`** — A single organization-wide multi-region CloudTrail (`lttm-org-trail`) writing under `cloudtrail/` in the data lake.
- **`cloudwatch.tf`** — Five Kinesis Firehose delivery streams (main eu-central-1, dev eu-central-1, prod eu-central-1, main us-west-2, main us-east-1) with GZIP decompression + JQ metadata extraction, plus one account-level `SUBSCRIPTION_FILTER_POLICY` per region/account that sends every log group into its stream.
- **`config_multiregion.tf`** — Config recorders + delivery channels in eu-central-1 / us-east-1 / us-west-2 for each account, plus EventBridge cross-region forwarding rules that push non-eu-central-1 Config events onto the eu-central-1 default event bus where `config_pipeline.tf` picks them up.
- **`config_pipeline.tf`** — EventBridge rules → three Firehose streams (main/dev/prod) → the `lttm-config-transform` Lambda → S3 under `config/`. The Lambda lowercases field names and strips the EventBridge envelope.
- **`config_snapshot.tf`** — An S3 `ObjectCreated` notification on the `AWSLogs/*.json.gz` prefix triggers `lttm-config-snapshot-transform`, which reads snapshots, splits them by account, and writes NDJSON into `config-snapshot/`.
- **`cur.tf`** — A BCM Data Exports CUR 2.0 export in us-east-1 writing monthly Parquet into `cur/lttm-cur-export/data/BILLING_PERIOD=YYYY-MM/`.
- **`flowlogs.tf`** — One `aws_flow_log` per VPC, per account, per region (main eu-central-1, main us-west-2, dev eu-central-1, prod eu-central-1), direct to S3 as hive-compatible Parquet.
- **`dns.tf`** — Route 53 DNS query logging for every hosted zone in `var.hosted_zone_ids`, with CloudWatch log groups created in us-east-1.
- **`guardduty.tf`** — Detectors across every account and region, Organizations admin delegation, dev/prod member enrollment, and EventBridge rules that forward every GuardDuty finding through per-account Firehose streams into `guardduty/`.
- **`inspector.tf`** — `aws_inspector2_enabler` in all three accounts × three regions, scanning EC2 + Lambda + ECR.
- **`macie.tf`** — `aws_macie2_account` in all three accounts × three regions, plus `null_resource` hooks that call `aws macie2 update-automated-discovery-configuration` because the Terraform provider doesn't expose it.

**Security and access**

- **`guardrails.tf`** — The `lttm-prompt-guard` Bedrock managed guardrail with `PROMPT_ATTACK` filter on input and a denied "off_topic" topic policy. Published as a version the supervisor references via `LTTM_GUARDRAIL_ID` / `LTTM_GUARDRAIL_VERSION`.
- **`cognito.tf`** — User pool + `lttm-cli` app client + API Gateway Cognito authorizer + an initial admin user (temporary password from tfvars). Clients (the `alexandra.sh` CLI, browser, any custom client) `InitiateAuth` here to get a JWT for API Gateway.

**API + Lambdas + DynamoDB**

- **`apigw.tf`** — REST API `lttm-stream-api` with five routes, all Cognito-authorized: `POST /ask` → streaming Lambda, `GET /conversations` → list, `DELETE /conversations/{id}` → delete, `GET /health` → runtime health, `GET /services` → sub-agent catalog.
- **`lambda.tf`** — The five Lambdas behind those routes: `lttm-invoke-agent-stream`, `lttm-list-conversations`, `lttm-delete-conversation`, `lttm-health-check`, `lttm-list-services`. Each has its own IAM role and is zipped from `lambda/<name>/` at apply time.
- **`dynamodb.tf`** — `<prefix>-conversations` table. Hash key `session_id`, GSI on `user_id + last_active` for listing, and a TTL column `expires_at` (30 days). Written fire-and-forget by the streaming Lambda, read by `list_conversations`, cleared by `delete_conversation`.

## `terraform/lambda/` — Lambda source

- **`invoke_agent_stream/index.mjs` + `package.json` + `package-lock.json`** — Node.js 20, AWS SDK v3. Parses the JWT `sub` claim for `user_id`, invokes the AgentCore streaming runtime via `InvokeAgentRuntimeCommand`, and pipes the SSE response straight to API Gateway. In parallel, fire-and-forget `UpdateItem` on the conversations table for session metadata — slow DynamoDB never blocks the token stream.
- **`list_conversations/index.mjs`** — `Scan` of the conversations table, unmarshalled to plain objects, sorted by `last_active` descending.
- **`delete_conversation/index.mjs`** — `DeleteItem` with `ReturnValues: ALL_OLD`. 200 on hit, 404 on miss.
- **`health_check/index.mjs`** — `GetAgentRuntime` on the AgentCore control plane. 200 if `READY`, 503 otherwise.
- **`list_services/index.mjs`** — Static JSON list of the twelve routable sub-agent domains with keyword triggers. Useful for CLI autocomplete or help menus.
- **`config_transform/index.py`** — Python 3.12 Firehose record transformer for Config. Extracts `detail.configurationItem`, lowercases field names, emits newline-delimited JSON. Attached to all three Config Firehose streams.
- **`config_snapshot_transform/index.py`** — Python 3.12 S3 trigger. Reads a `ConfigSnapshot/*.json.gz`, decompresses, groups `configurationItems` by account, writes partitioned NDJSON under `config-snapshot/`.

Terraform builds the zip artifacts from these source directories at apply time via `archive_file`; the `.zip` files are not tracked in git.

## Root files

- **`alexandra.sh`** — Bash CLI. Authenticates against Cognito, caches the JWT, opens an SSE stream to `POST /ask`, prints live status while the agent works. Flags: `--new`, `--session`, `--clean`, `--history`, `--delete`, `--health`, `--services`, `--notboring`.
- **`lttm_game.py`** — Optional Python arcade game rendered in the terminal while the agent is thinking, activated by `alexandra.sh --notboring`.
- **`README.md`** — Main project README.
- **`LICENSE`** — MIT.
- **`.gitignore`** — Excludes `.terraform/`, `*.tfstate*`, `__pycache__/`, `.pytest_cache/`, `terraform/lambda/*.zip`, `.bedrock_agentcore.yaml`, `.bedrock_agentcore/`, `agents/hooks/retired/`, `tests/`, `scripts/`, `pytest.ini`, personal notes, and local credentials.
