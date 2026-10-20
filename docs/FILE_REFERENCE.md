<!-- Copyright (c) 2025-2026 Michal Salanci -->
<!-- SPDX-License-Identifier: MIT -->

# File reference

Per-file documentation for the LTTM v3 codebase, as of October 2026. Files that share the same shape (for example the API-based sub-agents) are grouped.

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

`agents/` is the zip root of the AgentCore deployment, so the imports inside it are flat (`from tools.athena_tool import …`).

- **`supervisor_agent.py`** — The AgentCore entry point (`@app.entrypoint invoke`).
  - **Before the agent runs**, it checks the session ID (a lower-case UUID), answers questions about LTTM's own internals with a fixed text (`PROBE_ANSWER`), and runs the question through the Bedrock guardrail (`utils/prompt_guard.py`).
  - **Per session**, it keeps one supervisor (`SupervisorSession`: agent, hooks, steering handler, memory hook, lock), up to 20 sessions per process. A second question sent while the first is still running is rejected.
  - **The agent:** Claude Opus 4.6 (`SUPERVISOR_MODEL`) with the 13 sub-agents as tools, a cached system prompt (1-hour cache point), today's date filled in per question, and a 600 s read timeout.
  - **Wired in:** `OutputIntegrityHook`, `ArchitectureGuardHook`, `LTTMMemoryHook`, `LTTMLoggingPlugin` and `SupervisorSteeringHandler`.
  - **Streaming:** the agent runs on a worker thread, and `invoke()` yields its SSE events (status, 💰 receipt, cache lines, result).
  - **Connects to:** all sub-agents, `hooks/*`, `plugins/*`, `utils/*` and AgentCore Memory.

- **`cloudtrail_agent.py` / `cloudwatch_agent.py` / `config_agent.py` / `cur_agent.py` / `flowlogs_agent.py`** — The Athena-based sub-agents.
  - **What each does:** turns the request into SQL against its Glue table (`lttm_logs.cloudtrail_logs`, `cloudwatch_logs`, `config_logs` + `config_snapshot`, `cur_data`, `flowlogs`), runs it through `tools/athena_tool.py`, and returns the raw rows.
  - **How it is built:** a factory (`_new_<name>_agent()`) builds a fresh agent with fresh `SQLValidatorHook` and `SQLRewriteHook` for every question. The input comes from `utils/delegation.py`.
  - **CloudTrail:** region rules from the `*_REGIONS` constants, deduplication on `eventid`, and sign-ins split into console logins, IAM Identity Center and WorkMail. Its prompt is cached.
  - **CloudWatch:** also exposes `list_log_groups`, which lists the lake's log groups per account with one S3 request. It adds code-written count and coverage lines, refuses LTTM's own runtime log groups, and its prompt is cached.
  - **Config:** picks `config_snapshot` (the newest daily inventory) or `config_logs` (every change) by what the question is about. It adds a code-written line for regions where Config does not record.
  - **CUR:** money amounts and totals come from `utils/money.py`.
  - **Flow Logs:** names the regions and accounts where flow logs are not collected.
  - **A question about several accounts** runs one query with `account_id IN (…)`, grouped by account.

- **`guardduty_agent.py`** — Two sources:
  - current findings through the GuardDuty API (`tools/guardduty_tool.py`), in every GuardDuty region by default;
  - the history in the lake's archive (`tools/guardduty_history_tool.py`).

  Totals over regions and accounts are computed by code. Sample findings are reported as test data. It registers the SQL hooks for the Athena path, and its prompt is cached.

- **`access_analyzer_agent.py` / `health_agent.py` / `inspector_agent.py` / `macie_agent.py` / `organizations_agent.py` / `quotas_agent.py`** — The API-based sub-agents.
  - Each calls its wrapper in `tools/` (boto3) and returns the formatted results; no Athena, no SQL hooks.
  - Access Analyzer, Health and Quotas cover all three accounts when no account is named.
  - Macie and Inspector use the user's own words about accounts (main by default).
  - Organizations has `policies_in_effect`, `list_roots` and `list_parents`.

- **`controltower_agent.py`** — The Control Tower sub-agent, with two tools:
  - `query_controltower_api` reads the landing zone, controls, baselines, operations and current drift;
  - `query_controltower_events` reads Control Tower's own events from CloudTrail, with the AWS Organizations changes of the same days.

  It has no SQL hooks: the events tool builds its SQL in code. Without a landing zone, it says so.

