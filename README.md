# tj-bot

A self-hosted Telegram bot for torrent search. It queries a private [Jackett](https://github.com/Jackett/Jackett) instance across every configured indexer, caches results in PostgreSQL, and serves them as paginated, sortable, filterable cards with one-tap `.torrent` download, inline sharing, favorites — and, for admins, full remote control of a qBittorrent client, live download progress, and fleet monitoring, all inside the chat.

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
- Directional sorting (seeders / size ↓↑ / date ↓↑), category picker, and stackable filters (min seeders, size range, freshness)
- Compact list view (📋): ten results per page with number buttons jumping straight to a card
- **Instant cache** — repeat queries render from PostgreSQL with no tracker round-trip (⚡ badge), with a 🔄 refresh button for a live re-query
- Per-user search history (`/history`) with one-tap replay, and `/last` to repeat the most recent query
- **Inline mode** — `@your_bot <query>` in any chat serves cached results as shareable cards (enable via BotFather → `/setinline`)
- **Favorites** — star any result (⭐); `/favorites` keeps a snapshot that survives cache expiry, with download / send-to-server / remove actions

**Downloads**
- Download the `.torrent` file straight into the chat
- **Admin — send to server**: pick a qBittorrent category (fetched live, save path follows the category) with a free-disk-space readout and a low-space warning, then watch **live progress in the chat** with pause / resume / delete buttons
- **Admin — magnet links**: send a `magnet:` link to the bot to queue it the same way (pending magnets survive bot restarts)
- **Admin — qBittorrent console** (`/dl`): live transfer stats, filterable torrent list, per-torrent control (pause/resume, force start, queue priority, delete with or without data), and global stop/start plus alternative-speed toggle

**Access & administration**
- Open bot with guardrails: per-user rate limiting plus a quota on tracker-hitting actions; admins exempt
- User management (`/users`): block/unblock, promote runtime admins, per-user search & download activity timeline
- `/health` — uptime, database size, disk space, qBittorrent summary, and Jackett indexer health in one message
- `/broadcast` — draft-preview-confirm announcement to every active user
- **Edge-triggered alerts** — admins get notified when an indexer goes down (and when it recovers), when Jackett is unreachable, or when the qBittorrent disk runs low; deploy restarts stay silent

**Operations & safety**
- SSRF guard on tracker downloads, HTML escaping of indexer data, size caps, sanitized filenames
- Automatic PostgreSQL migrations on startup, container healthchecks, log rotation, daily database backups, CI-driven deployment
- [FlareSolverr](https://github.com/FlareSolverr/FlareSolverr) service for Cloudflare-protected trackers; transient Jackett connection failures are retried
- 200+ tests (unit + integration against real PostgreSQL), `mypy --strict`, ~88% coverage

## Architecture

```mermaid
flowchart LR
    U[Telegram user] -->|/s query · inline| B[bot · aiogram 3]
    A[Admin] -->|/dl · /health · magnet| B
    B -->|Torznab API| J[Jackett]
    J -->|scrape| T[(Trackers)]
    J -->|challenges| F[FlareSolverr]
    B <-->|cache · history · favorites| P[(PostgreSQL)]
    B -->|WebAPI| Q[qBittorrent]
    B -->|.torrent · live progress| U
```

Four containers on an internal bridge network, orchestrated by Docker Compose:

| Service        | Image                                | Role                                              |
|----------------|--------------------------------------|---------------------------------------------------|
| `bot`          | local multi-stage build              | aiogram long-polling bot, runs as a non-root user |
| `jackett`      | `linuxserver/jackett`                | meta-search proxy over torrent indexers           |
| `db`           | `postgres:16`                        | result cache, history, favorites, users           |
| `flaresolverr` | `ghcr.io/flaresolverr/flaresolverr`  | Cloudflare challenge solver for Jackett (internal only) |

qBittorrent runs separately (any host reachable from the bot). The bot downloads each `.torrent` itself and uploads the bytes to qBittorrent, so it works regardless of network topology.

## Requirements

- Docker Engine with Compose v2
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- (Optional) a qBittorrent instance with the Web UI enabled, for admin send-to-server
- (Optional) inline mode enabled via BotFather `/setinline`

## Quick start

```bash
git clone https://github.com/DarkAlioth/tj-bot.git && cd tj-bot
cp .env.example .env            # fill in the values
docker compose up -d jackett db
# open http://<host>:9117, configure indexers
# (for Cloudflare-protected trackers set FlareSolverr API URL to http://flaresolverr:8191)
docker compose up -d --build
```

## Configuration

All settings come from `.env` (never committed):

| Variable                      | Required | Default            | Description                                    |
|-------------------------------|----------|--------------------|------------------------------------------------|
| `BOT_TOKEN`                   | yes      | —                  | Telegram bot token                             |
| `ADMINS`                      | yes      | —                  | Comma-separated super-admin user IDs           |
| `POSTGRES_USER` / `_PASSWORD` / `_DB` | yes | —            | PostgreSQL credentials                         |
| `DB_HOST` / `DB_PORT`         | yes / no | — / `5432`         | PostgreSQL host and port                       |
| `SEARCH_CACHE_SECONDS`        | no       | `3600`             | Window for serving a search from cache         |
| `CACHE_TTL_DAYS`              | no       | `7`                | Age after which cached results are purged      |
| `QBIT_URL` / `_USERNAME` / `_PASSWORD` | no | —              | qBittorrent Web API; unset disables the feature |
| `QBIT_CATEGORY`               | no       | `tj-bot`           | Default category offered first in the picker   |
| `QBIT_POLL_INTERVAL_SECONDS`  | no       | `30`               | Live-progress refresh interval                 |
| `ALERT_CHECK_INTERVAL_SECONDS`| no       | `1800`             | Monitor pass interval; `0` disables alerts     |
| `ALERT_FREE_SPACE_GB`         | no       | `10`               | Low-disk threshold for qBittorrent alerts      |
| `DOWNLOAD_MAX_BYTES`          | no       | `10485760`         | Max `.torrent` size the bot will fetch         |

## Commands

| Command       | Who   | Description                                    |
|---------------|-------|------------------------------------------------|
| `/s <query>`  | all   | Search torrents                                |
| `/last`       | all   | Repeat the most recent search                  |
| `/history`    | all   | Recent searches, one-tap replay                |
| `/favorites`  | all   | Starred torrents with actions                  |
| `@bot <query>`| all   | Inline search from any chat (cached results)   |
| `/dl`         | admin | qBittorrent management console                 |
| `/health`     | admin | Service overview in one message                |
| `/stats`      | admin | Cache totals, weekly activity, top queries     |
| `/indexers`   | admin | Jackett indexer health                         |
| `/users`      | admin | Block/unblock, promote admins, activity        |
| `/broadcast`  | admin | Announcement to all active users               |

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

**Deployment** — a push to `main` runs CI and, once green, the `deploy` job on the self-hosted runner (its working directory comes from the `DEPLOY_PATH` repo secret) rebuilds and restarts the stack. Manual deploy:

```bash
cd /path/to/tj-bot && git pull && docker compose up -d --build
```

**Database backups** — `scripts/backup_db.sh` writes a gzipped `pg_dump` and prunes old copies. Schedule it with cron:

```cron
0 4 * * * /path/to/tj-bot/scripts/backup_db.sh >> ~/backups/tj-bot/backup.log 2>&1
```

**Migrations** run automatically on bot startup (Alembic). **Healthchecks** and **log rotation** are configured for every service in `docker-compose.yml`.

**Hardening checklist**
- Set a Jackett Web UI admin password (Jackett → Settings → Admin password); the API stays key-protected regardless.
- Restrict the Jackett port (`9117`) to trusted networks or bind it to localhost if the UI is not needed remotely.
- Rotate the bot token and database password if they were ever shared.
- If you fork this with a self-hosted runner, require approval for workflow runs from outside collaborators (Settings → Actions).

## License

MIT — see [LICENSE](LICENSE).
