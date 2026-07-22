# Security Policy

## Reporting a vulnerability

This is a personal, self-hosted project. If you find a security issue, please open a
private security advisory on GitHub or contact the maintainer directly rather than
filing a public issue.

## Security model

- **Secrets** live only in `.env` (git-ignored) and are injected as environment
  variables; nothing sensitive is baked into the image or committed.
- **Runtime data** — the PostgreSQL volume and the Jackett config (which holds the
  API key and tracker credentials) — is git-ignored and excluded from the Docker
  build context.
- **Container** runs as a non-root user from a multi-stage image with no build
  toolchain in the final layer.
- **Input handling**: all user-supplied text is rendered through HTML escaping; every
  database query is parameterized; outbound HTTP parameters are percent-encoded.
- **Resource limits**: torrent downloads are size-capped and streamed; the open bot
  is rate-limited per user; in-memory state is bounded.
- **Privilege separation**: server-side actions (send-to-server, the qBittorrent
  console, stats, indexer health) are gated to configured admin IDs and re-checked in
  every handler, so replayed callbacks from non-admins are rejected.
- **Dependencies** are pinned via `uv.lock` and audited on every push with
  `pip-audit`; static analysis runs via `bandit`.

## Operational recommendations

- Set a Jackett Web UI admin password and keep port `9117` on a trusted network.
- Take regular database backups (`scripts/backup_db.sh`).
- Rotate the bot token and database credentials if they are ever exposed.
