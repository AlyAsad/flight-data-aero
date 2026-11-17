# Aero: run locally with `make install` then `make dev` (or `make start`).
FRONTEND := frontend
HOST     := 127.0.0.1
# `env -u VIRTUAL_ENV` stops an unrelated activated venv from confusing uv.
UV       := env -u VIRTUAL_ENV uv
UVICORN  := $(UV) run uvicorn app.main:app --app-dir backend --host $(HOST) --port 8000

ifeq ($(shell command -v uv 2>/dev/null),)
$(error uv not found on PATH. Install it with `curl -LsSf https://astral.sh/uv/install.sh | sh` , then open a new terminal)
endif
ARGS     ?=

.DEFAULT_GOAL := help
.PHONY: help install dev backend frontend build start test test-backend test-frontend lint format smoke clean

help: ## Show available targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## Create venv, install Python and Node dependencies
	@test -f .env || cp .env.example .env
	$(UV) sync
	cd $(FRONTEND) && npm install

dev: ## Run backend (:8000) and frontend dev server (:5173) with reload
	@trap 'kill 0' INT TERM EXIT; \
	$(MAKE) --no-print-directory backend & \
	$(MAKE) --no-print-directory frontend & \
	( sleep 4; (xdg-open http://localhost:5173 || open http://localhost:5173 || true) >/dev/null 2>&1 ) & \
	wait

backend: ## Run only the API server with reload
	$(UVICORN) --reload --reload-dir backend

frontend: ## Run only the Vite dev server
	cd $(FRONTEND) && npm run dev -- --host $(HOST)

build: ## Build the frontend into frontend/dist
	cd $(FRONTEND) && npm run build

start: build ## Build the frontend and serve everything from FastAPI on :8000
	@( sleep 3; (xdg-open http://localhost:8000 || open http://localhost:8000 || true) >/dev/null 2>&1 ) &
	$(UVICORN)

test: test-backend test-frontend ## Run all tests

test-backend: ## Run pytest
	$(UV) run pytest

test-frontend: ## Run Vitest
	cd $(FRONTEND) && npm test -- --run

lint: ## Lint backend (ruff) and frontend (tsc + eslint)
	$(UV) run ruff check backend
	$(UV) run ruff format --check backend
	cd $(FRONTEND) && npm run lint

format: ## Auto-format backend code
	$(UV) run ruff format backend
	$(UV) run ruff check --fix backend

smoke: ## One live NCL→KHI search per provider (ARGS=--record saves responses)
	cd backend && $(UV) run python -m scripts.smoke $(ARGS)

clean: ## Remove build artefacts and caches (keeps data/)
	rm -rf $(FRONTEND)/dist .pytest_cache .ruff_cache
