.PHONY: dev test lint install build compose help

help:
	@echo "Usage: make <target>"
	@echo ""
	@echo "  dev       Start dev server (uvicorn --reload on :8000)"
	@echo "  test      Run test suite"
	@echo "  lint      Run ruff linter"
	@echo "  install   Install Python dependencies"
	@echo "  build     Build Docker image (hygie:dev)"
	@echo "  compose   Start via docker compose"

install:
	pip install -r requirements.txt -r requirements-dev.txt

dev:
	uvicorn backend.main:app --reload --port 8000

test:
	python3 -m pytest -q

lint:
	python3 -m ruff check backend/ tests/

build:
	docker build -t hygie:dev .

compose:
	docker compose up -d
