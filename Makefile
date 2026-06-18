.PHONY: dev test lint lint-frontend lint-all test-all install build compose help deploy deploy-backend deploy-frontend check-schema

CONTAINER ?= hygie

help:
	@echo "Usage: make <target>"
	@echo ""
	@echo "  dev              Start dev server (uvicorn --reload on :8000)"
	@echo "  test             Run test suite"
	@echo "  lint             Run ruff linter"
	@echo "  install          Install Python dependencies"
	@echo "  build            Build Docker image (hygie:dev)"
	@echo "  compose          Start via docker compose"
	@echo "  deploy           Build frontend + deploy all files to container + health-check"
	@echo "  deploy-backend   Copy backend .py files to container + restart"
	@echo "  deploy-frontend  Build frontend + copy dist to container"
	@echo "  check-schema     Validate OpenAPI schema (scripts/check-schema.py)"
	@echo "  lint-frontend    Run ESLint on frontend/vue"
	@echo "  lint-all         Run lint + lint-frontend"
	@echo "  test-all         Run test + frontend unit tests"

install:
	pip install -r requirements.txt -r requirements-dev.txt

dev:
	uvicorn backend.main:app --reload --port 8000

test:
	python3 -m pytest -q

lint:
	python3 -m ruff check backend/ tests/

lint-frontend:
	cd frontend/vue && npm run lint

lint-all: lint lint-frontend

test-all: test
	cd frontend/vue && npm run test:unit

build:
	docker build -t hygie:dev .

compose:
	docker compose up -d

deploy:
	@bash scripts/deploy.sh $(CONTAINER)

deploy-backend:
	@for dir in backend/routers backend/arr_clients backend/scanner backend/db backend/rules; do \
		for f in $$dir/*.py; do \
			[ -f "$$f" ] || continue; \
			docker cp "$$f" "$(CONTAINER):/app/$$f" 2>/dev/null || true; \
		done; \
	done
	@for f in backend/*.py; do \
		[ -f "$$f" ] || continue; \
		docker cp "$$f" "$(CONTAINER):/app/$$f" 2>/dev/null || true; \
	done
	@docker exec -u root $(CONTAINER) find /app/backend -name "*.pyc" -delete 2>/dev/null || true
	@docker restart $(CONTAINER)
	@echo "✅ Backend deployed"

deploy-frontend:
	@bash scripts/build-frontend.sh
	@docker cp frontend/dist/. $(CONTAINER):/app/frontend/dist/
	@echo "✅ Frontend deployed"

check-schema:
	@python3 scripts/check-schema.py
