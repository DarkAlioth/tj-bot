# search — protocol (bot ↔ widget)

Widget: `widgets/search/widget.html` — the search result **card** (the screen after `/s дюна`).
A clean **typographic** composition, **no images**: the ambient mosaic and the category gradient are
gone, the keys `m`, `hu`, `hp`, `img` do not exist any more (the widget ignores them if an old bot still
sends them — never send them).

Siblings: `widgets/search-list/` (numbered list, request `ls`) and `widgets/search-filters/` (filter
sheet, request `fs`) are **separate messages**. A row tap in the list or «Применить» in the sheet makes
the bot send a **fresh card as a new message below** (new `chat_data`: the tapped index, or index 0 of
the filtered set) and delete the list/sheet. The card that opened them is **not edited**: it stays live
with its own `ctx` (the sort/filters it was sent with). See the two sibling PROTOCOL.md files.

The card is sent **without a caption** — the message is the widget. The client draws the time in the
bottom-right band; the widget pads for it (`padding-bottom:max(12px,env(safe-area-inset-bottom),
var(--tg-content-safe-area-inset-bottom,0px))`) and nothing tappable sits in that corner (the footer line
under the nav row is text only).

Design principle: **the bot formats, the widget lays out.** Every human-readable string (size, date,
duration, genre line, sort label, category label) is pre-formatted in Python with the helpers in
`services/formatting.py`. The widget only escapes and places them.

## 1. chat_data (JSON, ≤ 1024 bytes UTF-8)

Full view state of ONE card + search context. Keys absent = feature absent; the card must look finished
with only the "required" rows. Every string is drawn with `min-width:0` + ellipsis / `overflow-wrap`, so
over-long values degrade gracefully, but respect the caps — they are what fits at 320 px.

| key  | type   | req | meaning / cap |
|------|--------|-----|---------------|
| `v`  | int    | yes | schema version, `1` |
| `r`  | int    | yes | revision, +1 on every edit/answer; the widget ignores `chatDataChanged` with `r` ≤ newest seen |
| `qh` | str 8  | yes | first 8 hex of the query hash (echoed in requests) |
| `i`  | int    | yes | 0-based index of this card inside the (filtered, sorted) result set |
| `n`  | int    | yes | total results after filters (0 → empty state); header shows «i+1 / n» with thin-space thousands |
| `s`  | str 2  | yes | sort code `se\|sz\|za\|dt\|da` (echoed in requests) |
| `sl` | str ≤ 10 | yes | sort label on the chip: «Сиды ↓», «Размер ↓», «Размер ↑», «Дата ↓», «Дата ↑» |
| `f`  | str 3  | yes | filter code `<seeders><size><date>` (`ResultFilters.to_code()`), echoed |
| `c`  | str    | yes | category token (`category_token()`, 8 hex) or `ALL`, echoed |
| `fa` | 0/1    | no  | filters active → accent dot on the «Фильтры» chip |
| `fc` | 0/1    | no  | from cache → «⚡ кэш» chip in the header |
| `adm`| 0/1    | no  | admin → «На сервер» button |
| `h`  | str 8  | yes* | first 8 hex of the torrent hash (`*` absent only in the empty state) |
| `tr` / `te` | str ≤ 60 each | one of | Russian / English title parts (split by the bot). `tr` is the big bold title (2-line clamp, long words break), `te` the hint-coloured subtitle (1 line, ellipsis) |
| `ti` | str ≤ 120 | one of | raw title when no split is possible (same 2-line clamp) |
| `y`  | int    | no  | year chip (first chip) |
| `q`  | [str ≤ 12] ≤ 5 | no | quality chips («2160p», «HDR10», «Dolby Vision», «BDRemux», «S1E1-6»…); the chip row is clamped to 2 lines, so > 5 chips may hide the rating pills at 320 px |
| `cn` | str ≤ 20 | no | category label («Фильмы HD», «Сериалы HD», «Игры», «Музыка», «Книги»…); shrinks with an ellipsis when the header is full |
| `tk` | str ≤ 10 | yes | tracker display name («RuTracker», «RuTor», «MegaPeer», «NoNaMe», «Tapochek», «PC-torrent», «Byrutor») — the accent chip |
| `sd` / `pr` | int | yes | seeders / peers (health ring = sd/(sd+pr); 0 seeders → red «нет сидов») |
| `sz` | str ≤ 9 | yes | formatted size «45 ГБ», «912 МБ», «8,2 ГБ», «999,9 ГБ» |
| `dt` | str 10 | yes | formatted date «01.03.2024» |
| `dr` | str ≤ 12 | no | relative label under the date, only when ≤ 30 days: «сегодня», «вчера», «9 дн. назад» (default label «дата») |
| `up` | str ≤ 16 | no | uploader → footer «Загрузил X» (longer values are ellipsised) |
| `ml` | str ≤ 60 | no | meta line: `genres(≤3) · country · duration` («фантастика, драма · США · 2 ч 46 мин») |
| `pp` | str ≤ 60 | no | people line: «Реж. Дени Вильнёв · Тимоти Шаламе, Зендея» (bot truncates with «…») |
| `aa` | str ≤ 50 | no | audio/subs line: «Дубляж, MVO · Субтитры: рус, англ» |
| `im` / `kp` | str | no | ratings already comma-formatted «8,5» → «★ IMDb 8,5» / «★ КП 8,1» pills (text colour, after the quality chips) |
| `pl` | str ≤ 80 | no | plot teaser (word boundary + «…»); 2-line clamp |
| `pm` | 0/1    | no  | 1 → «ещё» is shown and the first tap requests `more:<h>` |
| `u`  | str ≤ 64 | no | details URL; the «↗» button appears only for `https://` (openLink rejects http) |
| `fav`| 0/1    | no  | in favorites (★ accent label «В избранном») |
| `en` | 0/1    | no  | enrichment pending → «загружаем детали …» under the chips until the bot edits chat_data |

