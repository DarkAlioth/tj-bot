# search — protocol (bot ↔ widget)

Widget: `widgets/search/widget.html` — the search result **card** (hero screen after `/s дюна`).
Scope note (see NOTES.md §1): the 4096-byte compressed cap holds the card only. The list view
and the filter sheet are **hand-offs** (`ls:` / `fs:` requests): the bot sends them as separate
messages (another widget or today's inline-keyboard list / filter menu) and edits this card's
`chat_data` when the user applies a filter / taps a row there.

Design principle: **the bot formats, the widget lays out.** Every human-readable string (size,
date, duration, genre line, sort label, category label) is pre-formatted in Python with the
existing helpers in `services/formatting.py`. The widget only escapes and places them. This is
what keeps the widget under the size cap and keeps number/date formatting in one place.

## 1. chat_data (JSON, ≤ 1024 bytes UTF-8)

Full view state of ONE card + search context. Keys absent = feature absent; the card must
look finished with only the "required" rows.

| key  | type   | req | meaning / cap |
|------|--------|-----|---------------|
| `v`  | int    | yes | schema version, `1` |
| `r`  | int    | yes | revision, +1 on every edit/answer; widget ignores `chatDataChanged` with `r` ≤ newest seen |
| `qh` | str 8  | yes | first 8 hex of the query hash (echoed in requests) |
| `i`  | int    | yes | 0-based index of this card inside the (filtered, sorted) result set |
| `n`  | int    | yes | total results after filters (0 → empty state) |
| `s`  | str 2  | yes | sort code `se|sz|za|dt|da` (echoed in requests) |
| `sl` | str    | yes | sort label shown on the chip: «Сиды ↓», «Размер ↓», «Размер ↑», «Дата ↓», «Дата ↑» |
| `f`  | str 3  | yes | filter code `<seeders><size><date>` (today's `ResultFilters.to_code()`), echoed |
| `c`  | str    | yes | category token (`category_token()`, 8 hex) or `ALL`, echoed |
| `fa` | 0/1    | no  | filters active → dot on the «Фильтры» chip |
| `fc` | 0/1    | no  | from cache → «⚡ кэш» chip in the hero |
| `adm`| 0/1    | no  | admin → «На сервер» button |
| `h`  | str 8  | yes* | first 8 hex of the torrent hash (`*` absent only in the empty state) |
| `tr` / `te` | str ≤ 60 each | one of | Russian / English title parts (split by the bot) |
| `ti` | str ≤ 120 | one of | raw title when no split is possible (2-line clamp + ellipsis) |
| `y`  | int    | no  | year chip |
| `q`  | [str ≤ 12] ≤ 5 | no | quality chips («2160p», «HDR10», «Dolby Vision», «BDRemux», «S1E1-6»…) |
| `cn` | str ≤ 14 | no | category label («Фильмы HD», «Сериалы HD», «Игры», «Музыка», «Книги»…) |
| `hu` | int    | no  | backdrop hue when there is no mosaic: Movies 262, TV 178, Games 36, Audio 322, Books 82, other 212 (default) |
| `tk` | str    | yes | tracker display name («RuTracker», «RuTor», «MegaPeer», «NoNaMe», «Tapochek», «PC-torrent», «Byrutor») |
| `sd` / `pr` | int | yes | seeders / peers (health ring = sd/(sd+pr); 0 seeders → red «нет сидов») |
| `sz` | str    | yes | formatted size «45 ГБ», «912 МБ», «8,2 ГБ» |
| `dt` | str    | yes | formatted date «01.03.2024» |
| `dr` | str ≤ 12 | no | relative label under the date, only when ≤ 30 days: «сегодня», «вчера», «9 дн. назад» (default label «дата») |
| `up` | str ≤ 16 | no | uploader → footer «Загрузил X» |
| `ml` | str ≤ 60 | no | meta line: `genres(≤3) · country · duration` («фантастика, драма · США · 2 ч 46 мин») |
| `pp` | str ≤ 60 | no | people line: «Реж. Дени Вильнёв · Тимоти Шаламе, Зендея» (bot truncates with «…») |
| `aa` | str ≤ 50 | no | audio/subs line: «Дубляж, MVO · Субтитры: рус, англ» |
| `im` / `kp` | str | no | ratings already comma-formatted «8,5» → ★ pills |
| `pl` | str ≤ 80 | no | plot teaser (word boundary + «…»); 2-line clamp |
| `pm` | 0/1    | no  | 1 → «ещё» is shown and the first tap requests `more:<h>` |
| `u`  | str ≤ 64 | no | details URL; the «↗» button appears only for `https://` (openLink rejects http) |
| `fav`| 0/1    | no  | in favorites |
| `en` | 0/1    | no  | enrichment pending → «загружаем детали …» hint until the bot edits chat_data |
| `m`  | str 72 (or 144) hex | no | 4×6 ambient mosaic, **12-bit `rgb` per cell recommended** (72 hex); 24-bit `rrggbb` (144 hex) also accepted |

Empty state: `{"v":1,"r":..,"qh":..,"i":0,"n":0,"s":"se","sl":"Сиды ↓","f":"300","c":"ALL","fa":1,"tk":"RuTracker"}`
→ «Ничего не найдено с этими фильтрами» + the sort / filters / list chips.

### Byte budget

Realistic cards (mock.json): 376–983 bytes. Worst case with every cap filled with Cyrillic
(ti 120, q 5×12, ml 60, pp 60, aa 50, pl 80, up 16, u 64, m 72): **1388 bytes** → does not fit,
so the bot must apply the drop order below after serialising with
`json.dumps(d, ensure_ascii=False, separators=(",", ":"))`:

```python
DROP_ORDER = ("pl", "pp", "aa", "ml", "dr", "u", "te", "m")  # least valuable first

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

With the worst case above the loop drops `pl`, `pp`, `aa` and lands at 984 bytes. Keep the
full plot and cast only in the `more` answer (≤ 1024 bytes: `pl` ≤ 360 chars + `pp` ≤ 100).

## 2. Requests (`TelegramWidget.request`, ≤ 64 bytes ASCII)

Context string `ctx = <qh>:<i>:<s>:<f>:<c>` (≤ 29 bytes) makes every request self-contained
(the bot gets no chat_data with a widget_query — same idea as today's `Pg2` callback_data).

| request | when | bot must |
|---------|------|----------|
| `n:ctx` / `p:ctx` | › ‹ buttons, swipe | load card `i±1` with the same sort/filters → answer the **full new chat_data** (new `r`), then `editMessageWidgetData` with the same JSON |
| `s:ctx` | sort chip (`s` in ctx is already the **next** code: se→sz→za→dt→da→se) | card 0 of the re-sorted set → full state |
| `ls:ctx` | «Список» | send the list message (page `i // 10`) → answer `{"ok":1}` (or a full state if the bot prefers to stay on the card) |
| `fs:ctx` | «Фильтры» | send the filter menu message → `{"ok":1}`; when applied, edit this card's chat_data to card 0 of the filtered set (`fa`, `f`, `c`, `n`, new `r`) |
| `d:<h>` | «Скачать .torrent» | send the `.torrent` document into the chat → `{"ok":1}` → toast «Файл отправлен» |
| `fav:<h>` | «☆/★» (optimistic flip) | toggle favorite → `{"fav":1,"r":<r+1>}` (merged; on error the widget flips back) |
| `srv:<h>` | «На сервер» (admins) | send the qBittorrent file-picker widget / start the download → `{"ok":1}` → «Отправлено на сервер» |
| `more:<h>` | «ещё» on the plot (only when `pm:1`) | `{"pl":"<full plot ≤ 360 chars>","pp":"<director · full cast ≤ 4>"}` — cached in the widget, not written to chat_data |

«↗ Открыть на трекере» needs no request: `TelegramWidget.openLink(u)` in the tap handler.

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
`editMessageWidgetData` with `r+1`, `en:0`, `ml/pp/aa/im/kp/pl/pm/m/tr/te` → the widget redraws
in place (same `h` → no slide). The poster itself is a separate photo message edited by the bot.

## 4. Caption template (fallback for clients without widgets)

```
{title_ru} · {size} · {seeders} сидов · {tracker} · {i+1} / {n}
```
e.g. «Дюна: Часть вторая · 45 ГБ · 1 543 сидов · RuTracker · 12 / 302». Send it with
`show_caption_above_media=True` so the widget keeps the time band at the bottom (the widget pads
for `env(safe-area-inset-bottom)` already).

## 5. Notes for the Python handler author

- Reuse `category_token()` for `c`, `ResultFilters.to_code()` for `f`, `SORT_LABELS` for `sl`.
- Title split: `title.split(" / ", 1)` → `tr`, then `te` = text before `(` / `[`; the rest of the
  title → `q` chips via the regex list in BRIEF §4. If no ` / ` → `ti` only.
- `hu` by Torznab category prefix; `cn` by the map Movies→«Фильмы», TV→«Сериалы», PC/Games→«Игры»,
  Audio→«Музыка», Books→«Книги» (+ « HD» / « 4K» for `/HD`, `/UHD`).
- `m`: 24 colours → `"".join(f"{r>>4:x}{g>>4:x}{b>>4:x}")` (72 hex). The widget doubles each
  nibble; the blur hides the quantisation.
- `dr` only when `published_at >= today - 30 days`.
- Requests arrive as `WidgetQuery.data`; parse `verb[:ctx]` with `split(":")`.
