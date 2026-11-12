# console — qBittorrent console widget (admin, `/dl`) · protocol v1

Widget: `widgets/console/widget.html` (8 274 B raw, **4 052 B zlib-9**, limit 4 096). Sent with
`allow_queries=True` in the private chat only (background `bg` ticks need it). Caption always set.

## chat_data (≤ 1024 bytes UTF-8) — the full view state

```json
{"v":1,"r":12,"t":1794502627,"tab":"all","pg":0,"np":3,
 "cnt":{"all":14,"dl":3,"up":9,"st":2,"er":0},
 "tr":{"d":15500000,"u":4030000,"dd":13207024435,"ud":4831838208,"fs":1330409069608,"ft":4002222325104,"alt":0,"cs":1},
 "rows":[["8f3a1c2b","Дюна: Часть вторая / Dune: Part Two…","d",0.42,12400000,5400,0.31], …]}
```

| key | type | meaning |
|---|---|---|
| `v` | 1 | schema version (state only) |
| `r` | int | revision, +1 on every `editMessageWidgetData`; the widget ignores states/answers with `r` lower than what it already has |
| `t` | unix s | snapshot time; drawn as «обновлено HH:MM:SS»; the meta turns destructive when `now − t > 20 s` (and the widget sets `t = 0` itself when a `bg` request is rejected) |
| `tab` | `all` `dl` `up` `st` `er` | active tab (the bot filters: dl = `d*`, up = `u*`, st = `p`/`c`, er = `e*`) |
| `pg`, `np` | int | 0-based page, number of pages (6 rows per page) |
| `cnt` | {all,dl,up,st,er} | counts for the tab chips; the «Ошибка» chip is drawn only when `er > 0`; `all = 0` → «Нет активных задач» |
| `tr` | object | `d`/`u` speeds B/s, `dd`/`ud` session totals B (`dl_info_data`/`up_info_data`), `fs` free bytes on the save disk, `ft` total bytes of that disk (`shutil.disk_usage(save_path).total`; 0 = unknown → bar hidden), `alt` 0/1 alt-speed mode, `cs` 1 = connected (`connection_status` connected/firewalled), 0 = disconnected (red dot) |
| `rows` | list | the current page, **positional**: `[h, n, s, p, v, e, ra]` |
| `err` | string | present instead of `cnt/tr/rows` when qBittorrent is unreachable → the widget draws the unavailable view with «Повторить» (`rf`) |

Row `[h, n, s, p, v, e, ra]`:

- `h` first 8 hex of the torrent hash (used in every request);
- `n` name, **≤ 36 characters** (cut to 35 + «…»); escape nothing, the widget escapes `<` and `&`;
- `s` state code (2 chars max): first char = kind `d` downloading, `u` seeding, `p` stopped incomplete,
  `c` stopped complete, `e` error; optional second char = variant `s` stalled, `q` queued, `k` checking,
  `f` forced, `m` metadata, `x` missing files (only `ex`). Mapping from qBittorrent `state`:
  `downloading→d`, `forcedDL→df`, `stalledDL→ds`, `queuedDL→dq`, `checkingDL→dk`, `metaDL→dm`,
  `allocating→dk`, `uploading→u`, `forcedUP→uf`, `stalledUP→us`, `queuedUP→uq`, `checkingUP→uk`,
  `moving→uk`, `stoppedDL/pausedDL→p`, `stoppedUP/pausedUP→c`, `error→e`, `missingFiles→ex`.
  The widget derives icon (arrow / pause), hue (accent = active download, green = seeding/complete,
  amber = any `d?` variant i.e. waiting, hint = paused, destructive = error) and the right column:
  `d*` → «↓ speed» + ETA, `u*` → «↑ speed» + «×ratio», `c` → «—» + «×ratio», `p`/`e*` → «—» + «42%»;
- `p` progress 0–1 (3 decimals), `v` relevant speed B/s (dlspeed for `d*`, upspeed otherwise),
  `e` ETA seconds, **−1 for unknown/∞** (qBittorrent 8640000 must be mapped to −1), `ra` ratio (2 decimals).

**Byte budget** (measured): header with 6-digit `r`, 5 × 3-digit counts, 14-digit totals ≈ 215 B;
a row with a 36-char Cyrillic name and max digits = 129 B → 6 rows **990 B**. With 40-char names it
is 1 038 B (over), so: name cap 36, and after serializing the bot must assert `len ≤ 1024` and shorten
names to 30 if not (never happens with the caps above, but it is one line of defense).

## Requests (1–64 B ASCII) and what the bot does

Every answer is JSON ≤ 1024 B. Three shapes: **snapshot** (same keys as `chat_data` minus `v`,
replaces the whole view), **patch** (`{r,t,cnt,tr,row,x}` — merged into the view), `{"err":"…"}`.
Rejections are shown as «Ошибка связи» (TIMEOUT → «Бот не ответил»); for `bg` silently (stale meta).

