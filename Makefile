# Task runner for the whole repository.
#
# Targets are grouped by intent and named for what a person wants to do, not for the tool that
# does it: `make dev`, `make check`, `make deploy`. Backend targets run inside backend/ and
# frontend targets inside frontend/, so neither half needs to know where the other lives.
#
# Recipes run in /bin/sh, which does not inherit an activated virtualenv. Putting .venv/bin first
# on PATH means every target works whether or not the shell was activated, and is harmless when no
# .venv exists. PATH is colon-separated, so a checkout path containing spaces needs no quoting.

STAGE ?= dev
CONFIG_ENV ?= default
export PATH := $(CURDIR)/.venv/bin:$(PATH)

# The preview bundle is built like a deployed one: an explicit API origin rather than the dev
# server's proxy, so what is audited behaves exactly like what ships.
LOCAL_API_URL ?= http://localhost:8000

STACK_DIR := $(CURDIR)/infrastructure/stack_request

# SAM takes these per subcommand, not globally: `sam build --template-file …`, never
# `sam --template-file … build`. Both paths are absolute because SAM resolves --config-file
# against its own notion of the project root, and quoted because a checkout can live under a
# path containing spaces.
SAM_OPTS := --template-file "$(STACK_DIR)/template.yaml" \
            --config-file "$(STACK_DIR)/samconfig.toml" --config-env $(CONFIG_ENV)
# `sam build` writes .aws-sam/build/template.yaml and deploy must read that, not the source
# template, or it ships the unbuilt version. So deploy gets the config but not --template-file.
SAM_DEPLOY_OPTS := --config-file "$(STACK_DIR)/samconfig.toml" --config-env $(CONFIG_ENV)

# SAM looks for /var/run/docker.sock. Docker Desktop on macOS does not create it unless "Allow the
# default Docker socket to be used" is enabled, so `sam build` reports "is Docker running?" even
# when `docker info` works. Point DOCKER_HOST at the per-user socket when the default is absent.
DOCKER_SOCK := $(HOME)/.docker/run/docker.sock
DOCKER_ENV = $(shell test ! -S /var/run/docker.sock && test -S "$(DOCKER_SOCK)" \
             && echo DOCKER_HOST=unix://$(DOCKER_SOCK))

.DEFAULT_GOAL := help
.PHONY: help setup dev dev-api dev-web preview check lint test test-backend test-web audit-a11y \
        api-types validate build deploy deploy-web put-secret logs clean

##@ Getting started

help: ## Show this list
	@awk 'BEGIN {FS = ":.*## "} \
	     /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5); next } \
	     /^[a-z][a-z0-9-]*:.*## / { printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2 }' \
	     $(MAKEFILE_LIST)
	@echo

setup: ## Install everything: backend deps, NLTK corpora, frontend packages, backend/.env
	@test -f backend/.env || (cp backend/.env.example backend/.env && echo "created backend/.env; add your X_BEARER_TOKEN")
	cd backend && pip install -e ".[dev]"
	cd backend && SSL_CERT_FILE=$$(python -m certifi) python -m nltk.downloader wordnet omw-1.4 averaged_perceptron_tagger_eng
	cd frontend && npm install

##@ Running locally

dev: ## Run the API and the UI together (Ctrl-C stops both)
	@command -v uvicorn >/dev/null || { echo "uvicorn not found. Run 'make setup' first."; exit 1; }
	@echo "  API  http://localhost:8000/docs"
	@echo "  UI   http://localhost:5173"
	@echo
	@# `exec` in each subshell means the recorded pid is the server itself, not a wrapper, so the
	@# trap actually stops it. `wait` keeps make in the foreground until both exit.
	@trap 'kill $$api $$web 2>/dev/null' INT TERM; \
	  ( cd backend && exec uvicorn sentiment_prep.api.app:app --reload --port 8000 --app-dir src ) & api=$$!; \
	  ( cd frontend && exec npx vite ) & web=$$!; \
	  wait $$api $$web

dev-api: ## Run only the API on :8000 (Swagger at /docs)
	@command -v uvicorn >/dev/null || { echo "uvicorn not found. Run 'make setup' first."; exit 1; }
	cd backend && uvicorn sentiment_prep.api.app:app --reload --port 8000 --app-dir src

dev-web: ## Run only the UI on :5173 (proxies /api to the backend)
	cd frontend && npm run dev

preview: ## Build and serve the production bundle on :5173, as it will ship (audit-a11y checks this)
	cd frontend && VITE_API_URL=$(LOCAL_API_URL) npm run build && npx vite preview --port 5173

##@ Checking your work

check: lint test ## Everything CI would run: lint, types and both test suites

lint: ## ruff + mypy --strict (backend); eslint + tsc (frontend)
	cd backend && ruff check src tests && ruff format --check src tests && mypy src
	ruff check --config backend/pyproject.toml tools && ruff format --check --config backend/pyproject.toml tools
	cd frontend && npm run typecheck && npm run lint

test: test-backend test-web ## Both test suites; no AWS credentials needed

test-backend: ## pytest (use 'pytest -m "not slow"' to skip wall-clock assertions)
	cd backend && pytest -q

test-web: ## vitest
	cd frontend && npm test

audit-a11y: ## axe over every stage in both themes; run 'make dev-api' and 'make preview' first
	python tools/a11y_audit.py

##@ Keeping the two halves in step

api-types: ## Regenerate the frontend's API types and Zod schemas from the running backend
	cd frontend && npm run gen:api

##@ Deploying to AWS

validate: ## Lint the CloudFormation template (cheap; run before build)
	sam validate $(SAM_OPTS) --lint

build: ## Build the Lambda container image (needs Docker running)
	@$(DOCKER_ENV) sam build $(SAM_OPTS)

deploy: build ## Deploy the API stack from the artefacts in .aws-sam/build
	@$(DOCKER_ENV) sam deploy $(SAM_DEPLOY_OPTS)

deploy-web: ## Build the UI against the deployed API and upload it to the site bucket
	$(eval API_URL := $(shell aws cloudformation describe-stacks --stack-name sentiment-prep-$(STAGE) --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" --output text))
	$(eval SITE_BUCKET := $(shell aws cloudformation describe-stacks --stack-name sentiment-prep-$(STAGE) --query "Stacks[0].Outputs[?OutputKey=='SiteBucketName'].OutputValue" --output text))
	$(eval API_KEY := $(shell aws ssm get-parameter --name /sentiment-prep/$(STAGE)/api-key --with-decryption --query Parameter.Value --output text 2>/dev/null))
	cd frontend && VITE_API_URL=$(API_URL) VITE_API_KEY=$(API_KEY) npm run build
	aws s3 sync frontend/dist s3://$(SITE_BUCKET) --delete

put-secret: ## Store a SecureString: make put-secret NAME=api-key VALUE=... [STAGE=dev]
	aws ssm put-parameter --name /sentiment-prep/$(STAGE)/$(NAME) --type SecureString --overwrite --value "$(VALUE)"

logs: ## Tail the deployed Lambda's structured logs
	sam logs --stack-name sentiment-prep-$(STAGE) --tail

##@ Housekeeping

clean: ## Remove build output, caches and the local database
	rm -rf .aws-sam frontend/dist backend/data
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf backend/.pytest_cache backend/.mypy_cache backend/.ruff_cache .ruff_cache
