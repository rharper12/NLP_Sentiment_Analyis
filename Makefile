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
.PHONY: help setup lock dev stop dev-api dev-web preview check lint test test-backend test-web audit-a11y aws-check aws-login \
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
	python -m pip install --require-hashes -r backend/requirements-dev.txt
	python -m pip install --no-deps --no-build-isolation -e "backend[dev]"
	python -m pip check
	cd backend && SSL_CERT_FILE=$$(python -m certifi) python -m nltk.downloader wordnet omw-1.4 averaged_perceptron_tagger_eng
	cd frontend && npm ci

lock: ## Regenerate Python locks; set LOCK_FLAGS=--upgrade for a deliberate dependency update
	uv pip compile backend/pyproject.toml --universal --python-version 3.12 --generate-hashes --no-annotate --custom-compile-command 'make lock' $(LOCK_FLAGS) -o backend/requirements.txt > /dev/null
	uv pip compile backend/pyproject.toml --extra dev --universal --python-version 3.12 --constraint backend/requirements.txt --generate-hashes --no-annotate --custom-compile-command 'make lock' $(LOCK_FLAGS) -o backend/requirements-dev.txt > /dev/null

##@ Running locally

dev: ## Run the API and the UI together (Ctrl-C stops both)
	@command -v uvicorn >/dev/null || { echo "uvicorn not found. Run 'make setup' first."; exit 1; }
	@echo "  API  http://localhost:8000/docs"
	@echo "  UI   http://localhost:5173"
	@echo
	@# `exec` in each subshell means the recorded pid is the server itself, not a wrapper, so the
	@# trap actually stops it. `wait` keeps make in the foreground until both exit.
	@trap 'kill $$api $$web 2>/dev/null || true' EXIT; \
	  trap 'exit 130' INT; trap 'exit 143' TERM; \
	  ( cd backend && exec uvicorn sentiment_prep.api.app:app --reload --port 8000 --app-dir src ) & api=$$!; \
	  ( cd frontend && exec ./node_modules/.bin/vite ) & web=$$!; \
	  wait $$api $$web

stop: ## Stop this project's local servers on every port (including Vite fallback ports)
	@python tools/stop_dev.py

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

aws-check: ## Verify AWS credentials the way the app resolves them; PROBE=1 also calls Comprehend
	python tools/aws_check.py

aws-login: ## Renew SSO using AWS_PROFILE from backend/.env (opens AWS sign-in)
	python tools/aws_check.py --login

audit-a11y: ## General and consumer browser checks; use an isolated API and production preview
	python tools/a11y_audit.py

##@ Keeping the two halves in step

api-types: ## Regenerate frontend API types; update Zod validators separately when fields change
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
	cd frontend && VITE_API_URL=$(API_URL) npm run build
	aws s3 sync frontend/dist s3://$(SITE_BUCKET) --delete

put-secret: ## Store a SecureString: make put-secret NAME=api-key VALUE=... [STAGE=dev]
	@aws ssm put-parameter --name /sentiment-prep/$(STAGE)/$(NAME) --type SecureString --overwrite --value "$(VALUE)"

logs: ## Tail the deployed Lambda's structured logs
	sam logs --stack-name sentiment-prep-$(STAGE) --tail

##@ Housekeeping

clean: ## Remove build output, caches and the local database
	rm -rf .aws-sam frontend/dist backend/data
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf backend/.pytest_cache backend/.mypy_cache backend/.ruff_cache .ruff_cache