Removed (do not send): `m` (mosaic), `hu` (gradient hue), `hp` / `img` (poster).

Empty state: `{"v":1,"r":..,"qh":..,"i":0,"n":0,"s":"se","sl":"Сиды ↓","f":"300","c":"ALL","fa":1,"tk":"RuTracker"}`
→ «Ничего не найдено с этими фильтрами» + the nav row (‹ › disabled, sort / «Фильтры» / «Список» chips).

### Byte budget

Realistic cards (mock.json scenarios 0–7): 137–895 bytes. Worst case with every cap filled with Cyrillic
(ti 120, q 5×12, ml 60, pp 60, aa 50, pl 80, up 16, u 64): **≈ 1 300 bytes** → the bot applies the drop
order below after serialising with `json.dumps(d, ensure_ascii=False, separators=(",", ":"))`:

```python
DROP_ORDER = ("pl", "pp", "aa", "ml", "dr", "u", "te")  # least valuable first

def pack(d: dict[str, object]) -> str:
    for key in DROP_ORDER:
        s = json.dumps(d, ensure_ascii=False, separators=(",", ":"))
        if len(s.encode()) <= 1024:
            return s
        d.pop(key, None)
        if key == "pl":
            d["pm"] = 1          # the full plot stays reachable through `more`
    return json.dumps(d, ensure_ascii=False, separators=(",", ":"))
```

With the worst case above the loop drops `pl` and `pp` and lands at ≈ 1 004 bytes (`aa` survives). Keep
the full plot and cast only in the `more` answer (≤ 1024 bytes: `pl` ≤ 360 chars + `pp` ≤ 100).
Mock scenario 8 is deliberately 1 645 bytes (layout stress only — never ship it).

## 2. Requests (`TelegramWidget.request`, ≤ 64 bytes ASCII)

