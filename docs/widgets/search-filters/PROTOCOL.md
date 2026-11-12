# search-filters — protocol (bot ↔ widget)

Widget: `widgets/search-filters/widget.html` — the **filter sheet**, sent as a *new message* when the
user taps «Фильтры» on the card (`fs:` → `{"ok":1}` → `sendWidget`) or on the list. Sibling of
`widgets/search/` and `widgets/search-list/`: same CSS vocabulary and `ctx` conventions.

The working selection is **local**: every tap only toggles classes and rewrites the «Активно: …»
summary (haptic `selectionChanged`), mirrored into `sessionStorage` so a re-created widget keeps it.
Two requests exist: «Применить» (`f`) and «Закрыть» (`x`).

Flow since the real-device round: «Применить» → the bot **sends a fresh search card as a new message
below** (index 0 of the filtered set) and **deletes this sheet**. «Закрыть» → the bot deletes this sheet.
The «done» state is only the **fallback** when the deletion fails.

## 1. chat_data (JSON, ≤ 1024 bytes UTF-8)

| key   | type | req | meaning / cap |
|-------|------|-----|---------------|
| `v`   | int  | yes | schema version `1` |
| `r`   | int  | yes | revision (newest wins) |
| `qh`  | str 8 | yes | query hash prefix (echoed) |
| `i`   | int  | no  | index of the origin view (card index / list `i0`), echoed in `ctx`; default 0 |
| `s`   | str 2 | yes | sort code (echoed) |
| `f`   | str 3 | yes | **applied** filter code `<seeders><size><date>` (`ResultFilters.to_code()`); invalid → `000` |
| `c`   | str  | yes | **applied** category token (8 hex) or `ALL` |
| `n`   | int  | no  | total results (informational; the «Все» chip carries its own count) |
| `cats`| array | no | `[[token, label, count], …]`, **≤ 10 entries, labels ≤ 18 chars** (20 tolerated: the chip ellipsises the label, the count stays visible), «Все» first: `["ALL","Все",302]`. Absent → no category section |
| `done`| str ≤ 80 | no | fallback done state: «✓ {done}» in hint colour, no controls |

Buckets and labels live in the widget (they are the control's captions):
seeders `[любые, 1+, 10+, 50+]`, size `[любой, < 1 ГБ, 1–5 ГБ, 5–20 ГБ, > 20 ГБ]`, date
`[любая, неделя, месяц, год]` — the same order as `db/filters.py` (`SEEDERS_MIN`, `SIZE_RANGES`,
`DATE_DAYS`), so the code digits are identical. The widget writes «ГБ» where `SIZE_LABELS` says «GB».

Summary line «Активно: Фильмы HD · сиды 10+ · 1–5 ГБ · за месяц» / «Активно: не заданы» is built
locally from the working selection (2-line clamp).

Byte budget: typical 291 B (7 categories), 10 categories with 18-char labels and 5-digit counts
**565 B**, 10 × 20-char labels with 6-digit counts 658 B (mock scenario 7). No drop order needed; if
`cats` ever exceeds 10 the bot sends the 10 largest.

## 2. Requests (≤ 64 bytes ASCII)

`ctx = <qh>:<i>:<s>:<f>:<c>` — `f`/`c` are the **applied** values the sheet was opened with.

| request | when | bot must |
|---------|------|----------|
| `f:ctx:<code>:<cat>` | «Применить» (`code` = working 3-digit filter code, `cat` = token or `ALL`) | count the filtered set; **0 → `{"err":"Ничего не найдено с этими фильтрами"}`** (controls stay, nothing is sent or deleted). Otherwise: 1) answer `{"ok":1}`; 2) `sendWidget(search card)` as a **new message** with chat_data for index 0 of the filtered set (`f`, `c`, `n`, `fa:1`, no caption); 3) `deleteMessage(this sheet)`. If 3 fails → `editMessageWidgetData(this sheet, {"v":1,"r":<r+1>,"qh":…,"done":"Применено: сиды 10+ · 1–5 ГБ · Фильмы HD · 48 результатов"})` — or answer that done state in step 1 when you keep the message on purpose |
| `x` | «Закрыть» (header) | `deleteMessage(this sheet)` → `{"ok":1}`. Fallback: done state `{"v":1,"r":<r+1>,"qh":…,"done":"Закрыто"}` (answer or edit) |

Longest request: `f:a1b2c3d4:300:se:000:a1b2c3d4:333:a1b2c3d4` = 44 bytes.
«Сбросить» is local (working selection → `000`/`ALL`, haptic impact); the user still taps «Применить»
to send `f:…:000:ALL` — the bot treats it like today's `rs` (reset incl. category) and still sends a
new card + deletes the sheet.

If the sheet was opened from the **list**, the list keeps stale rows after a new card appears — delete
the list in the same handler (recommended: one live view) or edit it to page 0 of the filtered set.

## 3. Answers (≤ 1024 bytes)

- `{"ok":1}` → the widget clears its `sessionStorage` entry, haptic success, draws nothing new (the
  message is about to be deleted and the new card appears below).
- Done (fallback): `{"v":1,"r":<r+1>,"qh":…,"done":"Применено: …"}` / `"Закрыто"` (a fragment
  `{"r":…,"done":"…"}` without `v` is merged and works too) → one hint-coloured line.
- `{"err":"…"}` → the text under the buttons in `--tg-theme-destructive-text-color`, stays until the
  next tap, haptic error.
- Rejections → the same slot: NOT_ALLOWED «Откройте чат с ботом», BUSY «Подождите…», RATE_LIMITED
  «Слишком часто», TIMEOUT «Бот не ответил», FAILED «Ошибка связи», INVALID_ANSWER «Ошибка ответа».
- A full non-done state (`v` present, new `f`/`c`) is also accepted: the sheet re-bases on it.

`sessionStorage` key: `sf:<qh>:<f>:<c>` → `"<code>:<cat>"`. The key includes the applied base, so
a sheet re-sent with different applied filters never restores a stale selection.

## 4. Caption template (optional)

```
Фильтры поиска «{query}» · Активно: {summary or «не заданы»}
```
Or no caption, like the card; the widget pads for the time band either way.

## 5. Notes for the Python handler author

- `cats`: `[["ALL", "Все", search.result_count]] + [[category_token(name), label(name)[:18], count] for name, count in categories[:9]]`
  — `label()` = the card's `cn` mapping (Movies/HD → «Фильмы HD», …) or the raw Torznab name.
- The new card is built by the same function as `/s` (see `search/PROTOCOL.md` §5, new-card flow) with
  `f = code`, `c = cat`, `i = 0`, `fa = 1 if (code, cat) != ("000", "ALL") else 0`.
- Fallback `done` text: «Применено: » + `", ".join(parts)` where parts = `сиды {SEEDERS_LABELS}`,
  `{SIZE_LABELS}` (write «ГБ»), «за неделю/месяц/год», category label, then `· {n} результатов`
  (use the existing pluralisation helper; «48 результатов», «1 результат»); reset → «Применено: фильтры
  сброшены · 302 результата».
- Unknown `qh` → `{"err": STALE_QUERY_TEXT}`. `x` has no arguments.
