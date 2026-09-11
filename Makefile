# Common tasks. Run `make` with no target to list them.

STAGE ?= dev
CONFIG_ENV ?= default
SAM := sam --template-file infrastructure/stack_request/template.yaml \
           --config-file infrastructure/stack_request/samconfig.toml --config-env $(CONFIG_ENV)

.PHONY: help setup lint test gen-api local-api frontend build validate deploy deploy-site put-secret logs

help:
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  %-12s %s\n", $$1, $$2}'

setup: ## Create .env, install Python deps, NLTK corpora, frontend packages
	@test -f .env || (cp .env.example .env && echo "created .env; add your X_BEARER_TOKEN")
	pip install -e ".[dev]"
	SSL_CERT_FILE=$$(python -m certifi) python -m nltk.downloader wordnet omw-1.4 averaged_perceptron_tagger_eng
	cd frontend && npm install

lint: ## ruff + mypy --strict + eslint + tsc
	ruff check src tests tools
	ruff format --check src tests tools
	mypy src
	cd frontend && npm run typecheck && npm run lint

test: ## Backend and frontend unit tests (no AWS credentials needed)
	pytest -q
	cd frontend && npm test

gen-api: ## Regenerate frontend API types from the running backend's OpenAPI schema
	cd frontend && npm run gen:api

local-api: ## API on :8000 (Swagger at /docs)
	uvicorn sentiment_prep.api.app:app --reload --port 8000 --app-dir src

frontend: ## Vite dev server on :5173 (proxies /api to :8000)
	cd frontend && npm run dev

validate: ## Lint the CloudFormation template
	$(SAM) validate --lint

build: ## Build the Lambda container image
	$(SAM) build

deploy: build ## Deploy the API stack
	$(SAM) deploy

deploy-site: ## Build the UI against the deployed API and upload it to the site bucket
	$(eval API_URL := $(shell aws cloudformation describe-stacks --stack-name sentiment-prep-$(STAGE) --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" --output text))
	$(eval SITE_BUCKET := $(shell aws cloudformation describe-stacks --stack-name sentiment-prep-$(STAGE) --query "Stacks[0].Outputs[?OutputKey=='SiteBucketName'].OutputValue" --output text))
	cd frontend && VITE_API_URL=$(API_URL) npm run build
	aws s3 sync frontend/dist s3://$(SITE_BUCKET) --delete

put-secret: ## Store a SecureString: make put-secret NAME=x-bearer-token VALUE=... [STAGE=dev]
	aws ssm put-parameter --name /sentiment-prep/$(STAGE)/$(NAME) --type SecureString --overwrite --value "$(VALUE)"

logs: ## Tail Lambda logs with structured fields
	sam logs --stack-name sentiment-prep-$(STAGE) --tail
