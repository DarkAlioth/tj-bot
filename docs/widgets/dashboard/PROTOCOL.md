# dashboard — protocol (admin only; merges /health, /stats, /indexers)

## chat_data (JSON, ≤ 1024 B UTF-8)

```json
{"v":1,"r":12,"t":1794487200,"a":"h",
 "h":{"up":312840,"df":[41000000000,53000000000],"db":123400000,
      "q":{"a":2,"n":14,"d":1250000,"u":234000,"f":[1230000000000,4000000000000]},
      "m":1794487200,"sc":{"p":1240,"q":12,"e":3}},
 "s":{"d7":[12,30,22,45,8,19,33],"ev":169,"us":5,"tc":4213,"qc":812,
      "top":[["дюна часть вторая",14],["оппенгеймер",9]]},
 "i":[["RuTracker",1204],["RuTor",0,"Login failed: captcha required"],["MegaPeer",118]]}
```

| key | meaning |
|---|---|
| `v` | schema version, always 1 |
| `r` | revision, +1 on every `editMessageWidgetData`; answers carry it too; the widget ignores anything with `r` < newest seen |
| `t` | unix seconds of the snapshot (shown as «обновлено 12:40») |
| `a` | start tab for a freshly created widget: `h` Состояние / `s` Статистика / `i` Индексеры (`/health` → `h`, `/stats` → `s`, `/indexers` → `i`). Tab switching is local, no request |
| `h.up` | uptime, seconds |
| `h.df` | bot host disk `[free, total]` bytes (`shutil.disk_usage("/")`) — bar = used share |
| `h.db` | `repo.db_size()` bytes |
| `h.q` | qBittorrent summary `{a: active (DOWNLOADING_STATES), n: total, d: dl B/s, u: up B/s, f:[free,total] bytes}`; `null` → «недоступен» (also when not configured) |
| `h.m` | unix seconds of the last completed monitor pass («проверено 12:40») |
| `h.sc` | optional enrichment scraper `{p: posters cached, q: queue length, e: errors last 24 h}`; omit → tile hidden |
| `s.d7` | 7 ints, searches per day, oldest first, last = today (weekday labels are computed from `t`) |
| `s.ev`, `s.us` | searches / distinct users in the 7-day window (`get_stats`) |
| `s.tc`, `s.qc` | torrents cached, result sets cached |
| `s.top` | ≤ 8 × `[text ≤ 24 chars, count]`, sorted desc (raise the `limit(5)` in `get_stats` to 8) |
| `i` | indexer rows `[name ≤ 14, results]` (ok) or `[name, 0, error ≤ 40 chars]` (down); order: as Jackett returns (or errors first) |

Byte budget (measured with `json.dumps(separators=(',',':'), ensure_ascii=False)`):
- typical (1 indexer down, 8 Russian top queries of ~17 chars): **802 B**;
- worst at the caps (7 down × 40-char errors + 8 × 24 Cyrillic chars): **1184 B — over**;
- `fit()` on the bot side, in order, until ≤ 1024: (1) errors → 24 chars (→ 1086), (2) drop `top` rows from the end down to 5 (→ 912). Both caps hold in practice: 2+ indexers down at once is rare and Jackett error texts are English (1 byte/char).

## Requests (`TelegramWidget.request`, ASCII ≤ 64 B)

| verb | when | bot must |
|---|---|---|
| `bg` | once when the widget appears, then every 5 s while visible (`activated`/`deactivated`), and on a tap on «обновлено …» / «обновить» | answer within ~1 s from **cached** data: `{"r":<same r>,"t":<now>,"h":{…}}` (+ `"i":[…]` only when the cached indexer list changed). No `editMessageWidgetData` for ticks. `h` = uptime, `disk_usage`, `db_size` (cache 60 s), 3 qBittorrent calls (cache 5 s; on `QbittorrentError` → `"q":null`), `m` + `i` from the monitor's last pass, `sc` from the scraper stats |
| `chk` | «Проверить сейчас» on the Индексеры tab | answer **immediately** `{"ok":1,"pending":1}`; `asyncio.create_task(...)`: `await jackett.indexers()` (up to ~30 s), update the monitor cache (`known` problems — reuse `check_once`), then `editMessageWidgetData(chat_data with r+1, new i, h.m=now, t=now)`. On `JackettError` edit anyway with every row `[name,0,"Jackett недоступен"]`, or answer `{"err":"Jackett недоступен"}` if it fails before answering. The widget shows «Проверяю… до 30 с», clears on `chatDataChanged` with a newer `r`, and after 45 s without it shows «Не дождался ответа» + «Повторить» |
| `q:<i>` | tap on a top-queries row (`i` = index into `s.top` of the state the bot stored) | run that search for the admin exactly like a typed query (new search-card message); answer `{"ok":1}` → toast «Отправлено», or `{"err":"…"}` |

`t:<tab>` is **not** sent — tabs are local. One request in flight at a time (the widget skips a tick while a tap request is pending and shows «Подождите…» on a tap during a tick).

## Answers

- `bg`: `{"r":12,"t":…,"h":{…}}` (optionally `"i"`, `"s"`); ≤ 1024 B (measured 217 B); stale (`r` lower than seen) ignored.
- `chk`: `{"ok":1,"pending":1}`; errors `{"err":"<≤ 40 chars>"}`.
- `q:<i>`: `{"ok":1}` or `{"err":"…"}`.
- Rejections mapped in the widget: NOT_ALLOWED → «Откройте чат с ботом», BUSY → «Подождите…», RATE_LIMITED → «Слишком часто», TIMEOUT → «Бот не ответил», FAILED / INVALID_ANSWER → «Ошибка связи». Background-tick failures are silent; after 90 s without a successful tick the header turns into «обновить» (destructive color, tap = manual `bg`); NOT_ALLOWED on a tick stops the timer.

## Caption (fallback, sent with the widget; keep it above or below — no dependency)

`🩺 Панель: аптайм {up} · диск {free} · qBittorrent {active}/{total} ({dl}↓ {up}↑) · индексеры {ok}/{n}` and on a second line `📊 7 дней: {ev} поисков, {us} пользователей`.

## Handler notes (Python)

- Keep the authoritative state per message (`message_id` → dict) or rebuild it from the repo on every request; the widget never trusts its own copy for actions.
- `bg` is answered 12×/min per open dashboard: never call Jackett there; the monitor loop (`monitor.py`) should store its last `indexers()` result + timestamp in a small shared object (e.g. `MonitorState` with `indexers`, `checked_at`), which both `bg` and `chk` read.
- `chk` must not block the answer: answer first, probe in a task, edit after (Telegram answers must come within 15 s; the probe takes up to 30 s). Rate-limit `chk` to one concurrent probe per bot (reuse the running task if another admin taps).
- `q:<i>`: validate `0 ≤ i < len(top)`; the search goes through the normal search path for `from.id` (admin).
- Error texts in `i[*][2]`: strip to ≤ 40 chars (the widget shows them in one line, tap expands).
