# progress — protocol (bot ↔ widget)

One widget message per server download, sent by the bot right after the picker's `go` (replaces
today's edited text message + `watch_download`). The widget refreshes itself with background
requests; the bot edits `chat_data` only on real state changes.

## chat_data snapshot (≤ 1024 B, typically ≈ 400 B)

```json
{"v":1,"r":3,"t":1762943405,"h":"3f9a1c2b",
 "n":"Дюна: Часть вторая / Dune: Part Two (2024) UHD BDRemux 2160p | 4K | HDR10 | Dolby Vision | D, P, A",
 "st":"downloading","pr":0.372,"sz":64906854400,"dn":24145349836,
 "ds":12400000,"us":1200000,"eta":3290,"se":12,"pe":48,"ra":0.12,
 "fc":1,"ft":1,"ad":1762939805,"fo":0,
 "sp":[8100000,9300000,10200000,11000000,12400000,11800000,12400000]}
```

| key | source (`torrents/info`) | notes |
|-----|--------------------------|-------|
| `v`, `r` | — | schema 1; revision, +1 per `editMessageWidgetData` |
| `t` | — | unix seconds of the snapshot; the widget shows «нет связи» when `now − t > 20 s` (omit `t` in **answers**: the widget stamps receipt time) |
| `h` | `hash[:8]` | informational only |
| `n` | `name` ≤ 120 chars | clamped to 2 lines |
| `st` | `state` | any qBittorrent state, plus `gone` (torrent no longer listed) |
| `pr` | `progress` 0–1, 3 decimals | `pr ≥ 1` = done (ring full, check mark, celebration once) |
| `sz`, `dn` | `size`, `downloaded` bytes | `sz = 0` while `metaDL` → ring shows «—», ETA «—» |
| `ds`, `us` | `dlspeed`, `upspeed` B/s | |
| `eta` | `eta` seconds | `8640000` or `< 0` → «—» |
| `se`, `pe` | `num_seeds`, `num_leechs` | tile «Сиды · пиры» |
| `ra` | `ratio` 2 decimals | |
| `fc`, `ft` | selected / total files (`torrents/files`: priority > 0) | `ft = 0` → «—» |
| `ad` | `added_on` | tile «Добавлен dd.mm hh:mm» |
| `fo` | `force_start` or `state == forcedDL` | «Форсировать» chip accent when 1 |
| `sp` | last ≤ 12 `dlspeed` samples | optional, chat_data only: seeds the sparkline of a re-created widget |
| `e` | str ≤ 120 | optional free text (error detail) — reserved, not drawn in this build |

The widget keeps its own 12-point speed history in memory (`sp` seeds it; every answer with `ds`
appends a point).

## Requests

| request | when | bot action | answer |
|---------|------|-----------|--------|
| `bg` | once at script start (goes out when the widget appears), then every 5.5 s while `isActive`; never two in flight; skipped while a tap request is pending | `torrent_info(hash)` (+ `torrent_files` only when `fc/ft` unknown) → snapshot; no `chat_data` edit | snapshot (no `t`), or `{"err":"…"}` → widget marks stale silently |
| `rf` | tap on the «обновлено …» line (stale or not) | same as `bg` | snapshot |
| `pa` | «Пауза» / «Остановить» (seeding) | `stop_torrents(hash)` | snapshot after the call; then edit chat_data |
| `re` | «Возобновить» / «Раздавать» | `start_torrents(hash)` | snapshot; edit chat_data |
| `fo` | «Форсировать» toggle | `set_force_start(hash, not fo)` | snapshot with `fo`; edit chat_data |
| `del:0` | confirm «Только из клиента» | `delete_torrents(hash, delete_files=False)` | `{"r":…,"st":"gone"}`; edit chat_data to `st:"gone"` (keep `n`) |
| `del:1` | confirm «С файлами» | `delete_torrents(hash, delete_files=True)` | same |
| `rc` | «Повторить проверку» (error / missingFiles) | `torrents/recheck` (add `recheck_torrents(hashes)` to `QbittorrentClient`: `POST /api/v2/torrents/recheck`) then `start_torrents` | snapshot (`checkingDL`) |
| `con` | «Консоль» | send the qBittorrent console widget as a new message | `{"ok":1}` |

Answers are applied at once (`Object.assign` over the state, count-up of the percent, 400 ms ring
and bar transitions). `r` in an answer must be ≥ the newest `r` seen or the answer is ignored;
omitting `r` is fine for `bg`/`rf`. `{"err":"<Russian>"}` on a tap → toast + haptic error.

When the bot notices completion (any poll: `pr ≥ 1`), it edits chat_data once (so a re-created
widget starts complete) — there is no separate "done" message any more; the caption can be edited to
«✅ Загрузка завершена: …». `gone` likewise when the torrent disappears.

## State → UI map

| `st` | label | primary | second button |
|------|-------|---------|---------------|
| downloading, forcedDL, stalledDL, queuedDL, checkingDL, metaDL | `STATE_VIEW` words («Загружается», «Ожидает сидов», «Получение метаданных», «Принудительная загрузка», «В очереди на загрузку», «Проверка») | «Пауза» → `pa` | «Форсировать» → `fo` |
| stoppedDL, pausedDL | «На паузе» | «Возобновить» → `re` | «Форсировать» |
| `pr ≥ 1` (uploading, stalledUP, forcedUP, queuedUP, checkingUP) | «Готово · 60,4 ГБ · раздаётся» | «Остановить» → `pa` | — |
| stoppedUP, pausedUP | «Готово · 60,4 ГБ · остановлено» | «Раздавать» → `re` | — |
| error, missingFiles | «Ошибка» / «Файлы не найдены» (destructive ring and label) | «Повторить проверку» → `rc` | — |
| gone | «Удалено из qBittorrent» | no actions, tiles hidden | |

Second row always: «Удалить» (inline confirm: «Удалить?» · Отмена / «Только из клиента» / «С файлами»)
and «Консоль». Footer line: «обновлено 12:40:05 ↻» (tap = `rf`); «нет связи · …» in destructive
when the last tick failed or the snapshot is older than 20 s.

## Caption template

`⬇️ <b>{name}</b> — {pct} %, {speed}, осталось {eta}` edited by the bot only on state changes
(start, pause/resume, done, error, deleted), never per tick (no flood limits).

## Notes for the Python handler author

- Keep `(chat_id, message_id) → (hash, tag)` in the same `widget_messages` table as the picker; the
  tag lookup (`torrents_by_tag`) is a fallback when the hash changed (never, but today's `Dlc` used
  the tag).
- A `bg` request is indistinguishable from a tap: answer within 15 s, do not edit chat_data for it.
  Requests are limited to 5 per 5 s per user; the widget sends ≤ 1 per 5.5 s plus taps.
- Every snapshot comes from one `torrents/info?hashes=` call; `fc/ft` may be cached from the picker.
- `watch_download` and its asyncio task set become unnecessary; the only server-side timer left is
  optional: a slow poll (60 s) that edits chat_data on completion/removal for widgets nobody is
  looking at.