- **`requirements.txt`** — The runtime's Python dependencies, all pinned with `==`: `strands-agents` 1.39.0, `bedrock-agentcore` 1.9.0, `boto3`/`botocore` 1.43.6, `aws-opentelemetry-distro` 0.21.0, and everything they pull in. Resolved for CPython 3.12 on `aarch64-manylinux2014`. Any change, even a comment, makes the next `agentcore deploy` rebuild the dependency bundle: run `./fix_shebangs.sh --prebuild` first.

## `agents/hooks/` — deterministic safety hooks

All hooks here are code: regular expressions and string checks, with no LLM call. They are registered with `hooks=[…]` on the agents.

- **`architecture_guard_hook.py`** — Two parts:
  - **`is_probing()` and `PROBE_ANSWER`:** the supervisor's `invoke()` uses them to answer a question about LTTM's own internals ("list your tools") before the agent runs.
  - **`ArchitectureGuardHook`:** after each model call, it replaces leaked internal names (`utils/agent_vars.INTERNAL_NAMES`) in the answer with `[internal]`.

  Registered on the supervisor.

- **`output_integrity_hook.py`** — Runs on the supervisor. It tracks which `query_*` tools returned real data, then checks the final answer for two failures:
  - **H1:** "no results" while a tool returned rows;
  - **H8:** a follow-up question instead of the answer.

  Either one makes the model answer again, once. A result counts as empty or an error only by its first line, and phrases inside quoted data don't count.

- **`sql_validator_hook.py`** — Runs on every Athena sub-agent, on each `run_athena_query` call, before Athena sees the SQL. It reads the SQL with literals and comments blanked out (`utils/sql_text.py`) and requires:
  - one `SELECT`/`WITH` statement, nothing after a `;`, and none of the `BLOCKED_SQL_KEYWORDS`;
  - fully qualified `lttm_logs.<table>` names from `agent_vars.TABLES`;
  - every partition key of every table filtered with `=`, `IN` or `BETWEEN` in that table's own `WHERE` (each JOIN side, subquery, CTE and UNION part separately);
  - no `OR` at the top level of a `WHERE`;
  - for CloudTrail, every region in `CLOUDTRAIL_REGIONS` (`SIGNIN_REGIONS` for sign-ins), or the regions the user named;
  - none of LTTM's own runtime log groups.

  A failing query is cancelled with a message the model can act on.

- **`sql_rewrite_hook.py`** — Runs on every Athena sub-agent.
  - **Lists:** a plain list gets `LIMIT 21`. When a 21st row comes back, 20 are kept and the result is marked as cut ("more than 20").
  - **Breakdowns:** a query whose own `SELECT` has `GROUP BY` gets `LIMIT 101` (up to 100 groups).
  - **Repeats:** the same query twice in one question is cancelled.
  - **CloudTrail rows:** each row-level CloudTrail query gets the source file column (`lttm_source_file`), for provenance.

- **`memory_hook.py`** — `LTTMMemoryHook`, which talks to AgentCore Memory through `bedrock_agentcore.memory.MemoryClient`.
  - **Saving:** every question and answer becomes an event. A test run (`LTTM_TEST_RUN=1`) and the steering checks' corrections are never saved.
  - **First question of a session (per process):** it retrieves 5 facts (namespace `default`) and 3 episodic reflections (actor level), unless `--clean` is set.
  - **After a process restart:** it reloads the last 10 messages and up to 3 summary topics.
  - **Where the blocks go:** after the prompt's cache point, so they don't break the cache.

  Actor: `lttm-admin`.

## `agents/plugins/` — Strands plugins

- **`supervisor_steering.py`** — `SupervisorSteeringHandler`. It runs in the request path, but only code checks change anything.
  - **Code checks that act** (at most one code-made correction per question):
    - the **routing table** (`ROUTING_RULES`): a DNS question sent to `query_flowlogs` is pointed to `query_cloudwatch`;
    - the **money check** (`utils/money.py`): an answer that states a dollar amount no cost result holds;
    - the **answer check** (`utils/answer_check.py`).
  - **LLM judges in shadow mode:** Claude Haiku 4.5 judges routing before each tool call, and checks the final answer against the question's tool results. They log `SHADOW GUIDE …` and never block a call or rewrite an answer.
  - **The coverage log:** it notes result rows that the answer leaves out. It only logs.
  - **Per question:** the ledger is cleared, so a follow-up is never checked against an earlier question's rows.

