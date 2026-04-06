# GameDev monorepo — common tasks for Python packages (ruff, pytest, mypy), Rust (Materialize), and VibeGame (Bun).
# Requires GNU Make; on Windows, use Git Bash / MSYS2 / WSL so shell recipes and `find` work as expected.

PYTHON_PROJECTS := Shared Text2D Text3D Paint3D Part3D GameAssets Texture2D Text2Sound GameDevLab

.DEFAULT_GOAL := help

.PHONY: help lint fmt fmt-check test test-shared test-text2d test-text3d test-paint3d test-part3d test-gameassets test-texture2d test-text2sound test-gamedevlab test-materialize test-rust test-vibegame check-vibegame lint-vibegame fmt-vibegame fmt-check-vibegame build-vibegame clean typecheck check install-hooks

# Prefer .venv only if pytest is installed there; else python3, then python.
define run-pytest
	cd $(1) && if [ -f .venv/Scripts/python.exe ] && .venv/Scripts/python.exe -c "import pytest" 2>/dev/null; then .venv/Scripts/python.exe -m pytest; elif [ -f .venv/bin/python ] && .venv/bin/python -c "import pytest" 2>/dev/null; then .venv/bin/python -m pytest; elif command -v python3 >/dev/null 2>&1; then python3 -m pytest; else python -m pytest; fi
endef

define run-mypy-shared
	cd Shared && if [ -f .venv/Scripts/python.exe ]; then .venv/Scripts/python.exe -m mypy src --ignore-missing-imports; elif [ -f .venv/bin/python ]; then .venv/bin/python -m mypy src --ignore-missing-imports; elif command -v python3 >/dev/null 2>&1; then python3 -m mypy src --ignore-missing-imports; else python -m mypy src --ignore-missing-imports; fi
endef

help: ## List all targets (default)
	@echo "GameDev monorepo targets:"
	@echo ""
	@echo "Full Python/Rust CI (no VibeGame): make check"
	@echo "VibeGame (Bun): make test-vibegame | make check-vibegame | make lint-vibegame | make build-vibegame"
	@echo ""
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-22s %s\n", $$1, $$2}'

lint: ## ruff check at repo root; cargo clippy in Materialize/
	ruff check .
	cd Materialize && cargo clippy

fmt: ## ruff format at repo root; cargo fmt in Materialize/
	ruff format .
	cd Materialize && cargo fmt

fmt-check: ## ruff format --check; cargo fmt --check in Materialize/
	ruff format --check .
	cd Materialize && cargo fmt --check

test: ## pytest in each Python project; cargo test in Materialize/
	@for d in $(PYTHON_PROJECTS); do \
		echo "==> pytest $$d"; \
		cd "$(CURDIR)/$$d" || exit 1; \
		if [ -f .venv/Scripts/python.exe ] && .venv/Scripts/python.exe -c "import pytest" 2>/dev/null; then .venv/Scripts/python.exe -m pytest; elif [ -f .venv/bin/python ] && .venv/bin/python -c "import pytest" 2>/dev/null; then .venv/bin/python -m pytest; elif command -v python3 >/dev/null 2>&1; then python3 -m pytest; else python -m pytest; fi || exit 1; \
		cd "$(CURDIR)"; \
	done
	cd Materialize && cargo test

test-shared: ## pytest only in Shared/
	$(call run-pytest,Shared)

test-text2d: ## pytest only in Text2D/
	$(call run-pytest,Text2D)

test-text3d: ## pytest only in Text3D/
	$(call run-pytest,Text3D)

test-paint3d: ## pytest only in Paint3D/
	$(call run-pytest,Paint3D)

test-part3d: ## pytest only in Part3D/
	$(call run-pytest,Part3D)

test-gameassets: ## pytest only in GameAssets/
	$(call run-pytest,GameAssets)

test-texture2d: ## pytest only in Texture2D/
	$(call run-pytest,Texture2D)

test-text2sound: ## pytest only in Text2Sound/
	$(call run-pytest,Text2Sound)

test-gamedevlab: ## pytest only in GameDevLab/
	$(call run-pytest,GameDevLab)

test-materialize: ## cargo test in Materialize/
	cd Materialize && cargo test

test-rust: test-materialize ## alias for test-materialize

# VibeGame: requires Bun (https://bun.sh/) on PATH. Not part of `make test` / `make check`.
test-vibegame: ## bun install (frozen) + bun test in VibeGame/
	cd VibeGame && bun install --frozen-lockfile && bun test tests/unit tests/integration tests/e2e

check-vibegame: ## tsc --noEmit in VibeGame/
	cd VibeGame && bun install --frozen-lockfile && bun run check

lint-vibegame: ## eslint in VibeGame/
	cd VibeGame && bun install --frozen-lockfile && bun run lint

fmt-vibegame: ## prettier --write in VibeGame/
	cd VibeGame && bun install --frozen-lockfile && bun run format

fmt-check-vibegame: ## prettier --check in VibeGame/
	cd VibeGame && bun install --frozen-lockfile && bun run format:check

build-vibegame: ## vite build in VibeGame/
	cd VibeGame && bun install --frozen-lockfile && bun run build

clean: ## Remove __pycache__, caches, build/, dist/, *.egg-info under the repo
	@find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; true
	@find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null; true
	@find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null; true
	@find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null; true
	@find . -type d \( -name build -o -name dist \) -exec rm -rf {} + 2>/dev/null; true
	@find . -type d -name '*.egg-info' -exec rm -rf {} + 2>/dev/null; true

typecheck: ## mypy on Shared/src (--ignore-missing-imports)
	$(call run-mypy-shared)

check: lint fmt-check typecheck test ## lint + fmt-check + typecheck + test (full CI)

install-hooks: ## pre-commit install
	pre-commit install
