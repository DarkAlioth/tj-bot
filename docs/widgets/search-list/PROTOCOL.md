# search-list — protocol (bot ↔ widget)

Widget: `widgets/search-list/widget.html` — the **numbered results list** (10 rows per page), sent by
the bot as a *new message* under the search card when the user taps «Список» (`ls:` on the card).
Sibling of `widgets/search/` (card) and `widgets/search-filters/` (sheet): same CSS vocabulary, same
`ctx` string in requests, same "the bot formats, the widget lays out" rule.

Lifecycle (since the real-device round): card «Список» → bot answers `{"ok":1}` and
`sendWidget(search-list)`. A row tap or «Карточка» → `cd:…` → the bot **sends a fresh card as a new
message below** (chat_data of that index) and **deletes this list message**. «Закрыть» → `x` → the bot
deletes this message. The «done» state (`{"done":"…"}`) is only the **fallback** when the deletion fails
(or the bot prefers to keep the message): the widget draws one hint-coloured line and no controls.

## 1. chat_data (JSON, ≤ 1024 bytes UTF-8)

| key   | type | req | meaning / cap |
|-------|------|-----|---------------|
| `v`   | int  | yes | schema version `1` |
| `r`   | int  | yes | revision; the widget ignores `chatDataChanged` with `r` ≤ the newest seen |
| `qh`  | str 8 | yes | first 8 hex of the query hash (echoed in `ctx`) |
| `q`   | str ≤ 24 | no | query text for the context line «дюна» · Сиды ↓ (dropped first under budget pressure) |
| `i0`  | int  | yes | 0-based index of the first row; page = `i0 // 10`, header «Результаты i0+1–i0+len из n» |
| `n`   | int  | yes | total results after filters (`0` → empty state «Ничего не найдено…»); thin-space thousands |
| `s`   | str 2 | yes | sort code `se\|sz\|za\|dt\|da` (echoed; the sort chip sends the *next* code) |
| `sl`  | str ≤ 10 | yes | sort label for the chip and the context line («Сиды ↓» … from `SORT_LABELS`) |
| `f`   | str 3 | yes | filter code (`ResultFilters.to_code()`), echoed |
| `c`   | str  | yes | category token (`category_token()`, 8 hex) or `ALL`, echoed |
| `fa`  | 0/1  | no  | filters active → accent dot on «Фильтры» and the empty-state suffix «с этими фильтрами» |
| `rows`| array | yes | `[[t, m, im?], …]`, **always the full page of 10** (fewer only on the last page); row *k* is index `i0 + k` |
| `done`| str ≤ 40 | no | fallback done state: the widget draws only «✓ {done}» (hint colour, no controls) |

Row fields (arrays, not objects — saves 20 B per row):
- `t` — title ≤ **36 chars**, the Russian part of the title (`title.split(" / ", 1)[0]`, the bot
  truncates on a word boundary with «…»). At 320 px ≈ 30 chars are visible, the rest is ellipsised
  (a 36-char single word is ellipsised too — nothing wraps or overflows).
- `m` — meta, **4 fields joined with `|`**, ≤ 40 chars: `size|date|seeders|tracker` →
  `"45 ГБ|01.03.24|1 543|RuTracker"`. The widget draws `45 ГБ · 01.03.24 · ⇡1 543 · RuTracker`
  (separators and the ⇡ glyph are layout; the values are formatted by the bot: `fmt_size`, `%d.%m.%y`,
  thin-space thousands). A 3-field `m` (tracker dropped) is valid. One line, ellipsis.
- `im` — optional rating string already comma-formatted («8,5») → ★ pill on the title line.
  Send IMDb if known, else Kinopoisk; one pill per row.

Typical state (mock scenario 0, 10 rows, 6 ratings): **788 B**. Last page with 2 rows: 222 B.
Done state: ~110 B. Mock scenario 7 (3-digit indexes, 36-char words, 24-char `q`, rating on every row)
is 1 423 B — layout stress only, the bot must pack it as below.

### Byte budget and the drop order (page size never changes)

Worst case with every cap filled with Cyrillic (q 24, 10 × t 36 + m 38 + im): **1 415 B**. The bot
must shrink strings — never rows — after serialising with
`json.dumps(d, ensure_ascii=False, separators=(",", ":"))`:

```python
LIMIT = 1024

def cut(t: str, n: int) -> str:
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"

def pack_list(d: dict) -> str:
    steps = (
        lambda: [r.pop() for r in d["rows"] if len(r) > 2],                         # 1. ratings
        lambda: d.pop("q", None),                                                   # 2. query text
        lambda: [r.__setitem__(0, cut(r[0], 28)) for r in d["rows"]],               # 3. titles → 28
        lambda: [r.__setitem__(1, "|".join(r[1].split("|")[:3])) for r in d["rows"]],  # 4. tracker
        lambda: [r.__setitem__(0, cut(r[0], 20)) for r in d["rows"]],               # 5. titles → 20
    )
    for step in steps:
        s = json.dumps(d, ensure_ascii=False, separators=(",", ":"))
        if len(s.encode()) <= LIMIT:
            return s
        step()
    return json.dumps(d, ensure_ascii=False, separators=(",", ":"))
```

