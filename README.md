# tj-bot

A self-hosted Telegram bot for torrent search. It queries a private [Jackett](https://github.com/Jackett/Jackett) instance across every configured indexer, caches results in PostgreSQL, and serves them as paginated, sortable cards with category filtering, one-tap `.torrent` download, saved-search subscriptions, and — for the admin — full remote control of a qBittorrent client, all inside the chat.

<p align="center">
  <a href="#features">Features</a> ·
  <a href="#architecture">Architecture</a> ·
  <a href="#quick-start">Quick start</a> ·
  <a href="#configuration">Configuration</a> ·
  <a href="#commands">Commands</a> ·
  <a href="#development">Development</a> ·
  <a href="#operations">Operations</a>
</p>

---

## Features

**Search**
- Full-text search across all Jackett indexers: `/s <query>`
- Paginated result cards — title, uploader, description, seeders/peers, size, category, tracker, publish date
- Sort by seeders, size, or publish date; filter by category
- **Instant cache** — repeat queries render from PostgreSQL with no tracker round-trip (⚡ badge), with a 🔄 refresh button for a live re-query
- Per-user search history (`/history`) with one-tap replay

**Subscriptions**
- Subscribe to a query with the 🔔 button; a scheduled sweep notifies you about new uploads
- Manage subscriptions with `/subs`

**Downloads**
- Download the `.torrent` file straight into the chat
- **Admin — send to server**: hand a torrent to qBittorrent, which downloads it directly; the bot notifies you on completion
- **Admin — qBittorrent console** (`/dl`): live transfer stats, filterable torrent list, per-torrent control (pause/resume, force start, queue priority, delete with or without data), and global stop/start plus alternative-speed toggle

**Operations & safety**
- Per-user rate limiting for the open bot; admins exempt
- Automatic PostgreSQL migrations on startup, container healthchecks, log rotation, daily database backups, and CI-driven deployment

## Architecture

```mermaid
flowchart LR
    U[Telegram user] -->|/s query| B[bot · aiogram 3]
    A[Admin] -->|/dl · send to server| B
    B -->|Torznab API| J[Jackett]
    J -->|scrape| T[(Trackers)]
    B <-->|cache · history · subs| P[(PostgreSQL)]
    B -->|WebAPI| Q[qBittorrent]
    B -->|.torrent · notices| U
```

Three containers on an internal bridge network, orchestrated by Docker Compose:

| Service   | Image                     | Role                                            |
|-----------|---------------------------|-------------------------------------------------|
| `bot`     | local multi-stage build   | aiogram long-polling bot, runs as a non-root user |
| `jackett` | `linuxserver/jackett`     | meta-search proxy over torrent indexers         |
| `db`      | `postgres:16`             | result cache, search history, subscriptions     |

qBittorrent runs separately (any host reachable from the bot). The bot downloads each `.torrent` itself and uploads the bytes to qBittorrent, so it works regardless of network topology.

## Requirements

- Docker Engine with Compose v2
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- (Optional) a qBittorrent instance with the Web UI enabled, for admin send-to-server

## Quick start

```bash
git clone https://github.com/DarkAlioth/tj-bot.git && cd tj-bot
cp .env.example .env            # fill in the values
docker compose up -d jackett db
# open http://<host>:9117, configure indexers, then:
docker compose up -d --build bot
```

## Configuration

All settings come from `.env` (never committed):

| Variable                      | Required | Default            | Description                                    |
|-------------------------------|----------|--------------------|------------------------------------------------|
| `BOT_TOKEN`                   | yes      | —                  | Telegram bot token                             |
| `ADMINS`                      | yes      | —                  | Comma-separated admin user IDs                 |
| `POSTGRES_USER` / `_PASSWORD` / `_DB` | yes | —            | PostgreSQL credentials                         |
| `DB_HOST` / `DB_PORT`         | yes / no | — / `5432`         | PostgreSQL host and port                       |
| `SEARCH_CACHE_SECONDS`        | no       | `3600`             | Window for serving a search from cache         |
| `CACHE_TTL_DAYS`              | no       | `7`                | Age after which cached results are purged      |
| `SUBSCRIPTIONS_PER_USER`      | no       | `3`                | Per-user subscription limit (admins exempt)    |
| `SUBSCRIPTIONS_CHECK_SECONDS` | no       | `21600`            | Subscription sweep interval                    |
| `QBIT_URL` / `_USERNAME` / `_PASSWORD` | no | —              | qBittorrent Web API; unset disables the feature |
| `QBIT_CATEGORY`               | no       | `tj-bot`           | Category applied to sent torrents              |
| `DOWNLOAD_MAX_BYTES`          | no       | `10485760`         | Max `.torrent` size the bot will fetch         |

## Commands

| Command      | Who   | Description                                 |
|--------------|-------|---------------------------------------------|
| `/s <query>` | all   | Search torrents                             |
| `/history`   | all   | Recent searches, one-tap replay             |
| `/subs`      | all   | Manage saved-search subscriptions           |
| `/dl`        | admin | qBittorrent management console              |
| `/stats`     | admin | Cache totals, weekly activity, top queries  |
| `/indexers`  | admin | Jackett indexer health                      |

## Development

Tooling: [uv](https://docs.astral.sh/uv/), ruff, mypy (strict), pytest, bandit, pip-audit. Python 3.13+.

```bash
uv sync                        # install runtime + dev dependencies
uv run pytest                  # unit + integration tests (80%+ coverage gate)
uv run ruff check . && uv run ruff format --check .
uv run mypy                    # strict type checking
uv run bandit -c pyproject.toml -r . -ll
uv run pip-audit               # dependency vulnerability audit
```

Integration tests need PostgreSQL; point `TEST_DATABASE_URL` at a throwaway database:

```bash
docker run -d --rm --name tj_test_pg -e POSTGRES_USER=test -e POSTGRES_PASSWORD=test \
  -e POSTGRES_DB=test -p 127.0.0.1:55432:5432 postgres:17-alpine
TEST_DATABASE_URL="postgresql+asyncpg://test:test@127.0.0.1:55432/test" uv run pytest
```

### Contributing

`main` accepts changes only through pull requests with green CI (`quality` / `tests` / `docker`). A merge to `main` triggers an automatic deployment via a self-hosted runner.

## Operations

**Deployment** — a push to `main` runs CI and, once green, the `deploy` job on the self-hosted runner rebuilds and restarts the stack. Manual deploy:

```bash
cd ~/projects/tj-bot && git pull && docker compose up -d --build
```

**Database backups** — `scripts/backup_db.sh` writes a gzipped `pg_dump` and prunes old copies. Schedule it with cron:

```cron
0 4 * * * /home/sony/projects/tj-bot/scripts/backup_db.sh >> ~/backups/tj-bot/backup.log 2>&1
```

**Migrations** run automatically on bot startup (Alembic). **Healthchecks** and **log rotation** are configured for every service in `docker-compose.yml`.

**Hardening checklist**
- Set a Jackett Web UI admin password (Jackett → Settings → Admin password); the API stays key-protected regardless.
- Restrict the Jackett port (`9117`) to trusted networks or bind it to localhost if the UI is not needed remotely.
- Rotate the bot token and database password if they were ever shared.

## License

MIT — see [LICENSE](LICENSE).
