# broadcast — protocol (bot ↔ widget)

Admin types `/broadcast <text>`; the bot replies with this widget (draft). Flow: preview →
«Отправить» → inline confirm → `go` → progress (background `bg` every 5 s) → report, or
«Отмена» → `x`.

## chat_data (JSON, ≤ 1024 bytes)

```json
{"v":1,"r":1,"st":"draft","n":23,"ok":0,"fail":0,"tx":"Привет! …","more":0}
```

| key | type | meaning |
|-----|------|---------|
| `v` | 1 | schema version |
| `r` | int | revision, +1 per edit |
| `st` | `draft`\|`run`\|`done`\|`cancel` | view |
| `n` | int | recipients = `repo.active_user_ids()` count at draft time |
| `ok` | int | delivered so far / total delivered |
| `fail` | int | not delivered (blocked the bot / chat not found / API error) |
| `tx` | str | plain text as typed (**not** HTML-escaped; the widget uses `textContent`); line breaks preserved; ≤ 600 chars |
| `more` | int | optional: chars cut from `tx` to fit the budget (the widget shows «Показано начало — ещё N симв.») |

Budget: envelope ≈ 70 bytes → `tx` ≤ ~940 bytes after JSON escaping. Rule: `tx = text[:600]`;
while `len(json) > 1000`: cut `tx` by 20 chars (at a word boundary) and set
`more = len(text) - len(tx)`. Measured: 600 Cyrillic chars = 1267 bytes (over), 450 Cyrillic =
978 bytes (ok), 10 lines × 46 Cyrillic + newlines = 1018 bytes (edge). The widget also clips the
preview at 10 lines and shows the same note when the box overflows.

## Requests and answers

| verb | when | answer | what the bot does |
|------|------|--------|-------------------|
| `go` | «Да, отправить» | `{"ok":1,"st":"run"}` (or `{"r":2,"st":"run","ok":0,"fail":0}`) / `{"err":"Недостаточно прав"}` / `{"err":"Черновик уже закрыт"}` | check admin + `st == draft`; start the broadcast as an `asyncio` task that updates an in-memory progress record keyed by `(chat_id, message_id)`; edit `chat_data` to `st:"run"` (r+1); on completion edit `chat_data` to `st:"done"` with final `ok`/`fail` (r+1) |
| `bg` | background tick every 5 s while `st == run` and the widget is visible (first tick 1.5 s after the `go` answer, and one immediately when a widget is (re)created in `run`) | `{"st":"run","ok":12,"fail":1}` while running; `{"r":3,"st":"done","ok":21,"fail":2}` when finished; `{"st":"draft"}` etc. if asked out of order | read the progress record; **no** `chat_data` edit on ticks |
| `x` | «Отмена» | `{"r":2,"st":"cancel"}` / `{"err":"…"}` | mark cancelled, edit `chat_data` (the draft text stays in state, muted) |

Widget-side (no request): the confirm row (Да/Нет), «Закрыть» after `done` (collapses to one
line «Готово · Доставлено 21 из 23 · <text>», stored in `localStorage['c']`, tap re-expands).

Progress view: bar + «12 из 23» with a 300 ms count-up, «N заблокировали бота» in destructive
color, «Обновляется каждые 5 с». Done: «Доставлено 21 из 23», bar in accent color, chip
«Готово», `notificationOccurred('success'|'warning')`. Cancel: «Рассылка отменена».

Errors: `WidgetRequestError.code` → NOT_ALLOWED «Откройте чат с ботом», BUSY «Подождите…»,
RATE_LIMITED «Слишком часто», TIMEOUT «Бот не ответил», FAILED «Ошибка связи», INVALID_ANSWER
«Ошибка ответа» (red toast, the tapped button is restored). `bg` failures are silent (next tick).

## Caption (fallback)

```
📢 Рассылка 23 пользователям — подтвердите в виджете выше.
```
After `done`: `📢 Доставлено 21 из 23` (edit the caption together with the final state so
clients without widgets see the report).

## Notes for the Python handler author

- `broadcaster.broadcast` is synchronous today (the handler awaits all sends). For `bg` it must
  run in a background task with a shared progress object: `{"n", "ok", "fail", "done"}`; a
  `dict[(chat_id, message_id)] -> Progress` on the dispatcher workflow data is enough (one bot
  process). `send_message` already returns `bool` → count `ok`/`fail` per user.
- Rate: 0.05 s sleep per message ≈ 20 msg/s, so 23 users ≈ 1–2 s; the widget's estimate is
  `max(1, round(n/15))` s («≈ 2 с на 30 человек»). Most broadcasts finish before the first
  `bg` tick: that is fine, the `go` answer may already carry `st:"done"` if the task finished
  within the 15 s answer window — but do not wait for it; answer at once and let `bg` report.
- `fail` wording «N заблокировали бота» covers TelegramForbiddenError; BadRequest (chat not
  found) is rare and counted the same.
- Keep the message's HTML text (`html.escape`) out of `tx`: the widget renders plain text.
- Deleting the draft message on cancel (today's behaviour) is possible instead of editing to
  `cancel`; the widget handles both (a deleted message simply disappears).
- Answer every query (`answerWidgetQuery`) within 15 s, including `bg`.
