# Athena Audit — Installation Guide

## Prerequisites

### Tools
- **AWS CLI** v2 configured with credentials that have admin-level access
- **Python 3.10+** (used by packaging and deploy scripts)
- **pip** (for installing Lambda dependencies)
- **7z** (7-Zip, used by `package.sh` to create the Lambda ZIP)
- **Bash** (Git Bash on Windows, or native on Linux/macOS)

### AWS Resources (must exist before deployment)

| Resource | Description |
|---|---|
| **S3 code bucket** | Bucket for uploading the Lambda deployment package (e.g., `cf-templates-...`) |
| **S3 audit bucket** | Bucket for storing history, events, and query results |
| **CloudTrail trail** | A trail logging management events to an S3 bucket. Must be **active and logging** |
| **Athena workgroup** | A workgroup for the Events Lambda to run queries (e.g., `primary`) |
| **Athena output bucket** | Bucket where Athena writes query result files (can be the same as audit bucket) |

### For IDC Workgroup Support (optional)

| Resource | Description |
|---|---|
| **cihi_auth module** | `cihi_auth-*.whl` file placed in `bin/` directory |
| **IDC configuration** | `cihi_auth` must have been run locally at least once (`cihi-auth authenticate`) |
| **OIDC/TIP roles** | An OIDC role and an Identity-Enhanced role in IAM (created by the IdP stack) |

---

## Step 1: Configure Your Environment

Copy a config template and fill in your values:

```bash
cp config/dev.json config/<your-env>.json
```

Edit `config/<your-env>.json`:

```json
{
  "Region": "us-east-1",
  "HistoryStackName": "athena-audit-history",
  "EventsStackName": "athena-audit-events",
  "CodeBucket": "<your-code-bucket>",
  "Version": "latest",
  "AuditBucket": "<your-audit-bucket>",
  "CloudTrailBucket": "<your-cloudtrail-bucket>",
  "CloudTrailFolder": "<path-to-cloudtrail-logs>",
  "DatabaseName": "athena_events",
  "EventsFolder": "athena_audit/events",
  "HistoryFolder": "athena_audit/history",
  "REGIONS": "us-east-1",
  "Workgroup": "primary",
  "AthenaOutputFolder": "athena_audit/query_results",
  "AthenaOutputBucket": "<your-athena-output-bucket>",
  "KmsKeyArn": "",
  "WorkgroupsFilter": "",
  "IdcSecretArn": ""
}
```

### Key parameters

| Parameter | Notes |
|---|---|
| `CloudTrailFolder` | Full S3 prefix to the CloudTrail logs. Example: `my-trail/AWSLogs/123456789012/CloudTrail` |
| `WorkgroupsFilter` | Only process workgroups whose name contains this string (e.g., `idc`). Leave empty for all workgroups |
| `KmsKeyArn` | If your S3 buckets use a KMS CMK, provide the ARN. Leave empty for SSE-S3 or no encryption |
| `AthenaOutputBucket` | If different from `AuditBucket`. Leave empty to use the audit bucket |
| `IdcSecretArn` | Leave empty for now; populated in Step 2 if using IDC workgroups |

---

## Step 2: Set Up IDC Secret (IDC workgroups only)

 

### 2a. Configure cihi_auth to use the Athena Audit TIP role

Before authenticating, update `~/.aws_cihi_auth/config.json` to use the dedicated Athena Audit Identity Enhanced role (deployed from `cloudformation/athena_audit_tip_role_cloudformation.yaml`) instead of the default one:

```json
{
  "id_enhanced_role_arn": "arn:aws:iam::<account-id>:role/AthenaAudit-IdEnhancedRole",
  ...
}
```

> ⚠️ **Important:** The `id_enhanced_role_arn` must point to the role created by the Athena Audit TIP role stack. This role has least-privilege permissions scoped to reading Athena query history only. Using the default Identity Enhanced role would grant the Lambda unnecessary permissions (S3 Access Grants, Glue, Redshift, EMR, etc.).

### 2b. Authenticate locally

```bash
cihi-auth authenticate
```

This creates:
- `~/.aws_cihi_auth/config.json`
- `~/.aws_secure/idp_token.json`

### 2b. Create the Secrets Manager secret

```bash
./bin/create_idc_secret.sh [secret-name] [region]
```

Defaults:
- **secret-name:** `athena-audit/idc-tokens`
- **region:** `us-east-1`

The script outputs the secret ARN. Add it to your config file:

```json
"IdcSecretArn": "arn:aws:secretsmanager:us-east-1:123456789012:secret:athena-audit/idc-tokens-XXXXXX"
```

### 2c. Place the cihi_auth wheel in `bin/`

```bash
cp /path/to/cihi_auth-2.0.0-py3-none-any.whl bin/
```

The packaging script automatically detects and includes it.

### 2d. Token refresh

The IdP tokens stored in Secrets Manager have an expiration. When they expire, re-run:

```bash
cihi-auth authenticate
./bin/create_idc_secret.sh
```

---

## Step 3: Deploy

```bash
./bin/deploy.sh <environment>
```

