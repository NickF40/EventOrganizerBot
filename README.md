# Event Telegram Bot

A Telegram bot for event registrations with an admin toolset to review applications and send announcements.

## What it does

- Collects attendee registrations and waitlists when capacity is full.
- Supports additional registration categories (e.g., lecturers, showcases).
- Lets admins approve/reject applicants and send scheduled or urgent broadcasts.
- Persists data in a SQL database (SQLite by default).

## Quick start

```bash
make docker-build 
make docker-up
```

The bot will start polling. Admin commands are available to Telegram users listed in the config.

## Configuration

Default config lives in `config.yaml` (or point `CONFIG_FILE` to another file):

- `telegram_token`: bot token
- `admin_usernames`: list of Telegram usernames allowed to use admin commands
- `database_url`: SQLAlchemy database URL

You can override any value with environment variables like `TELEGRAM_TOKEN`, `ADMIN_USERNAMES`, and `DATABASE_URL`.

## Docker

```bash
docker compose up --build
```

Mount your `config.yaml` into the container or set env vars for production.

## Development

```bash
make format FIX=1
make lint
make docker-test
```