- **`logging_plugin.py`** — `LTTMLoggingPlugin`, the central lifecycle logger. It prints five tagged events (`INVOKE_START`, `INVOKE_END`, `TOOL_CALL`, `TOOL_DONE`, `TOOL_ERROR`) for the supervisor, the one agent that registers it. Filter with `[LTTM:Log]` in the runtime log group.

## `agents/tools/` — `@tool` functions

- **`athena_tool.py`** — `run_athena_query` and `format_athena_rows`.
  - **Running:** it runs SQL in the `lttm-athena-workgroup`, waits for the result, and adds the bytes scanned to the question's receipt.
  - **Formatting:** it escapes line breaks in values, labels CloudTrail rows with their provenance, and writes "Results: more than N rows" for a cut list.
  - **On the 10 GiB cap:** when Athena cancels a query for scanning too much, it tells the model to narrow the query and retry.

- **`cloudtrail_provenance.py`** — `with_source_file()` adds the source file column to a CloudTrail query. `label_rows()` checks each row's file against CloudTrail's digest files, and labels it `SIGNED`, `NO DIGEST`, `PENDING`, `UNVERIFIED` or `MISMATCH`.

- **`guardduty_tool.py`** — `query_guardduty_findings`: current findings through the GuardDuty API.
  - It covers every GuardDuty region by default, or the regions named.
  - Filters use `Equals`, `GreaterThanOrEqual` and `LessThanOrEqual`, and each list is limited to the asked account (main administers dev and prod).
  - Each finding carries its own account, and each list starts with a count line by severity.

- **`guardduty_history_tool.py`** — `query_guardduty_history`: one Athena query over the archive (`lttm_logs.guardduty_findings`).
  - It counts each finding once, in its latest version, and reads every result page.
  - The counts are made by Python, and sample findings are counted apart from real ones.

- **`access_analyzer_tool.py`** — `query_access_analyzer_api`: the organization analyzer `lttm-org-analyzer` in the main account.
  - It filters by the accounts asked (all three by default).
  - It adds `GetFinding` details: the principal, conditions, actions and a `Public:` line.
  - It marks each organization ID in a condition as this organization or another one.
  - It refuses a region where Access Analyzer does not run.

- **`macie_tool.py` / `inspector_tool.py`** — `query_macie_findings` and `query_inspector_findings`.
  - Each covers every enabled region by default, starts its list with a count line, and says "not enabled in <region>" outside its regions.
  - Macie reads severity from `severity.description` and can filter by bucket.
  - Inspector filters CVEs by `vulnerabilityId`, has a `finding_status` (default `ACTIVE`), and builds statistics with `aggregationRequest`.

- **`health_tool.py`** — `query_health_api`: AWS Health events, affected entities and event details, through the read roles in dev and prod. Without a Business or Enterprise Support plan, every call returns the support-plan message.

- **`organizations_tool.py`** — `query_organizations_api`, in the management account. Actions:
  - `describe_organization`, `list_accounts`, `list_roots`, `list_ous_for_parent`, `list_accounts_for_parent`, `list_parents`, `describe_ou`;
  - `list_policies`, `list_policies_for_target`, `describe_policy`;
  - **`policies_in_effect`**: walks from an account or OU up to the root, and lists every policy on the way once. It says where each policy is attached, and how SCPs combine. It marks SCPs managed by AWS Control Tower, and notes that SCPs do not restrict the management account.

- **`quotas_tool.py`** — `query_quotas_api`: `list_services`, `get_service_quota`, `list_request_history`. IAM, CloudFront and Route 53 quotas are always read in `us-east-1`, and a keyword filter runs before the result is cut.

- **`controltower_tool.py`** — `query_controltower_api`. Actions: `overview`, `landing_zone`, `enabled_controls`, `control_details`, `enabled_baselines`, `operations`, `operation`.
  - It finds the landing zone with `ListLandingZones` on every call, never from a stored ID. Without one, it answers that there is none.
  - It writes count lines per operation kind.
  - For each `NOT_CHECKING` or `UNKNOWN` drift status, it adds a line saying that drift is not known, rather than absent.