Example:
```bash
./bin/deploy.sh sandbox
```

This script:
1. Packages the Lambda code (`bin/package.sh`)
2. Uploads the ZIP to S3
3. Deploys the History CloudFormation stack
4. Deploys the Events CloudFormation stack
5. Force-updates both Lambda function code

---

## Step 4: Configure Lake Formation Permissions

> **This step is required if Lake Formation is enabled in your account** (common when using IAM Identity Center). If Lake Formation is not enforcing permissions, you can skip this step.

The Events Lambda creates Glue tables. Lake Formation must grant access to these tables for both the Lambda role and any users who will query the data.

### 4a. Grant access to the Events Lambda role

1. Go to **AWS Console → Lake Formation → Data permissions → Grant**
2. **Principal:** IAM role → search for the role containing `AthenaEventsLambdaRole`
3. **Database:** `athena_events` (or your configured `DatabaseName`)
4. **Database permissions:** `All`
5. Click **Grant**
6. **Grant again** → Same role → **Table:** All tables
7. **Table permissions:** `Select`, `Alter`, `Delete`, `Describe`, `Drop`, `Insert`
8. Click **Grant**

### 4b. Grant access to users who will query the data

Repeat the process for each IAM user, role, or SSO role that needs to query the audit tables:

1. **Lake Formation → Data permissions → Grant**
2. **Principal:** The IAM user or role
3. **Database:** `athena_events` → Permissions: `Describe`
4. **Grant again** → **Table:** All tables → Permissions: `Select`, `Describe`
5. Click **Grant**

> ⚠️ **Important:** Make sure you grant access to the correct identity. If you log into the AWS Console via IAM Identity Center (SSO), the principal is the SSO assumed role, **not** the root account or an IAM user. Check the identity shown in the top-right corner of the console.

---

## Step 5: Initial Data Load

### 5a. Run the History Lambda

Invoke the History Lambda with no payload — it will automatically backfill the last 14 days on first run:

```bash
aws lambda invoke \
  --function-name <history-stack-name>-AthenaHistoryLambdaFunction \
  --payload '{}' \
  --region us-east-1 \
  /dev/stdout
```

Or for a specific date range:
```bash
aws lambda invoke \
  --function-name <history-stack-name>-AthenaHistoryLambdaFunction \
  --payload '{"from_day": "2026-02-15", "to_day": "2026-03-01"}' \
  --region us-east-1 \
  /dev/stdout
```

### 5b. Run the Events Lambda

Invoke the Events Lambda with `force_recreate` to create the database and tables (also backfills 14 days):

```bash
aws lambda invoke \
  --function-name <events-stack-name>-AthenaEventsLambdaFunction \
  --payload '{"force_recreate": true}' \
  --region us-east-1 \
  /dev/stdout
```

### 5c. Verify

Query the events table from the Athena console (using the `primary` workgroup):

```sql
SELECT * FROM athena_events.events ORDER BY event_time DESC LIMIT 20;
```

---

## Daily Operations

Once deployed, both Lambdas run automatically every 2 hours via EventBridge:

| Lambda | Schedule | Behavior |
|---|---|---|
| History | Every 2 hours | Finds the latest day in S3, re-processes from there to yesterday |
| Events | Every 2 hours | Finds the latest day in the events table, re-processes from there to yesterday |

### How It Works

Each scheduled invocation receives an empty event (`{}`). With no parameters, the Lambda automatically:

1. **History:** Scans S3 for the latest `day=` partition → uses it as `from_day`
2. **Events:** Queries `SELECT MAX(day) FROM events` → uses it as `from_day`
3. Re-processes from that day to yesterday (overlap catches partial data)
4. On **first run** (no existing data), backfills the last **14 days**

Key properties:
- **No gaps:** If a run fails, the next one picks up from the last successfully written data
- **Idempotent:** Re-processing a day replaces existing data — no duplicates
- **Self-healing:** Late-arriving data or partial failures are caught by the next run

### Manual Backfill

To manually process specific dates, invoke the Lambda with `day` or `from_day`/`to_day`:

```bash
# Single day
aws lambda invoke \
  --function-name <stack-name>-AthenaHistoryLambdaFunction \
  --payload '{"day": "2026-03-01"}' \
  --region us-east-1 /dev/stdout

# Date range
aws lambda invoke \
  --function-name <stack-name>-AthenaHistoryLambdaFunction \
  --payload '{"from_day": "2026-02-15", "to_day": "2026-03-01"}' \
  --region us-east-1 /dev/stdout
```

No manual intervention is needed unless:
- IDC tokens expire (re-run `cihi-auth authenticate` + `create_idc_secret.sh`)
- You need to backfill data for past days (invoke Lambdas manually with date range)
- Tables need recreation after schema changes (`{"force_recreate": true}`)

---

## Troubleshooting

### "COLUMN_NOT_FOUND: Relation contains no accessible columns"

**Cause:** Lake Formation is blocking access to the Glue table columns.

