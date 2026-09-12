# Deployment

Everything is one SAM/CloudFormation stack in `infrastructure/stack_request/`. The Lambda is a
container image because NLTK corpora, pyarrow and  exceed the zip limit; the
Dockerfile at the repo root bakes all of them in so nothing downloads at runtime.

## Prerequisites

- AWS CLI and SAM CLI, Docker running.
- Bedrock model access enabled in the target region for the two model ids in the template.
- An X developer account with credits (pay-per-use; roughly $0.005 per post read).

## Secrets

Nothing secret is in the repository or in CloudFormation parameters. The function reads
SecureStrings from SSM at startup:

| SSM path (default, per stage) | Used for | Required |
|---|---|---|
| `/sentiment-prep/{stage}/api-key` | the `X-API-Key` every request must carry | **yes, for any internet-facing deployment** |
| `/sentiment-prep/{stage}/x-bearer-token` | X API | only for X fetches |
| custom, set via `DatabaseUrlSsmPath` | Postgres URL for durable history | optional |

```bash
make put-secret NAME=api-key VALUE="$(python -c 'import secrets;print(secrets.token_urlsafe(32))')" STAGE=dev
make put-secret NAME=x-bearer-token    VALUE='AAAA…'   STAGE=dev
```

**Switching X accounts** is one of: overwrite the parameter with `put-secret`, or point
`XBearerTokenSsmPath` in `samconfig.toml` at a different parameter and redeploy. Locally, set
`X_BEARER_TOKEN` in `.env`; it takes precedence over SSM.

## Deploy

```bash
make validate                    # cfn-lint on the template
make deploy                      # build image, push, create/update stack (default env)
make deploy-site STAGE=dev       # build the UI against ApiUrl, sync to the site bucket
make deploy CONFIG_ENV=prod      # uses the [prod] block in samconfig.toml
```

Build the UI with the same key (`VITE_API_KEY=… npm run build`, which `make deploy-site` passes
through) or the deployed site cannot call its own API. Check `/health` after deploying: if
`auth_required` is false, the deployment is open to anyone who finds the URL and can spend your X
credits — fix that before sharing the link.

After the first deploy, copy the `SiteUrl` output into the `CorsOrigins` override and deploy
again so the browser can call the API. 

## Least privilege

The role in the template grants exactly the actions the code calls:

| Action | Resource |
|---|---|
| `logs:CreateLogStream`, `logs:PutLogEvents` | the function's own log group |
| `s3:GetObject`, `s3:PutObject` | objects in the one data bucket |
| `s3:ListBucket` | the one data bucket |
| `comprehend:BatchDetectSentiment` | `*` (Comprehend has no resource-level permissions) |
| `pricing:GetProducts` | `*` (Price List API has no resource-level permissions; endpoint is us-east-1 regardless of deploy region) |
| `bedrock:InvokeModel` | the two model ARNs from the parameters |
| `ssm:GetParameter` | the two (or three) exact parameter ARNs |

No `kms:Decrypt` is needed: SecureStrings use the AWS-managed `aws/ssm` key, whose key policy
permits decryption through SSM for principals in the account. The data bucket blocks public
access, denies non-TLS requests, and expires working state after 7 days. The site bucket allows
`s3:GetObject` only. The HTTP API is throttled to 10 rps / burst 20.

Checkpoints (`checkpoints/` prefix) and saves (`datasets/` prefix) use the same bucket grant; the
lifecycle rule only expires `_work/`, so paid snapshots are kept until you delete them.

If you add a service call to the code, add exactly one statement here, in the same commit.

## Database

**Upgrading an existing deployment:** a `spend_day` table was added (created automatically) and
the cached-price column changed from `Float` to `Numeric(12, 6)`. `create_all` does not alter existing tables, so on SQLite delete the file (it is
a cache; it rebuilds) and on Postgres run
`ALTER TABLE price_quote ALTER COLUMN price_per_unit_usd TYPE numeric(12,6);` before deploying.


Default is SQLite on `/tmp`: fine for demos, resets on cold start. For durable history, provision Postgres (RDS, Aurora Serverless v2, Neon, Supabase), store the URL as a
SecureString, set `DatabaseUrlSsmPath`, and add `psycopg[binary]` to `pyproject.toml`
dependencies before building the image. Tables are created on first use (`create_all`); the schema is small and additive. Adopt Alembic if
you ever need a destructive change.

## Operating

- `/health` returns operator details only when `DIAGNOSTICS` is true; it defaults to false in
  Lambda. Leave it that way on a public URL.

- `make logs` tails structured logs. See [logging-and-debugging.md](logging-and-debugging.md) for
  Logs Insights queries.
- Cold start is dominated by SQLAlchemy setup and NLTK import; expect 3–6 s at 2048 MB. Provisioned
  concurrency is the fix if that matters for a demo.
- Client cancellation reaches the Lambda only while the connection is open; API Gateway does not
  propagate a disconnect once the integration has started. Caps remain the hard limit.