- **`controltower_events_tool.py`** — `query_controltower_events`: Control Tower's API calls and lifecycle events from `cloudtrail_logs`.
  - It reads the main account in the Control Tower region, with SQL built in code, and leaves read-only calls out unless asked.
  - It counts by event name and by who started it.
  - It lists the AWS Organizations account and OU changes of the same days.
  - It ends with a line saying that CloudTrail records what was done and by whom, not why.

## `agents/utils/` — shared helpers

- **`agent_vars.py.example`** — The template of `agent_vars.py`, the single source of truth for every shared constant.
  - **What it holds:**
    - the accounts (`ACC1_ID`/`ACC1_LABEL`/`ACC1_EMAIL`, …, `ACCOUNTS`);
    - the models (`SUPERVISOR_MODEL`, `SUBAGENT_MODEL`, `JUDGE_MODEL`) and `build_bedrock_model()`;
    - the prompt cache (`CACHE_TTL`, `cached_system_prompt()`);
    - the lake (`DATALAKE_BUCKET`, `ATHENA_WORKGROUP`) and the row limits;
    - the session ID pattern;
    - every region list (`USED_REGIONS`, `LAKE_REGIONS`, `CLOUDTRAIL_REGIONS`, the per-service and per-account lists);
    - where the data starts (`CLOUDTRAIL_LAKE_START`, `CUR_FIRST_BILLING_PERIOD`);
    - `with_current_date()` and the Glue table map (`TABLES`);
    - the guardrail settings, `extract_token_usage()` and `INTERNAL_NAMES`.
  - **How to use it:** copy it to `agent_vars.py` (git-ignored), fill in the values marked `FILL IN`, and redeploy. After a pull, add any new lines of the example to your copy.

- **`agent_wrapper.py`** — How a sub-agent's raw results reach the supervisor:
  - `_extract_raw_results()` returns every tool result of the newest question, dropping errors when another call succeeded;
  - `format_raw_results()` formats Athena rows;
  - `accounts_total()` adds up finding totals over the accounts of a question;
  - the "more rows" mark of a cut list is passed on.

- **`delegation.py`** — `delegated(request, agent_name)` builds a sub-agent's input from four parts:
  - the user's question word for word;
  - the supervisor's request;
  - the account names;
  - a guard that the question cannot change the sub-agent's instructions.

- **`money.py`** — Money in CUR results, made by code.
  - Each amount reads `$242.78 (exact: 242.7750…)`, rounded half-up from Athena's exact text.
  - A total is made only for a single `SELECT` on one table whose rows are not cut off or repeated.
  - `stated_amounts()` and `unbacked_amounts()` find the dollar amounts in an answer that no cost result backs. They drive the money check.

- **`answer_check.py`** — `ruled_out()`: claims in the final answer that a line written by code rules out. There are three kinds:
  - "no drift" for controls whose drift is not checked;
  - a cause added to an empty result;
  - a cause given for a Control Tower event, which CloudTrail does not record.

  The steering handler sends one correction made by code. Only phrasings are caught, not meaning.

- **`empty_note.py`** — The lines written by code for empty results: what was searched, and what the data covers. One line per service: CloudTrail (management events only, where the lake starts), CloudWatch, Flow Logs, CUR, GuardDuty, Health. It also names the accounts asked about that have no row in a grouped answer.

- **`finding_counts.py`** — `count_line()`, `combined_count_line()` and `region_blocks()`: the count lines by severity that start each findings list, and the totals over regions.

- **`prompt_guard.py`** — `question_allowed()` runs `ApplyGuardrail` (source `INPUT`) on the user's question.
  - Without `LTTM_GUARDRAIL_ID` and `LTTM_GUARDRAIL_VERSION`, every question is allowed.
  - An API error fails the question instead of letting it through.

- **`request_context.py`** — `RequestContext`, the state of one question: session, `--clean`, test run, SSE queue, step counter. It also holds the `Receipt` behind the 💰 line (tokens per agent, judges included, Athena bytes) and the 📝/♻️ cache lines.