**Fix:**
1. Identify which principal is running the query:
   - If querying from the **Athena console**, check the identity in the top-right of the AWS Console
   - If the **Lambda** is failing, the principal is the Lambda's IAM role
   - If logged in as **root**, root needs separate Lake Formation grants
   - If logged in via **SSO/Identity Center**, the principal is the SSO assumed role, not an IAM user
2. Go to **Lake Formation → Data permissions**
3. Grant the principal `Select`, `Describe` on the table (see Step 4)

**Common pitfall:** The AWS Console may show you're logged in as "Angel Krustev" but you may be using the **root account** rather than an IAM user. Root and IAM users are separate principals in Lake Formation. Verify by checking if the console shows `username @ account-id` (IAM user) vs just the account name (root).

### "You do not have access to \<workgroup\>"

**Cause:** The Lambda's IAM role cannot call `ListQueryExecutions` on an IDC workgroup via standard IAM.

**Fix:** This is expected for IDC workgroups. The History Lambda automatically detects this and retries with the TIP-authenticated client. Ensure:
1. `IdcSecretArn` is set in your config file
2. The secret contains valid (non-expired) tokens
3. The `cihi_auth` wheel is included in the deployment package

### History table is empty

**Cause:** The history table partitions haven't been discovered.

**Fix:** The Events Lambda runs `MSCK REPAIR TABLE` automatically. If querying the history table directly:
```sql
MSCK REPAIR TABLE athena_events.history;
SELECT * FROM athena_events.history LIMIT 10;
```

### Query text is `***OMITTED***` or NULL

**Cause:** CloudTrail redacts `queryString` for TIP/IDC sessions. The query text must come from the History Lambda via the JOIN.

**Fix:**
1. Ensure the History Lambda ran **before** the Events Lambda for that day
2. Re-run the History Lambda: `{"day": "YYYY-MM-DD", "force": true}`
3. Re-run the Events Lambda: `{"day": "YYYY-MM-DD"}`

### Status is empty

**Cause:** The history data was collected with an older version of the code before the `status` field was added.

**Fix:** Re-run the History Lambda with `force` to re-collect:
```json
{"from_day": "YYYY-MM-DD", "to_day": "YYYY-MM-DD", "force": true}
```
Then re-run the Events Lambda for the same date range.

### TIP authentication fails in Lambda

**Symptoms:** Log messages like `TIP authentication failed: ...` or `No module named 'click'`

**Fix:**
1. Ensure `cihi_auth-*.whl` is in the `bin/` directory
2. Re-run `bin/package.sh` and redeploy — it automatically installs `click`, `PyJWT`, `chardet`, `requests`
3. If tokens expired: `cihi-auth authenticate` → `./bin/create_idc_secret.sh`

### Lambda queries appear in the events table

**Cause:** Using an older deployment that doesn't filter Lambda-generated queries.

**Fix:** Redeploy with the latest code. The INSERT query filters out:
- `useridentity.arn LIKE '%AthenaEventsLambda%'`
- `useridentity.arn LIKE '%AthenaHistoryLambda%'`

Then re-run the Events Lambda to reprocess the affected days.

### Tables need schema updates after code changes

**Fix:** Invoke the Events Lambda with `force_recreate`:
```json
{"force_recreate": true, "day": "YYYY-MM-DD"}
```

This drops and recreates all three tables with the current SQL definitions, then reprocesses the specified day. You'll also need to:
1. Re-grant Lake Formation permissions (new tables = new grants needed)
2. Re-run History Lambda if history data needs the new schema fields

### Lambda timeout (300 seconds)

**Cause:** Too many workgroups or too many queries to process in one invocation.

**Fix:**
1. Use `WorkgroupsFilter` to limit which workgroups are processed
2. Process smaller date ranges instead of large backfills
3. Increase the Lambda timeout in the CloudFormation template (max 900 seconds)

### CloudTrail data missing for a day

**Cause:** CloudTrail delivers logs with a delay (typically 5–15 minutes, occasionally longer). Also, CloudTrail only logs events **going forward** from when the trail was created.

**Fix:**
1. Ensure the CloudTrail trail was active on the target day
2. Wait at least 30 minutes after the end of the day before processing
3. The Events Lambda runs at 01:15 UTC to allow CloudTrail delivery time

### "Access Denied" on S3 when Lambda runs

**Cause:** The Lambda role doesn't have S3 permissions for the relevant buckets.

**Fix:** Check that the CloudFormation parameters are correct:
- `AuditBucket` — Lambda needs read/write
- `CloudTrailBucket` — Lambda needs read
- `AthenaOutputBucket` — Lambda needs read/write
- `CodeBucket` — Lambda needs read

If using KMS encryption, ensure `KmsKeyArn` is set in your config.

### Lake Formation grant fails with "Resource does not exist or requester is not authorized"

**Cause:** The principal attempting to grant permissions is not a Lake Formation administrator.

**Fix:** Only Lake Formation administrators can grant permissions. Ask your account administrator to either:
1. Add your IAM user/role as a Lake Formation administrator
2. Grant the required permissions on your behalf

To check who the LF admins are: **Lake Formation → Administrative roles and tasks → Data lake administrators**
