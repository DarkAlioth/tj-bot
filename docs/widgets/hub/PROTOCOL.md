# hub — protocol (bot ↔ widget)

Replaces `/start`, `/history`, `/favorites`. One widget with tabs «Главная» / «История» /
«Избранное». The bot sends it on `/start` (and on `/history`, `/favorites` with the matching
`tb`). All rows are rendered from `chat_data`; the widget never formats numbers/dates itself —
the bot ships display strings (see "why" in NOTES.md: the 4096-byte budget).

## chat_data (JSON, ≤ 1024 bytes UTF-8)

```json
{"v":1,"r":7,"tb":"m","adm":1,"st":{"j":1,"q":0,"w":"12:40"},
 "fn":7,"fu":2,
 "h":[{"i":1041,"q":"дюна часть вторая","a":3,"n":57}],
 "ff":"a","fp":0,
 "f":[{"i":501,"n":"Дюна: Часть вторая / Dune: Part Two (2024) UHD BDRemux 2160p",
       "t":"RuTracker","m":"58,4 ГБ · 02.11.2026 · ⇡1 240","u":1}]}
```

| key  | type | meaning |
|------|------|---------|
| `v`  | 1 | schema version |
| `r`  | int | revision, +1 on every `editMessageWidgetData`; the widget ignores states/answers with `r` < newest seen |
| `tb` | `m`\|`h`\|`f` | current tab; **only this tab's rows are in the state** |
| `adm`| 1 | present for admins: shortcuts row + «На сервер» button |
| `st` | obj | optional status for the main tab: `j` Jackett ok (0/1), `q` qBittorrent ok (0/1), `w` snapshot time string («12:40») |
| `fn` | int | favorites total (always present — the main tile and the «Все» chip) |
| `fu` | int | favorites changed on the tracker since saved (always present) |
| `h`  | list | history rows: `tb=m` → ≤ 5, `tb=h` → ≤ 10 (see budget rule) |
| `h[].i` | int | `search_queries.id` → sent back as `h:<i>` |
| `h[].q` | str | query text, **≤ 40 chars** (cut at 39 + «…», as today's `/history`) |
| `h[].a` | int \| str | age: int = minutes ago when < 60 (widget prints «3 мин назад» / «только что»); str = preformatted «HH:MM» (same day) or «DD.MM» (older) |
| `h[].n` | int | result_count of that search |
| `ff` | `a`\|`u` | favorites filter: all / updated (`tb=f` only) |
| `fp` | int | favorites page index, 0-based, **4 rows per page** |
| `f`  | list | ≤ 4 favorite rows of the current filter+page |
| `f[].i` | int | `favorites.id` → `fo:/fd:/fs:/fr:` |
| `f[].n` | str | title, **≤ 60 chars and ≤ 90 bytes UTF-8** (cut + «…»); html-unescape before cutting (`favorite_line` does this today) |
| `f[].t` | str | tracker display name ≤ 12 chars («RuTracker», «NoNaMe Club», …) |
| `f[].m` | str | preformatted meta: `f"{size} · {published:%d.%m.%Y} · ⇡{seeders:n}"`, size via `format_size` with the Russian units («58,4 ГБ», «312 МБ»), thin spaces in thousands |
| `f[].u` | 1 | changed on the tracker since saved (needs a flag, see backend notes) |

### Byte budget (measured with `gen/hub_mock.py`)

| state | bytes |
|-------|-------|
| main, 5 queries × 40 Cyrillic chars, admin + status | 686 |
| history, 10 × 20 Cyrillic chars | 886 |
| history, 10 × 40 Cyrillic chars (worst) | **1286 → over** |
| favorites, 4 × (60 chars / 90 bytes title) + meta, admin + status | 810 (hard worst 987 with the longest meta) |

Rule for the handler: serialize, and while `len(json) > 1000` drop the **last** history row
(history is newest-first, so the oldest goes). Favorites never exceed the budget with the caps
above (4 rows × ≤ 181 bytes + 75 envelope), so paging stays offset-based.
Always `json.dumps(..., ensure_ascii=False, separators=(",", ":"))`.

## Requests (`TelegramWidget.request`, ASCII ≤ 64 bytes) and answers

Answer shapes: **fragment** = the same keys as `chat_data` for the new view with the new `r`
(the widget merges and redraws at once; the bot then edits `chat_data` with the same content
and the same `r`, the widget ignores that second copy); `{"ok":1}` = done, the result is a new
message; `{"err":"<short Russian text>"}` = shown as a red toast, nothing changes.

| verb | answer | what the bot does |
|------|--------|-------------------|
| `t:m` / `t:h` / `t:f` | fragment of that tab (`t:f` → `ff:a`, `fp:0`) | load the tab's rows; `editMessageWidgetData` |
| `h:<query_id>` | `{"ok":1}` or `{"err":"Запрос устарел, выполните новый поиск"}` | same as today's `Hst` callback: `run_search(message.answer(SEARCHING_TEXT), …)` → a **new** search card message; no state edit |
| `hc` | fragment `{"r":…,"tb":"h","h":[]}` | delete the user's `search_events` (new repo method), edit state |
| `ff:a` / `ff:u` | favorites fragment, page 0 | filter all / updated-only |
| `fp:<n>` | favorites fragment, page n | offset `4*n`, same filter |
| `fo:<fav_id>` | `{"l":"https://…"}` (details_url, https only, ≤ 256 chars; omit `l` for http/invalid) or `{"err":"Записи больше нет в избранном"}` | lookup only, no state edit. Sent when a row is expanded the first time (cached in the widget afterwards) |
| `fd:<fav_id>` | `{"ok":1}` / `{"err":"Файл с трекера слишком большой"}` / `{"err":"Не удалось скачать — ссылка могла устареть"}` | `download_favorite` logic: `answer_document(.torrent)`, `record_download(..., "chat")`; answer **after** sending (≤ 15 s — answer first with `{"ok":1}` if Jackett may be slow and report failures as a message) |
| `fs:<fav_id>` | `{"ok":1}` / `{"err":"Недостаточно прав"}` / `{"err":"qBittorrent недоступен"}` | admins only: `begin_torrent_send(...)` → the file-picker widget as a new message |
| `fr:<fav_id>` | favorites fragment (same filter, page clamped to the last one) with new `fn`/`fu` | `remove_favorite`, edit state |
| `con` / `dash` / `usr` | `{"ok":1}` / `{"err":"Недостаточно прав"}` | admins: send the console / dashboard / users widget as a new message |

Local (no request): «Скопировать /s» → `copyText('/s ')`; «На трекере» → `openLink(l)`;
inline confirms («Очистить историю?», «Убрать из избранного?»); accordion open/close after
the first `fo:`.

Haptics: `selectionChanged` on tab/page change, `impactOccurred('light')` on accordion and
confirm toggles, `notificationOccurred('success'|'error')` on `{"ok":1}` / errors.

Widget-side error texts for `WidgetRequestError.code`: NOT_ALLOWED «Откройте чат с ботом»,
BUSY «Подождите…», RATE_LIMITED «Слишком часто», TIMEOUT «Бот не ответил», FAILED «Ошибка
связи», INVALID_ANSWER «Ошибка ответа».

No background requests (`bg`) — the hub is static between taps.

## Caption (fallback for clients without widgets)

```
Напишите /s и название, чтобы найти торрент.
История — /history, избранное — /favorites.
```
(for admins add: `Консоль — /qb, панель — /health, пользователи — /users`). Keep the old
text commands working as fallbacks.

## Notes for the Python handler author

- One `WidgetQuery` handler for this widget: split `data` on the first `:`; unknown verb →
  `answerWidgetQuery(data='{"err":"Неизвестная команда"}')`. Always answer within 15 s.
- `viewer.first_name` comes from the client; the bot does not have to put the name in state.
- `adm` = `config.is_admin(user_id)` **and** `config.qbit_enabled` for the «На сервер» button;
  keep `adm` for the shortcuts even when qBittorrent is off (then `fs:` answers an error).
- `st`: cheap probes only — `jackett.indexers()` is slow; reuse the last `/health` result or a
  cached probe (≤ 60 s old) and put its time in `st.w`. Omit `st` when nothing is cached.
- `h[].a`: `delta = now - search_event.created_at`; `int(delta.total_seconds() // 60)` when
  < 60 min, else `"%H:%M"` if the same local day, else `"%d.%m"`. Timezone: the bot's local.
- `f[].u` needs a column: `favorites.updated_at` (set by `favorites_watcher` when it refreshes
  the snapshot) + `favorites.seen_at` (set when the user downloads / opens `fo:`); `u = updated_at
  > seen_at`. Alternative without migration: a `favorite_updates` table written by the watcher.
- `hc` needs `TorrentRepo.clear_user_history(user_id)` (`DELETE FROM search_events WHERE user_id = …`).
- `fo:` must validate the URL: `https://` only, ≤ 256 chars, else omit `l` (RuTor gives `http://`).
- Page math: `pages = ceil(count/4)`; after `fr:` clamp `fp` to `pages-1`.
- Edit `chat_data` only when the view changes (tab, filter, page, removal, clear); never on
  `h:`, `fd:`, `fs:`, `fo:`, shortcuts.
