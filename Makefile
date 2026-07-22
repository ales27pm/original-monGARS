.PHONY: sync format lint typecheck test check migrate compose-check ci-local

sync:
	uv sync --frozen --extra dev --extra documents

format:
	uv run ruff format .
	uv run ruff check --fix .

lint:
	uv run ruff format --check .
	uv run ruff check .

typecheck:
	uv run mypy src

test:
	uv run pytest -q

check: lint typecheck test compose-check

migrate:
	uv run alembic upgrade head

compose-check:
	docker compose config --quiet

ci-local:
	bash scripts/ci-local.sh
