# Event Telegram Bot

A modernised Telegram bot that manages registrations for events, with a lightweight admin panel to approve attendees, schedule announcements and send urgent notifications.

## Features

- Attendee registration with automatic waitlisting when the configured capacity is reached.
- Separate lecturer and project showcase registration categories with no capacity limits.
- Admin panel protected by HTTP Basic authentication:
  - Approve or reject attendees, lecturers and showcase presenters.
  - Manually add priority attendees that should bypass the capacity limit.
  - Schedule broadcast posts that are sent to every subscribed user.
  - Send urgent notifications to all participants instantly.
- Periodic scheduler that delivers planned posts automatically.
- SQLite-backed persistence by default (configurable database URL) so data survives redeployments.

## Configuration

Runtime configuration is stored in `config.yaml`. Update this file (or mount an alternative and point `CONFIG_FILE` to it) with your Telegram token, admin credentials and other limits before starting the bot.

You can still override any value via environment variables (`TELEGRAM_TOKEN`, `ADMIN_IDS`, etc.) for secrets that should not live in the YAML file.

## Getting started

1. Install [uv](https://github.com/astral-sh/uv) (already bundled in the Docker image). With uv available locally you can bootstrap the project with the included Makefile:

   ```bash
   make dev-install
   ```

2. Run the bot:

   ```bash
   make run
   ```

   The Telegram bot will start polling and the admin interface will be available at `http://localhost:8000/admin/posts`.

3. Format, lint and test:

   ```bash
   make format
   make lint
   make test
   ```

To create a lockfile for reproducible builds run `uv lock` and adjust the Dockerfile to copy it into the image.

## Docker workflow

The repository includes a `Dockerfile` and `docker-compose.yml` that rely on uv for dependency management.

```bash
docker compose up --build
```

By default the compose file mounts `config.yaml` into the container. Provide your production configuration before deploying.

## Deployment

- The project uses uv and a `config.yaml` file for configuration, making it easy to deploy with Docker or any container orchestrator.
- Data lives in the configured SQL database. When redeploying, point `DATABASE_URL` to the same storage (for SQLite mount the volume, for Postgres/MySQL use the external service) to retain registrations and scheduled posts.

## Project structure

```
app/
  config.py          # Settings and environment loading
  database.py        # SQLAlchemy engine & session helpers
  main.py            # Entry point that runs the bot and the admin server
  models.py          # ORM models
  scheduler.py       # APScheduler integration
  services/          # Business logic for registrations, posts and messaging
  telegram/          # Telegram bot application & handlers
  web/               # FastAPI admin interface
config.yaml          # Runtime configuration loaded by default
pyproject.toml       # Project metadata and dependency declarations
Dockerfile           # uv-based container build
Makefile             # Developer shortcuts (install, lint, test, run)
docker-compose.yml   # Local container orchestration
```

## Admin credentials and security

Set strong values for `ADMIN_USERNAME` and `ADMIN_PASSWORD`. For production deployments, run the admin panel behind HTTPS (for example via a reverse proxy or the hosting provider) to protect credentials in transit.

## Development tips

- Use SQLite locally (default) and Postgres/MySQL in production by changing `DATABASE_URL`.
- Adjust the scheduler frequency with `SCHEDULER_INTERVAL_SECONDS` (defaults to 60 seconds).
- To reset the database, delete the SQLite file (`anonchatbot.db`) or drop the tables in your SQL server.