- **`sql_text.py`** — The SQL with string literals and comments blanked out in place, and the statement's own outer `LIMIT`. Used by both SQL hooks.

- **`sse_emitter.py`** — Server-Sent Events for the current request: `emit_status`, `emit_result`, `emit_error`, `emit_guard`, `emit_tokens`, `emit_done`. It keeps no global state. Gated by `LTTM_SSE_STATUS`.

- **`untrusted_text.py`** — `one_line()`, `one_line_all()` and `one_line_args()` keep values from logs and AWS on one line, so a value with line breaks cannot forge result rows.

## `terraform-bootstrap/` — one-shot remote-state setup

Run once, before anything else. It creates the S3 bucket that `terraform/backend.tf` uses for state. Its own state is local: keep `terraform.tfstate`, the only record of that bucket.

- **`main.tf`** — The state bucket, with versioning, SSE-S3 encryption and a full public-access block. Default tags: `project = lttm-v3-bootstrap`, `terraform = yes`.
- **`variables.tf`** — Inputs: `backend_bucket`, `backend_region`, `backend_region_profile`.
- **`versions.tf`** — Terraform ≥ 1.14, AWS provider `~> 6.43`.
- **`terraform.tfvars.example`** — Copy to `terraform.tfvars` (git-ignored) and fill in: the state bucket's name, region and CLI profile.

## `terraform/` — everything else

Deployed with `terraform init -backend-config=backend.hcl && terraform apply`, with the S3 backend from the bootstrap step. One file per concern, and deliberately no modules.

**Foundations**

