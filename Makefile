# Task runner for the whole repository. Backend targets run inside backend/, frontend targets
# inside frontend/, so neither needs to know where the other lives.

STAGE ?= dev
CONFIG_ENV ?= default
# SAM takes these per subcommand, not globally: `sam build --template-file …`, never
# `sam --template-file … build`.
#
# Both paths are absolute and quoted. Absolute because SAM resolves --config-file against its own
# notion of the project root rather than the shell's working directory, and a relative path fails
# with "does not exist or could not be read". Quoted because a checkout can live under a path
# containing spaces, which would otherwise split into two arguments.
STACK_DIR := $(CURDIR)/infrastructure/stack_request
SAM_OPTS := --template-file "$(STACK_DIR)/template.yaml" \
            --config-file "$(STACK_DIR)/samconfig.toml" --config-env $(CONFIG_ENV)
# `sam build` writes .aws-sam/build/template.yaml; deploy must read that, not the source template,
# or it ships the unbuilt version. So deploy gets the config but not --template-file.
SAM_DEPLOY_OPTS := --config-file "$(STACK_DIR)/samconfig.toml" --config-env $(CONFIG_ENV)

.PHONY: help setup lint test gen-api local-api frontend build validate deploy deploy-site put-secret logs audit

help:
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  %-12s %s\n", $$1, $$2}'

setup: ## Create .env, install backend and frontend dependencies, download NLTK corpora
	@test -f backend/.env || (cp backend/.env.example backend/.env && echo "created backend/.env; add your X_BEARER_TOKEN")
	cd backend && pip install -e ".[dev]"
	cd backend && SSL_CERT_FILE=$$(python -m certifi) python -m nltk.downloader wordnet omw-1.4 averaged_perceptron_tagger_eng
	cd frontend && npm install

# tools/ is repo-level tooling with no manifest of its own, so it is linted against the single
# ruff configuration in backend/pyproject.toml rather than a second copy of the rules.
RUFF_CONFIG := backend/pyproject.toml

lint: ## ruff + mypy --strict (backend); eslint + tsc (frontend)
	cd backend && ruff check src tests && ruff format --check src tests && mypy src
	ruff check --config $(RUFF_CONFIG) tools && ruff format --check --config $(RUFF_CONFIG) tools
	cd frontend && npm run typecheck && npm run lint

test: ## pytest (backend) and vitest (frontend); no AWS credentials needed
	cd backend && pytest -q
	cd frontend && npm test

audit: ## Accessibility audit against a running API and built UI (see docs/design-system.md)
	python tools/a11y_audit.py

gen-api: ## Regenerate the frontend's API types and Zod schemas from the running backend
	cd frontend && npm run gen:api

local-api: ## API on :8000 (Swagger at /docs)
	cd backend && uvicorn sentiment_prep.api.app:app --reload --port 8000 --app-dir src

frontend: ## Vite dev server on :5173 (proxies /api to :8000)
	cd frontend && npm run dev

validate: ## Lint the CloudFormation template
	sam validate $(SAM_OPTS) --lint

build: ## Build the Lambda container image
	sam build $(SAM_OPTS)

deploy: build ## Deploy the API stack
	sam deploy $(SAM_DEPLOY_OPTS)

deploy-site: ## Build the UI against the deployed API and upload it to the site bucket
	$(eval API_URL := $(shell aws cloudformation describe-stacks --stack-name sentiment-prep-$(STAGE) --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" --output text))
	$(eval SITE_BUCKET := $(shell aws cloudformation describe-stacks --stack-name sentiment-prep-$(STAGE) --query "Stacks[0].Outputs[?OutputKey=='SiteBucketName'].OutputValue" --output text))
	$(eval API_KEY := $(shell aws ssm get-parameter --name /sentiment-prep/$(STAGE)/api-key --with-decryption --query Parameter.Value --output text 2>/dev/null))
	cd frontend && VITE_API_URL=$(API_URL) VITE_API_KEY=$(API_KEY) npm run build
	aws s3 sync frontend/dist s3://$(SITE_BUCKET) --delete

put-secret: ## Store a SecureString: make put-secret NAME=api-key VALUE=... [STAGE=dev]
	aws ssm put-parameter --name /sentiment-prep/$(STAGE)/$(NAME) --type SecureString --overwrite --value "$(VALUE)"

logs: ## Tail Lambda logs with structured fields
	sam logs --stack-name sentiment-prep-$(STAGE) --tail
