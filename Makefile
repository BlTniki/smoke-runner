.PHONY: sync check format test

sync:
	uv sync --locked

check:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy src
	uv run pytest

format:
	uv run ruff check --fix .
	uv run ruff format .

test:
	uv run pytest

