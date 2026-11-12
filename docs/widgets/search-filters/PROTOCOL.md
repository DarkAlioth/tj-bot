# search-filters — protocol (bot ↔ widget)

Widget: `widgets/search-filters/widget.html` — the **filter sheet**, sent as a *new message* when the
user taps «Фильтры» on the card (`fs:` → `{"ok":1}` → `sendWidget`) or on the list. Sibling of
`widgets/search/` and `widgets/search-list/`: same CSS vocabulary and `ctx` conventions.

The working selection is **local**: every tap only toggles classes and rewrites the «Активно: …»
summary (haptic `selectionChanged`), and is mirrored into `sessionStorage` so a re-created widget
keeps it. One request only: «Применить».

## 1. chat_data (JSON, ≤ 1024 bytes UTF-8)

| key   | type | req | meaning / cap |
|-------|------|-----|---------------|
| `v`   | int  | yes | schema version `1` |
| `r`   | int  | yes | revision (newest wins) |
| `qh`  | str 8 | yes | query hash prefix (echoed) |
| `i`   | int  | no  | index of the origin view (card index / list `i0`), echoed in `ctx`; default 0 |
| `s`   | str 2 | yes | sort code (echoed) |
| `f`   | str 3 | yes | **applied** filter code `<seeders><size><date>` (today's `ResultFilters.to_code()`); invalid → `000` |
| `c`   | str  | yes | **applied** category token (8 hex) or `ALL` |
| `n`   | int  | no  | total results (informational; the «Все» chip carries its own count) |
| `cats`| array | no | `[[token, label, count], …]`, **≤ 10 entries, labels ≤ 18 chars**, «Все» first: `["ALL","Все",302]`. Absent → no category section (single-category search) |
| `done`| str ≤ 80 | no | done state: «✓ {done}» in hint colour, no controls |

Buckets and labels live in the widget (they are the control's captions):
seeders `[любые, 1+, 10+, 50+]`, size `[любой, < 1 ГБ, 1–5 ГБ, 5–20 ГБ, > 20 ГБ]`, date
`[любая, неделя, месяц, год]` — the same order as `db/filters.py` (`SEEDERS_MIN`, `SIZE_RANGES`,
`DATE_DAYS`), so the code digits are identical. Note the widget writes «ГБ» where `SIZE_LABELS`
says «GB».

Summary line «Активно: Фильмы HD · сиды 10+ · 1–5 ГБ · за месяц» / «Активно: не заданы» is built
locally from the working selection.

Byte budget: typical 291 B (7 categories), 10 categories with 18-char labels and 5-digit counts
**565 B** measured, 675 B if every label is 18 Cyrillic letters. No drop order needed; if `cats`
ever exceeds 10 the bot sends the 10 largest (the rest are unreachable from the sheet — document the
overflow in the caption or keep today's inline picker for it).

## 2. Requests (≤ 64 bytes ASCII)

`ctx = <qh>:<i>:<s>:<f>:<c>` — `f`/`c` are the **applied** values the sheet was opened with.

| request | when | bot must |
|---------|------|----------|
| `f:ctx:<code>:<cat>` | «Применить» (`code` = working 3-digit filter code, `cat` = token or `ALL`) | count the filtered set; if 0 → `{"err":"Ничего не найдено с этими фильтрами"}` (controls stay); else edit the **card** (and the list, if live) to page/index 0 of the filtered set with `f`,`c`,`n`,`fa`, answer this widget with the done state and edit its chat_data to the same |

Longest request: `f:a1b2c3d4:300:se:000:a1b2c3d4:333:a1b2c3d4` = 44 bytes.
«Сбросить» is local (working selection → `000`/`ALL`, haptic impact); the user still taps «Применить»
to send `f:…:000:ALL` — the bot treats it like today's `rs` (reset incl. category).

## 3. Answers (≤ 1024 bytes)

- Done: `{"v":1,"r":<r+1>,"qh":…,"done":"Применено: сиды 10+ · 1–5 ГБ · Фильмы HD · 48 результатов"}`
  (a fragment `{"r":…,"done":"…"}` without `v` is merged and works too). The widget clears its
  `sessionStorage` entry, haptic success, redraws as one line.
- `{"err":"…"}` → the text under the buttons in `--tg-theme-destructive-text-color`, stays until
  the next tap (the user has to change something anyway), haptic error.
- Rejections → the same slot: NOT_ALLOWED «Откройте чат с ботом», BUSY «Подождите…», RATE_LIMITED
  «Слишком часто», TIMEOUT «Бот не ответил», FAILED «Ошибка связи», INVALID_ANSWER «Ошибка ответа».
- A full non-done state (`v` present, new `f`/`c`) is also accepted: the sheet re-bases on it (the
  working selection is reloaded from the new `f`/`c`).

`sessionStorage` key: `sf:<qh>:<f>:<c>` → `"<code>:<cat>"`. The key includes the applied base, so
a sheet re-sent with different applied filters never restores a stale selection.

## 4. Caption template

```
Фильтры поиска «{query}» · Активно: {summary or «не заданы»}
```

## 5. Notes for the Python handler author

- `cats`: `[["ALL", "Все", search.result_count]] + [[category_token(name), label(name)[:18], count] for name, count in categories[:9]]`
  — `label()` = the card's `cn` mapping (Movies/HD → «Фильмы HD», …) or the raw Torznab name.
- `done` text: «Применено: » + `", ".join(parts)` where parts = `сиды {SEEDERS_LABELS}`,
  `{SIZE_LABELS}` (write «ГБ»), «за неделю/месяц/год», category label, then `· {n} результатов`
  (use the existing pluralisation helper if there is one; «48 результатов», «1 результат»).
- Reset: `code == "000" and cat == "ALL"` → done text «Применено: фильтры сброшены · 302 результата».
- Unknown `qh` → `{"err": STALE_QUERY_TEXT}`.
