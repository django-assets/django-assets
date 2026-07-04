# Canonical command surface (Process ADR-0011). CI invokes these same targets.

.PHONY: up down run test test-create-db ci-test lint format typecheck check build clean

up:
	docker compose up -d postgres

down:
	docker compose down

run:
	uv run python manage.py runserver

test:
	uv run pytest

test-create-db:
	uv run pytest --create-db

ci-test:
	uv run pytest --create-db -n auto --cov=django_assets --cov-report=term-missing --tb=short -q

lint:
	uv run ruff format --check .
	uv run ruff check .
	@! grep -rn --include='*.py' -E ': float|-> float|float\(' django_assets/ \
		| grep -v '# float-ok' || (echo 'float ban violated (PADR-0006)' && exit 1)
	uv run python scripts/check_import_direction.py

format:
	uv run ruff format .
	uv run ruff check --fix .

typecheck:
	uv run mypy

check: lint typecheck test

build:
	uv build

clean:
	docker compose down -v --remove-orphans 2>/dev/null || true
	rm -rf dist .pytest_cache .mypy_cache .ruff_cache