| request | bot action | answer | edits chat_data? |
|---|---|---|---|
| `bg` | sent once when the widget appears and every 5.2 s while visible; read `torrents/info`, `transfer/info`, `speedLimitsMode`, free space → build the snapshot for the **stored** `tab`/`pg` | snapshot, or `{"r","t","err":"qBittorrent недоступен"}` when the client fails | **no** (ticks never edit; the widget keeps the state in memory) |
| `rf` | same as `bg` (tap on «Повторить») | snapshot | yes if the view changed |
| `t:<tab>` | switch tab, page 0 | snapshot | yes |
| `pg:<n>` | go to page n (clamp to 0…np−1; the widget may send −1 or np) | snapshot | yes |
| `x:<h>` | expand: `torrents/info?hashes=` → details | `{"x":{"h":"<8hex>","l":"<line>","sp":"<path>"}}` | no |
| `pa:<h>` | `torrents/stop` | patch (`row` = fresh row, `x` = fresh details) | yes (`rows` of the current page + `cnt`) |
| `re:<h>` | `torrents/start` | patch | yes |
| `fo:<h>` | `setForceStart` with `not torrent["force_start"]` (toggle) | patch | yes |
| `pr:<h>:top\|up\|down\|bottom` | `topPrio` / `increasePrio` / `decreasePrio` / `bottomPrio`; on `QueueingDisabledError` → `{"err":"Очередь отключена в настройках qBittorrent"}` | patch | yes |
| `del:<h>:0\|1` | `torrents/delete` (1 = with data); the widget shows an inline confirm before sending | snapshot of the same tab/page (row gone) | yes |
| `fi:<h>` | send the **file picker widget** as a new message for this torrent | `{"ok":1}` | no |
| `alt` | `toggleSpeedLimitsMode` | snapshot (`tr.alt` flipped) | yes |
| `sa` | `torrents/stop hashes=all` (the widget asks «Все на паузу? Да/Нет» first) | snapshot | yes |
| `ra` | `torrents/start hashes=all` | snapshot | yes |

Details `x.l` is a **preformatted** one-line string built by the bot (the widget wraps it):
`<state label> · <size> · ↓ <downloaded> · ↑ <uploaded> · ×<ratio> · сиды <num_seeds> (<num_complete>) · пиры <num_leechs> (<num_incomplete>) · <category> · <added dd.mm.yyyy>`,
state labels from `services/formatting.STATE_VIEW` (without the emoji); use NBSP between numbers and
units (`12,3 ГБ`). `x.sp` = `save_path`, cut to 60 chars. Worst case `{"x":…}` = 370 B, a patch
answer = 707 B. The widget caches details per hash and asks `x:<h>` on every expand (fresh data).

Rules the widget relies on:
- `h` in `row`/`x` must be the first 8 hex of the hash exactly as in `rows` (lookup key).
- After an action the bot answers **first** (`answerWidgetQuery`, ≤ 15 s), then edits `chat_data`
  with the same `r` it put in the answer; the widget applies both idempotently (`r` monotonic).
- `bg`/`rf`/`t:`/`pg:` answering `{"err"}` → the unavailable view; action verbs answering `{"err"}`
  → a destructive toast only (the view stays).
- One request in flight: a tap while a `bg` tick is pending is refused by the client with BUSY; the
  widget does not queue, the user taps again. Keep `bg` handling fast (one `torrents/info` call, cached
  disk usage) — it is on the critical path of every tap.
- `cnt.all` must be the total over all tabs (`len(torrents)`), not the page.

## Caption (fallback for clients without widgets)

`qBittorrent · ↓ {dl} · ↑ {up} · {n} торрентов ({dl_n} загружаются)` — e.g.
`qBittorrent · ↓ 15,5 МБ/с · ↑ 4,0 МБ/с · 14 торрентов (3 загружаются)`; when unreachable:
`qBittorrent недоступен`; empty: `qBittorrent · нет задач`.

## Notes for the handler author (Python)

- Keep today's `DL_STATES/UP_STATES/STOP_STATES` sets; add the state-code map above (one dict).
- Build the snapshot with `json.dumps(..., ensure_ascii=False, separators=(",", ":"))`; round
  `p` to 3 and `ra` to 2 decimals (floats like `0.42` serialize short), ints for bytes.
- Free disk: `free_space()` already exists (`sync/maindata`); total: `shutil.disk_usage(path).total`
  of the default save path, cached for 60 s; both 0 when unknown.
- `t` = `int(time.time())` of the snapshot; the widget compares it with the phone clock (20 s window),
  so the server clock must be sane (NTP).
- Treat `h` from requests as untrusted: validate `^[0-9a-f]{8}$`, resolve to the full hash by prefix
  among `torrents/info`; unknown → `{"err":"Торрент не найден"}` plus a snapshot on the next tick.
- The widget sends `pg:-1` / `pg:np` at the ends (no disabled arrows to save bytes): clamp silently.
