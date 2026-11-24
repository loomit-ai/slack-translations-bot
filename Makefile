.DEFAULT_GOAL := help
UV ?= uv
IMAGE ?= slack-translator:dev

.PHONY: help install run dev test lint format typecheck imports guards check clean image fake-translator translate-one translate-file translation-page

help: ## Show this list
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "} {printf "  %-16s %s\n", $$1, $$2}'

install: ## Install locked dependencies, dev tools included
	$(UV) sync --frozen

run: ## Run the bot in Socket Mode with the secrets in .env
	$(UV) run --env-file .env python -m slack_translator

dev: install run ## Install, then run

test: ## Unit and integration tests (live tests excluded)
	$(UV) run pytest -m "not live"

lint: ## Lint and check formatting
	$(UV) run ruff check .
	$(UV) run ruff format --check .

format: ## Apply formatting and safe lint fixes
	$(UV) run ruff check --fix .
	$(UV) run ruff format .

typecheck: ## Type check with pyright
	$(UV) run pyright

imports: ## FF-01: layer order and where third-party libraries may be imported
	$(UV) run lint-imports

guards: ## FF-02: nothing under src/ can post publicly with the bot token
	$(UV) run python scripts/check_no_public_posting.py src

check: lint typecheck imports guards test ## Everything CI would run

clean: ## Remove caches and build output
	rm -rf .pytest_cache .ruff_cache .import_linter_cache dist build
	find . -name __pycache__ -type d -prune -not -path './.venv/*' -exec rm -rf {} +

image: ## Build the container image
	docker build -t $(IMAGE) .

fake-translator: ## Run the fake translator endpoint (TS-02) for manual fault tests
	$(UV) run python -m tests.support.fake_translator

translate-one: ## Try one message with the model in ENV=lab/local.env (TEXT=, TARGET=, DIAGNOSE=1)
	@test -n "$(ENV)" || { echo "ENV=lab/local.env is required (see README, Try translations outside Slack)"; exit 2; }
	$(UV) run python scripts/translate_one.py --env "$(ENV)" $(if $(TARGET),--target "$(TARGET)") $(if $(TEXT),--text "$(TEXT)") $(if $(DIAGNOSE),--diagnose)

translate-file: ## Try a file of messages: ENV=lab/local.env FILE=lab/sample.txt (TARGET=, OUT=)
	@test -n "$(ENV)" -a -n "$(FILE)" || { echo "ENV=lab/local.env and FILE=lab/sample.txt are required"; exit 2; }
	$(UV) run python scripts/translate_file.py --env "$(ENV)" "$(FILE)" $(if $(TARGET),--target "$(TARGET)") $(if $(OUT),--out "$(OUT)")

translation-page: ## Try translations in the browser, on 127.0.0.1 only: ENV=lab/local.env (PORT=8765)
	@test -n "$(ENV)" || { echo "ENV=lab/local.env is required (see README, Try translations outside Slack)"; exit 2; }
	$(UV) run python scripts/translation_page.py --env "$(ENV)" $(if $(PORT),--port "$(PORT)")
