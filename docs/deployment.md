# Deployment

Everything is one SAM/CloudFormation stack in `infrastructure/stack_request/`. The Lambda is a
container image because NLTK corpora and pyarrow exceed the zip limit; the
`backend/Dockerfile` bakes all of them in so nothing downloads at runtime.

## Prerequisites

- AWS CLI and SAM CLI, Docker running.
- An X developer account with credits (pay-per-use; roughly $0.005 per post read).

## Secrets

Nothing secret is in the repository or in CloudFormation parameters. The function reads
SecureStrings from SSM on first use and caches values for at most five minutes. Rotation is
visible after cache expiry; environment variables take precedence:

| SSM path (default, per stage) | Used for | Required |
|---|---|---|
| `/sentiment-prep/{stage}/api-key` | operator login secret; scripts may send `X-API-Key` | **yes, for any internet-facing deployment** |
| `/sentiment-prep/{stage}/x-bearer-token` | X API | only for X fetches |
| custom, set via `DatabaseUrlSsmPath` | Postgres URL for durable history | required for paid X collection in Lambda |

```bash
make put-secret NAME=api-key VALUE="$(python -c 'import secrets;print(secrets.token_urlsafe(32))')" STAGE=dev
make put-secret NAME=x-bearer-token    VALUE='AAAA…'   STAGE=dev
```

**Switching X accounts** is one of: overwrite the parameter with `put-secret`, or point
`XBearerTokenSsmPath` in `samconfig.toml` at a different parameter and redeploy. Locally, set
`X_BEARER_TOKEN` in `.env`; it takes precedence over SSM.

## Deploy

All SAM targets run from the repository root and pass `--template-file` / `--config-file` *after*
the subcommand, which is where the SAM CLI expects them, as absolute quoted paths. SAM resolves
`--config-file` against its own notion of the project root rather than the shell's working
directory, so a relative path fails with "does not exist or could not be read"; the quoting keeps
a checkout under a path containing spaces working.

```bash
make validate                    # cfn-lint on the template
make deploy                      # builds first, then deploys from .aws-sam/build
make deploy-web STAGE=dev       # build the UI against ApiUrl, sync to the site bucket
make deploy CONFIG_ENV=prod      # uses the [prod] block in samconfig.toml
```

The production UI contains no operator secret. Open the site and sign in with the operator key;
the browser exchanges it at `POST /auth/session` for a one-hour in-memory bearer token. Reloading
requires sign-in again. Scripts may send `X-API-Key`. Lambda refuses an unset operator key.

The template adds the HTTPS CloudFront site origin to CORS automatically. Configure additional
origins through `CorsOrigins`; a second deployment to add the site URL is unnecessary.

## Least privilege

The role in the template grants exactly the actions the code calls:

| Action | Resource |
|---|---|
| `logs:CreateLogStream`, `logs:PutLogEvents` | the function's own log group |
| `s3:GetObject`, `s3:PutObject` | objects in the one data bucket |
| `s3:ListBucket` | the one data bucket |
| `comprehend:BatchDetectSentiment` | `*` (Comprehend has no resource-level permissions) |
| `pricing:GetProducts` | `*` (Price List API has no resource-level permissions; endpoint is us-east-1 regardless of deploy region) |
| `ssm:GetParameter` | the two (or three) exact parameter ARNs |

No `kms:Decrypt` is needed: SecureStrings use the AWS-managed `aws/ssm` key, whose key policy
permits decryption through SSM for principals in the account. The data bucket blocks public
access, denies non-TLS requests, and expires working state after 7 days. The site bucket allows
`s3:GetObject` only. The HTTP API is throttled to 10 rps / burst 20.

Checkpoints (`checkpoints/` prefix) and saves (`datasets/` prefix) use the same bucket grant; the
lifecycle rule only expires `_work/`, so paid snapshots are kept until you delete them.

If you add a service call to the code, add exactly one statement here, in the same commit.

## Database

The image includes `psycopg[binary]`. Paid X collection in Lambda requires a reachable shared
PostgreSQL database with a `postgresql+psycopg://` URL supplied through `DatabaseUrlSsmPath`.
No connection or durable ledger means no paid X request. SQLite under `/tmp` remains available
for unpaid demos/history and is ephemeral; it cannot enforce a shared paid budget.

Tables are created on first use. `create_all` does not migrate existing column types. For an
older deployment using a floating cached-price column, back up the database and migrate that
column to `numeric(12,6)`. Do not delete a database containing spend history to refresh prices.

## Running against AWS from your machine

The app uses boto3's normal credential chain, so any working `aws` CLI setup works. For SSO:

```bash
aws sso login --profile my-profile          # renew the session (expires every few hours)
```

Then set the profile in `backend/.env` alongside everything else:

```
AWS_PROFILE=my-profile
AWS_REGION=us-east-1
COMPREHEND_ENABLED=true
```

`AWS_PROFILE` is read as a setting and applied to a `boto3.Session`, not exported to the process:
this project's `.env` is parsed by pydantic-settings and never reaches boto3's own environment
lookup, so exporting it in the shell also works but is not required.

With diagnostics enabled and valid authentication, `GET /health` reports `comprehend_enabled`, and the first AWS call logs
`boto_session_created` with the profile name. When the SSO session lapses, requests fail with a
503 saying to run `aws sso login` rather than a generic error.

Least privilege for a local run is `comprehend:BatchDetectSentiment`, plus
`pricing:GetProducts` if price lookup is enabled.

## Troubleshooting

**`sam build` says "requires Docker. is Docker running?" but `docker info` works.** SAM looks for
`/var/run/docker.sock`. Docker Desktop on macOS does not create that path unless *Settings →
Advanced → Allow the default Docker socket to be used* is ticked; without it the socket lives at
`~/.docker/run/docker.sock`. `make build` detects this and sets `DOCKER_HOST` for you. To run SAM
directly, either tick that setting or export it yourself:

```bash
export DOCKER_HOST="unix://$HOME/.docker/run/docker.sock"
```

Also worth updating the CLI: 1.122 predates several Docker Desktop socket fixes.

**`uvicorn: command not found` from `make dev-api`.** Make's recipes run in `/bin/sh`, which does
not inherit an activated virtualenv. The Makefile puts `.venv/bin` first on `PATH`, so this works
without activation once `make setup` has run — if it still fails, the virtualenv is somewhere other
than `.venv/` at the repository root.

**The UI loads but every request fails.** The API is not running: `make dev` (or `make dev-api`) in a second terminal. The error notice says so explicitly rather than reporting "Internal Server Error".

## Operating

- `/health` returns operator details only when `DIAGNOSTICS` is true; it defaults to false in
  Lambda, and configured authentication is still required for operator details.

- `make logs` tails structured logs. See [logging-and-debugging.md](logging-and-debugging.md) for
  Logs Insights queries.
- Cold start is dominated by SQLAlchemy setup and NLTK import; expect 3–6 s at 2048 MB. Provisioned
  concurrency is the fix if that matters for a demo.
- Client cancellation reaches the Lambda only while the connection is open; API Gateway does not
  propagate a disconnect once the integration has started. Caps remain the hard limit.