Context string `ctx = <qh>:<i>:<s>:<f>:<c>` (≤ 29 bytes) makes every request self-contained
(the bot gets no chat_data with a widget_query — same idea as today's `Pg2` callback_data).

| request | when | bot must |
|---------|------|----------|
| `n:ctx` / `p:ctx` | › ‹ buttons in the nav row, horizontal swipe on the card body | load card `i±1` with the same sort/filters → answer the **full new chat_data** (new `r`), then `editMessageWidgetData` with the same JSON |
| `s:ctx` | sort chip (`s` in ctx is already the **next** code: se→sz→za→dt→da→se) | card 0 of the re-sorted set → full state |
| `ls:ctx` | «Список» | `sendWidget(search-list)` as a new message (page `i // 10`, same sort/filters) → answer `{"ok":1}`. This card stays as is |
| `fs:ctx` | «Фильтры» | `sendWidget(search-filters)` as a new message → `{"ok":1}`. This card stays as is; the sheet's «Применить» produces a **new card message**, not an edit of this one |
| `d:<h>` | «Скачать .torrent» | send the `.torrent` document into the chat → `{"ok":1}` → toast «Файл отправлен» |
| `fav:<h>` | «☆/★» (optimistic flip) | toggle favorite → `{"fav":1,"r":<r+1>}` (merged; on error the widget flips back) |
| `srv:<h>` | «На сервер» (admins) | send the qBittorrent file-picker widget / start the download → `{"ok":1}` → «Отправлено на сервер» |
| `more:<h>` | «ещё» on the plot (only when `pm:1`) | `{"pl":"<full plot ≤ 360 chars>","pp":"<director · full cast ≤ 4>"}` — cached in the widget, not written to chat_data |

«↗» (open on the tracker) needs no request: `TelegramWidget.openLink(u)` in the tap handler.

Keep `d:` under the client request timeout: answer `{"ok":1}` right after `answer_document`, or
answer first and send the file after.

## 3. Answers (`answerWidgetQuery`, ≤ 1024 bytes)

- Full state `{"v":1,"r":…}` → replaces the whole widget state and slides (direction from the tap).
  Must be a **complete** chat_data (no merge), identical to what the bot then edits into the message.
- Fragment without `v` (`{"fav":1,"r":9}`, `{"pl":…,"pp":…}`) → merged / cached.
- `{"ok":1}` → success toast for `d` / `srv`, nothing for `ls` / `fs`.
- `{"err":"<Russian text>"}` → red toast with the text (e.g. «Запрос устарел, выполните новый поиск: /s»).
- Rejections map to toasts: NOT_ALLOWED «Откройте чат с ботом», BUSY «Подождите…», RATE_LIMITED
  «Слишком часто», TIMEOUT «Бот не ответил», FAILED «Ошибка связи», INVALID_ANSWER «Ошибка ответа».

Revision rule: the widget keeps the newest `r` seen (chat_data or answer). A `chatDataChanged`
with `r` ≤ that is ignored, so always answer with the same `r` you are about to write.

Enrichment flow: send the card with `en:1` and whatever is known; when the tracker page is parsed,
`editMessageWidgetData` with `r+1`, `en:0`, `ml/pp/aa/im/kp/pl/pm/tr/te` → the widget redraws in place
(same `h` → no slide).

## 4. Caption

None. `sendWidget(..., caption=None)`; the widget keeps the time band clear itself. (Clients without
widget support show nothing useful — accepted: this bot is used from current clients only.)

## 5. Notes for the Python handler author

- Reuse `category_token()` for `c`, `ResultFilters.to_code()` for `f`, `SORT_LABELS` for `sl`.
- Title split: `title.split(" / ", 1)` → `tr`, then `te` = text before `(` / `[`; the rest of the
  title → `q` chips via the regex list in BRIEF §4. If no ` / ` → `ti` only.
- `cn` by the map Movies→«Фильмы», TV→«Сериалы», PC/Games→«Игры», Audio→«Музыка», Books→«Книги»
  (+ « HD» / « 4K» for `/HD`, `/UHD`); ≤ 20 chars.
- `dr` only when `published_at >= today - 30 days`.
- Requests arrive as `WidgetQuery.data`; parse `verb[:ctx]` with `split(":")`.
- New-card flow (from the list/sheet): build the state exactly as for `/s` (index = tapped index or 0),
  `sendWidget(chat_id, html=CARD_HTML, chat_data=pack(state), allow_queries=True)`, no caption, no
  reply_markup; then delete the list/sheet message. The previous card is left untouched.
