.PHONY: install install-phase1 install-phase2 install-all test test-unit test-hardware lint doctor serve clean help

# ---------------------------------------------------------------------------
# Installation
# ---------------------------------------------------------------------------

install: install-network  ## Install network extras (default)

install-network:  ## Install network extras — cellular routing + SMS
	pip install -e ".[network,dev]"

install-automation:  ## Install automation extras — adds Appium client + vision deps
	pip install -e ".[automation,dev]"

install-all:  ## Install all extras including HTTP server
	pip install -e ".[automation,serve,dev]"

# ---------------------------------------------------------------------------
# Testing
# ---------------------------------------------------------------------------

test:  ## Run offline unit tests (no device, no VLM required)
	pytest -m "not hardware and not vlm" -v

test-unit: test  ## Alias for test

test-hardware:  ## Run hardware tests (requires USB-connected iPhone)
	pytest -m hardware -v

test-vlm:  ## Run VLM tests (requires Ollama running)
	pytest -m vlm -v

test-all:  ## Run everything including skipped tests
	pytest -v

# ---------------------------------------------------------------------------
# Development tools
# ---------------------------------------------------------------------------

doctor:  ## Check all prerequisites and system dependencies
	ifarm doctor

doctor-json:  ## Machine-readable diagnostics (JSON to stdout)
	ifarm doctor --json

serve:  ## Start the iFarm HTTP server on port 7420
	ifarm serve --port 7420

lint:  ## Run ruff linter
	ruff check ifarm/ tests/

lint-fix:  ## Run ruff with auto-fix
	ruff check --fix ifarm/ tests/

format:  ## Run ruff formatter
	ruff format ifarm/ tests/

# ---------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------

clean:  ## Remove build artifacts and caches
	rm -rf build/ dist/ *.egg-info/ .pytest_cache/ .ruff_cache/ .mypy_cache/
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -name "*.pyc" -delete

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*##"}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'