Measured: the pathological page (10 titles of 36 Cyrillic letters, m at 40, rating everywhere,
q 23 chars) goes through all five steps and lands at **873 B** (mock scenario 3). Step 5 is the
guaranteed floor: 10 × (9 + 40 + 27) + 111 = **871 B** even with 20-letter Cyrillic titles and no
spaces. Realistic pages stop at step 0–1. `shots/mock_gen.py` carries the same `pack()`; keep them in sync.

## 2. Requests (`TelegramWidget.request`, ≤ 64 bytes ASCII)

`ctx = <qh>:<i0>:<s>:<f>:<c>` — the same shape as the card's `ctx` (`i` = first index of the page).
Longest request: `lp:a1b2c3d4:290:se:000:a1b2c3d4:30` = 34 bytes.

| request | when | bot must |
|---------|------|----------|
| `lp:ctx:<page>` | ‹ › squares in the bottom row, horizontal swipe on the rows | page `page` (offset `page*10`) with the same sort/filters → answer the **full new chat_data** (new `r`) and `editMessageWidgetData` with the same JSON. Slide animation in the widget. |
| `ls:ctx` | sort chip (`s` in ctx is already the **next** code, se→sz→za→dt→da→se) | page 0 of the re-sorted set → full state (slide) |
| `fs:ctx` | «Фильтры» | `sendWidget(search-filters)` → `{"ok":1}`. When filters are applied there, the sheet flow sends a **new card** and deletes the sheet; this list then shows stale rows — recommended: delete it too in the same handler (one live view), or edit it to page 0 of the filtered set (`f`, `c`, `n`, `fa`, new `r`) |
| `cd:ctx:<index>` | row tap (0-based index `i0+k`) or «Карточка» chip (`index = i0`) | 1) answer `{"ok":1}`; 2) `sendWidget(search card)` as a **new message** with chat_data for `index` (no caption); 3) `deleteMessage(this list)`. If 3 fails → `editMessageWidgetData(this list, {"v":1,"r":<r+1>,"qh":…,"done":"Открыта карточка 7"})` (or answer that done state in step 1 when you already know you will keep the message) |
| `x` | «Закрыть» (header) | `deleteMessage(this list)` → `{"ok":1}`. Fallback when deletion fails: done state `{"v":1,"r":<r+1>,"qh":…,"done":"Закрыто"}` (answer or edit) |

Answer **before** sending/deleting so the client's pending state clears within the request timeout;
an answer to a query whose message was already deleted is harmless.

## 3. Answers (`answerWidgetQuery`, ≤ 1024 bytes)

- Full state `{"v":1,"r":…,"rows":[…]}` → replaces the state, slides in the tap's direction (`lp`, `ls`).
- `{"ok":1}` → nothing visible (`fs`, `cd`, `x` — the message is about to be deleted or a new one appears).
- Done state `{"v":1,"r":…,"qh":…,"done":"Открыта карточка 7"}` (fallback) → one hint-coloured line
  and nothing else; a fragment `{"r":…,"done":"…"}` without `v` is merged and works too.
- `{"err":"<Russian text>"}` → red toast on the context line (2.5 s), controls stay, e.g.
  «Запрос устарел, выполните новый поиск: /s».
- Rejections → toasts: NOT_ALLOWED «Откройте чат с ботом», BUSY «Подождите…», RATE_LIMITED
  «Слишком часто», TIMEOUT «Бот не ответил», FAILED «Ошибка связи», INVALID_ANSWER «Ошибка ответа».

Revision rule: answer with the same `r` you are about to write into chat_data; the widget keeps the
newest `r` seen and ignores older `chatDataChanged`.

## 4. Caption template (optional fallback for clients without widgets)

```
Результаты {i0+1}–{i0+len} из {n} по запросу «{query}» · {sort label}
```
Send with `show_caption_above_media=True` (or no caption at all, like the card); the widget pads for
the time band either way.

## 5. Notes for the Python handler author

- Reuse `repo.get_result_list(qh, 10, page*10, category, sort, filters)` and
  `count_results` exactly as `render_list` does today; `LIST_PAGE_SIZE` stays 10 — the widget's
  page arithmetic (`page = i0 // 10`, `pages = ceil(n / 10)`) assumes it.
- Title: `title.split(" / ", 1)[0]`, then `cut(t, 36)`; `m = f"{fmt_size(size)}|{published_at:%d.%m.%y}|{fmt_int(seeders)}|{tracker}"`.
- `cd` needs the card builder (`search/PROTOCOL.md` §5, new-card flow) — same function as `/s`.
- `done` text for `cd`: «Открыта карточка {index+1}» (1-based for humans; the request carries the 0-based index).
- `WidgetQuery.data` → `verb, *args = data.split(":")`; `x` has no args. Validate `qh` against the stored
  search (unknown/expired → `{"err": STALE_QUERY_TEXT}`).
- Answer `fs:` with `{"ok":1}` *before* sending the sheet so the widget's pending state clears quickly.
