.PHONY: install dev-install format lint test run docker-build docker-up docker-down

install:
	uv sync --no-dev

dev-install:
	uv sync

format:
	uv run ruff format .

lint:
	uv run ruff check .

test:
	uv run pytest

run:
	uv run python -m app.main

docker-build:
	docker build -t anonchatbot:latest .

docker-up:
	docker compose up --build

docker-down:
	docker compose down

docker-test:
	docker compose run --rm app uv run pytest