- **`main.tf`** — The nine AWS provider blocks: three accounts × eu-central-1, us-east-1 and us-west-2. All carry the default tags `project = lttm-v3` and `terraform = yes`.
- **`variables.tf`** — Every input, with defaults where possible. Among them:
  - the accounts, regions and CLI profiles;
  - `prefix` (the lake bucket's name), the Cognito first user and the Route 53 zones;
  - `cli_stream_runtime_arn`, the runtime ARN (a placeholder until the first deploy);
  - `memory_retention_days`, `stream_timeout_seconds`, `evaluation_sampling_percentage`;
  - `agent_models`, the inference profiles the agent role may call (equal to the models in `agent_vars.py`);
  - the Athena settings (`athena_bytes_scanned_cutoff_per_query`, `athena_results_retention_days`);
  - the Config settings (`config_snapshot_schedule`, `config_home_name`);
  - `lake_check_schedule` and `lake_alert_email`;
  - the log groups kept out of the lake.
- **`terraform.tfvars.example`** — Copy to `terraform.tfvars` (git-ignored) and fill in.
- **`versions.tf`** — Terraform ≥ 1.14; providers `aws ~> 6.58.0`, `time ~> 0.13.1`, `archive ~> 2.7.1`.
- **`backend.tf`** — The S3 backend: the state's key and S3 native locking. A backend block can't read variables, so the bucket and its region come from `backend.hcl` (git-ignored; copy `backend.hcl.example` and set your bootstrap bucket).
- **`outputs.tf`** — What the later steps need:
  - the lake bucket and the Athena workgroup;
  - the Firehose and trail ARNs, and the DNS log groups;
  - the Cognito IDs and the OIDC discovery URL;
  - the stream API URL;
  - the AgentCore Memory ID and ARN;
  - the conversations table;
  - the guardrail ID and version;
  - the Config snapshot schedules;
  - the evaluation config and its log group.

**Data lake**

- **`s3.tf`** — The lake bucket (`var.prefix`), its bucket policy and its lifecycle rules.
  - **The policy** lets each producer write only its own prefix: CloudTrail, the Firehose streams of main, dev and prod (each limited to its delivery role), AWS Config delivery, the CUR, Flow Logs and GuardDuty.
  - **The agent role** may read the lake, and write only `athena-results/`.
  - **The lifecycle rules** expire Athena results after 30 days, and abort incomplete uploads after 7.
- **`s3_notification.tf`** — The lake bucket's one notification resource: an S3 `ObjectCreated` on a delivered snapshot triggers `lttm-config-snapshot-transform` (`config_snapshot.tf`), and a file in a Firehose error folder goes to the alert topic of `alerts.tf`.
- **`iam_cloudwatch_pipeline.tf`** — The roles of the CloudWatch Logs pipeline: the Firehose execution roles and the CloudWatch Logs → Firehose roles, per account and region.
- **`iam_agent_read_roles.tf`** — The cross-account read roles that the agent role assumes in dev and prod: `LTTMHealthReadRole`, `LTTMQuotasReadRole`, `LTTMGuardDutyReadRole`, `LTTMMacieReadRole`, `LTTMInspectorReadRole`.
- **`alerts.tf`** — The SNS topic `lttm-lake-error-files` and its email subscription (`lake_alert_email`, or else the Cognito email). Only S3 events of the lake bucket may publish to it. A file in any of the Firehose error folders sends an email; confirm the subscription email first.
- **`lake_check.tf`** — The `lttm-lake-check` Lambda, its role, its Lake Formation grants, and an EventBridge Scheduler schedule (daily, 02:30 UTC). It emails only when a check fails.

**Athena + Lake Formation**

- **`athena.tf`** — The `lttm-athena-workgroup`, which cancels any query over 10 GiB scanned, and the `lttm_logs` Glue database.
  - **Seven tables with partition projection:** `cloudtrail_logs`, `cloudwatch_logs`, `config_logs`, `config_snapshot`, `cur_data`, `flowlogs`, `guardduty_findings`.
  - **The CloudTrail table** projects 17 regions; keep them equal to `LAKE_REGIONS` in `agent_vars.py`. Its location reads the organization ID from `aws_organizations_organization`.
  - Changing a table's partitions means editing this file and `agent_vars.TABLES`.
- **`lakeformation.tf`** — `SELECT`/`DESCRIBE` on the seven tables (and `DESCRIBE` on the database) for the agent role.

**Agent runtime**

- **`agents.tf`** — The `lttm-agent-role`, trusted by `bedrock-agentcore.amazonaws.com`, with one inline policy:
  - Bedrock `InvokeModel` for the models in `var.agent_models`: for each, its inference profile's ARN and its foundation model's ARN in every Region;
  - Athena, Glue and Lake Formation; the lake (read) and `athena-results/` (write);
  - AgentCore Memory, the guardrail, X-Ray and CloudWatch Logs;
  - `sts:AssumeRole` into the read roles;
  - read-only Control Tower and Organizations actions.

  The file also declares the AgentCore Memory resource and its three strategies, all with `namespace_templates`:
  - semantic (namespace `default`);
  - summarization (`{sessionId}`);
  - episodic (episodes per actor and session, reflections per actor).

  The runtime itself is not in Terraform: the `agentcore` CLI deploys it.
- **`guardrails.tf`** — The `lttm-prompt-guard` Bedrock guardrail in us-west-2: a `PROMPT_ATTACK` filter on input (HIGH) and a denied `off_topic` topic, published as a version. The runtime gets them as `LTTM_GUARDRAIL_ID` / `LTTM_GUARDRAIL_VERSION` at `agentcore deploy` (its two `--env` flags).
- **`evaluations.tf`** — AgentCore online evaluation `lttm_datalake_quality_eval` on the runtime's `DEFAULT` endpoint, with its role.
  - Five built-in evaluators: Helpfulness, ToolSelectionAccuracy, GoalSuccessRate, Correctness, Faithfulness.
  - It samples `evaluation_sampling_percentage` of the sessions (default 10).
  - It is created only once `cli_stream_runtime_arn` is not the placeholder. `0` turns it off.

**Pipelines — the log sources**

- **`cloudtrail.tf`** — The organization-wide multi-region trail `lttm-org-trail`, writing under `cloudtrail/` in the lake.
- **`cloudwatch.tf`** — The CloudWatch Logs path into the lake:
  - **Five Firehose streams** (main eu-central-1, dev, prod, main us-west-2, main us-east-1), with GZIP decompression, metadata extraction and one JSON record per line.
  - **One account-level subscription policy per account and region**, which sends every log group into its stream, except the excluded ones: Control Tower's CloudTrail copy, and LTTM's own runtime, evaluation and telemetry log groups.
- **`config_home_region.tf`** — AWS Config in each account's home region (eu-central-1): the recorder, which also records the global resources, the delivery channel into the lake, and the recorder status. Dev and prod use Terraform's own names (`config_home_name`, default `default`).
- **`config_multiregion.tf`** — AWS Config in us-east-1 and us-west-2 of the 3 accounts: the recorders, the delivery channels into the lake, and the recorder statuses. EventBridge rules forward their Config events to the eu-central-1 default bus.
- **`config_pipeline.tf`** — EventBridge rules → three Firehose streams (main, dev, prod) → the `lttm-config-transform` Lambda → S3 under `config/`.
- **`config_snapshot.tf`** — The daily inventory:
  - **One EventBridge Scheduler schedule per delivery channel** (9, at 00:01 UTC), each calling `config:DeliverConfigSnapshot`, with a scheduler role per account.
  - **The transformer** `lttm-config-snapshot-transform`, which the lake bucket's notification (`s3_notification.tf`) starts on a delivered snapshot, writes `config-snapshot/`.
- **`cur.tf`** — A BCM Data Exports CUR 2.0 export in us-east-1, writing monthly Parquet into `cur/lttm-cur-export/data/BILLING_PERIOD=YYYY-MM/`.
- **`flowlogs.tf`** — VPC Flow Logs, direct to S3 as Parquet, kept on every VPC by `lttm-flowlog-enforcer`.
  - The enforcer runs every 15 minutes, and on `CreateVpc`.
  - It has its role in main and `LTTMFlowLogEnforcerRole` in dev and prod.
  - The old per-VPC `aws_flow_log` resources are `removed` from Terraform; their flow logs stay in AWS.
- **`dns.tf`** — Route 53 DNS query logging for every hosted zone in `var.hosted_zone_ids`, with the log groups in us-east-1.
- **`guardduty.tf`** — GuardDuty in the three accounts: detectors in every account and region, the Organizations admin delegation, and dev/prod membership. S3 Protection is off in the main account's eu-central-1.
- **`guardduty_archive.tf`** — The archive of GuardDuty findings, which the API keeps for only 90 days: EventBridge rules send every finding through per-account Firehose streams into `guardduty/`, with their roles. A rule in us-west-2 forwards main's findings there to eu-central-1.
- **`inspector.tf`** — `aws_inspector2_enabler` in 3 accounts × 3 regions, scanning EC2, Lambda and ECR.
- **`macie.tf`** — `aws_macie2_account` in 3 accounts × 3 regions, plus `terraform_data` resources that call the AWS CLI to turn on automated sensitive-data discovery, which the provider does not expose.
- **`access_analyzer.tf`** — Trusted access for IAM Access Analyzer in the organization, and the organization analyzer `lttm-org-analyzer` in eu-central-1, us-east-1 and us-west-2 (main account).

**Security and access**

- **`cognito.tf`** — The user pool, the `lttm-cli` app client, and the first user (temporary password from tfvars). The CLI signs in here to get a JWT for API Gateway.

**API + Lambdas + DynamoDB**

- **`apigw.tf`** — The REST API, with five Cognito-authorized routes:
  - `POST /ask` → stream Lambda, with the timeout of `stream_timeout_seconds` (900 s);
  - `GET /conversations` → list;
  - `DELETE /conversations/{id}` → delete;
  - `GET /health` → runtime health;
  - `GET /services` → sub-agent catalog.

  A changed route, method, integration or authorizer redeploys the stage.
- **`lambda.tf`** — The five Lambdas behind those routes, all `nodejs24.x`: `lttm-invoke-agent-stream`, `lttm-list-conversations`, `lttm-delete-conversation`, `lttm-health-check` and `lttm-list-services`. Each has its own IAM role, and is zipped from `lambda/<name>/` at apply time. The stream Lambda has X-Ray active tracing.
- **`dynamodb.tf`** — The `<prefix>-conversations` table: hash key `session_id`, a GSI on `user_id` + `last_active`, and the TTL attribute `expires_at` (30 days after the last question).

## `terraform/lambda/` — Lambda source

Terraform zips each folder with `archive_file` at apply time; the `.zip` files are not in git.

- **`invoke_agent_stream/index.mjs` + `package.json` + `package-lock.json`** — The stream shim, Node.js 24, with a bundled `@aws-sdk/client-bedrock-agentcore` (run `npm ci` in this folder before the first apply).
  - **Checks:** it refuses with HTTP 400 a body that is not a JSON object, a blank prompt, or a session ID that is not a lower-case UUID.
  - **Streaming:** it invokes the AgentCore runtime with `InvokeAgentRuntimeCommand`, and pipes the SSE response to API Gateway.
  - **Conversations:** it writes the conversation row (title cut to 100 characters and cleaned of control characters, question count, `expires_at`).
  - **Logs:** one `[LTTM:Stream] AgentCore call failed` line for each failed call.
- **`list_conversations/index.mjs`** — A `Scan` of the conversations table, sorted by `last_active`, newest first.
- **`delete_conversation/index.mjs`** — `DeleteItem` with `ReturnValues: ALL_OLD`: 200 on a hit, 404 on a miss.
- **`health_check/index.mjs`** — `GetAgentRuntime` on the AgentCore control plane: 200 if `READY`, 503 otherwise.
- **`list_services/index.mjs`** — A static list of the 13 sub-agents with their descriptions and keywords (`--services`).
- **`config_transform/index.py`** — Python 3.12 Firehose transformer for Config change events.
  - It takes `detail.configurationItem` out of the EventBridge envelope, and lowercases the field names.
  - It writes capture and creation times as UTC `YYYY-MM-DDTHH:MM:SS.mmmZ`.
  - It replaces an oversized change notification with the full record.
  - It marks a record without a readable time as failed, which lands it in `config-errors/` and sends an email.
- **`config_snapshot_transform/index.py`** — Python 3.12 S3 trigger.
  - It reads a delivered `ConfigSnapshot/*.json.gz` and groups its items by account.
  - It files them under the day the snapshot describes, which it reads from the file's own name.
  - It writes NDJSON under `config-snapshot/`; unreadable records go to `config-errors/snapshot-unreadable/`.
- **`flowlog_enforcer/index.py`** — Python 3.12. In each account and region, it gives every VPC without a flow log one into the lake.
- **`lake_check/index.py`** — Python 3.12. The daily checks of the rows filed under the day before:
  - `config_logs`: every time in UTC and filed under its own UTC day, the account ID equal to its folder's, and no item twice;
  - `config_snapshot`: the same time and account checks;
  - `cloudtrail_logs` and `guardduty_findings`: every time in UTC;
  - with S3: each delivered Config snapshot matched to its `config_snapshot` file by its records, and a snapshot from every delivery channel.

  It emails through `alerts.tf` only when something is wrong.

## Root files

- **`alexandra.sh`** — The Bash CLI.
  - **Sign-in:** it authenticates against Cognito, including the first login, which sets a new password. It caches the token in `~/.lttm_token` (mode 600), and keeps passwords and tokens off the command line.
  - **Questions:** it opens an SSE stream to `POST /ask`, and prints live status, the 💰 receipt and the cache lines.
  - **Flags:** `--new`, `--session <uuid>`, `--clean`, `--history`, `--delete [id]`, `--health`, `--services`, `--notboring`.
  - **Environment variables:** `LTTM_STREAM_API_URL`, `COGNITO_USER_POOL_ID`, `COGNITO_CLIENT_ID` and `COGNITO_REGION` (read from `terraform output` when unset), and `LTTM_TEST_RUN=1`.
- **`fix_shebangs.sh`** — Repairs the launcher scripts in the cached AgentCore dependency bundle (`agents/.bedrock_agentcore/<agent>/dependencies.zip`), which `uv` points at the local venv's Python.
  - `--prebuild` builds the bundle locally the way `agentcore deploy` does, then repairs it.
  - `--check` reports whether the next deploy reuses it.
  - Needs the venv with the starter toolkit, and `uv` on the PATH.
- **`lttm_game.py`** — An optional terminal arcade game shown while the agent works (`alexandra.sh --notboring`). It sits next to `alexandra.sh`.
- **`README.md`** — The main project README.
- **`LICENSE`** — MIT.
- **`.gitignore`** — Keeps out:
  - your configuration: `*.tfvars` (except the examples), `agents/utils/agent_vars.py`, `agents/.bedrock_agentcore.yaml`;
  - state: `*.tfstate*`, `*.tfplan`, `backend.hcl`;
  - build output: `.terraform/`, the AgentCore build folder, the Lambda zips, `node_modules/`, `__pycache__/`, `*.pyc`.
