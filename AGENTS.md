# Agent Instructions

## Overview
This repository uses Python 3.11+ with dependencies managed by `uv` and a Docker-based workflow for running the bot in containers.

## Local setup
- Ensure Python 3.11+ is installed.
- Install `uv` if needed: https://docs.astral.sh/uv/

### Install dependencies
```bash
make install
```

### Install dev dependencies
```bash
make dev-install
```

### Run the bot
```bash
make run
```

### Lint/format/test
```bash
make format
make lint
make test
```

## Docker workflow
```bash
docker compose up --build
```

## Configuration
- Default configuration lives in `config.yaml`.
- You can override values with environment variables like `TELEGRAM_TOKEN`, `ADMIN_USERNAMES`, and `DATABASE_URL`.
