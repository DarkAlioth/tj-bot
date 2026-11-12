# users — protocol (admin only; today's /users)

## chat_data (JSON, ≤ 1024 B UTF-8)

```json
{"v":1,"r":5,"t":1794487200,"g":"a","p":0,"n":3,"c":[23,19,3,1],
 "u":[[111111111,"Алексей","alexey",2,29908287,29332290,412,97],
      [555555555,"Spam Bot 3000","spambot3000",4,29903970,29903970,0,0]]}
```

| key | meaning |
|---|---|
| `v`, `r`, `t` | schema 1, revision (+1 per edit; answers carry it), snapshot unix seconds |
| `g` | tab: `a` все / `r` обычные / `m` админы / `b` блок (same groups as `repo._users_conditions`) |
| `p`, `n` | current page (0-based), page count (`ceil(count / 8)`, ≥ 1) |
| `c` | tab counts `[all, regular, admins, blocked]` — rendered on the chips |
| `u` | ≤ 8 rows, sorted by `last_seen desc` (as `list_users_page`): `[user_id, name ≤ 24, username ≤ 16 or null, flags, last_seen_min, first_seen_min, searches, downloads]` |

- `name` = `full_name or username or str(user_id)`, cut to 24 chars; `username` without `@`, cut to 16 chars, `null` when absent.
- `flags` bitmask: 1 admin (`BotUser.admin`), 2 super-admin (`config.is_super_admin`), 4 blocked. Super-admins have 2 (and 1 if also flagged).
- timestamps are **unix minutes** (`int(ts.timestamp() // 60)`) to save bytes; the widget renders «только что / N мин назад / сегодня 13:06 / вчера / 12.11.2026».
- `searches`, `downloads` = `user_activity_counts` (two COUNTs per row — 16 cheap queries per page; or one grouped query).

Byte budget (measured): typical page **593 B**; worst at the caps (8 rows × 24 Cyrillic-char names + 16-char usernames + 12-digit ids + 5-digit counts, 4-digit tab counts) **1019 B**. `fit()`: if over 1024, cut names to 20 chars (→ 955 B).

## Requests (ASCII ≤ 64 B; one in flight)

| verb | when | bot must |
|---|---|---|
| `t:<g>` | tab chip | page 0 of group `g` → answer the **full state** (same JSON as chat_data, `r`+1) and `editMessageWidgetData` with it |
| `pg:<g>:<n>` | pager ‹ › | page `n` of group `g` (clamp to `[0, pages-1]`) → full state + edit |
| `blk:<id>:<1|0>` | «Заблокировать» (after the inline «Заблокировать пользователя?» confirm) / «Разблокировать» | refuse super-admins and `from.id` itself with `{"err":"…"}`; `repo.set_blocked`; rebuild the **current** `g`/`p` page (the user may leave the group — then the row disappears and the counts change) → full state + `"m":"Заблокирован"` / `"Разблокирован"` + edit |
| `adm:<id>:<1|0>` | «В админы» / «Снять админа» (hidden for super-admins and for the viewer) | refuse super-admins/self; `repo.set_admin` (promote also unblocks, as today), update `config.dynamic_admins`, `set_user_commands(bot, uid, promote)`; full state + `"m":"Назначен админом"` / `"Снят с админа"` + edit |
| `act:<id>` | «Активность» | `get_user_searches(uid, 5)`, `get_user_downloads(uid, 5)` → `{"ok":1,"a":{"s":[[text ≤ 40, ts_min],…],"d":[[title ≤ 40, ts_min, "c"|"s"],…]}}` (`c` в чат, `s` на сервер). Not stored in chat_data. Worst case 986 B at 40 Cyrillic chars; cut titles to 40 |

The widget keeps the open row (accordion) across `blk`/`adm` answers and closes it on `t`/`pg`. Block button is hidden for admins (demote first, as in today's card); unblock is shown for blocked users; promote is shown for blocked users too (it unblocks).

## Answers
- Full state (`t`, `pg`, `blk`, `adm`): the exact chat_data document, plus optional `"m":"<toast ≤ 30 chars>"`. The widget applies it when `r` ≥ the newest seen, then also receives `chatDataChanged` (same `r`, harmless).
- `act`: `{"ok":1,"a":{…}}`.
- Errors: `{"err":"<≤ 40 chars>"}` → red toast in the header; rejections: NOT_ALLOWED «Откройте чат с ботом», BUSY «Подождите…», RATE_LIMITED «Слишком часто», TIMEOUT «Бот не ответил», FAILED/INVALID_ANSWER «Ошибка связи». The tapped button is disabled with a 3-dot pulse meanwhile; tab/pager taps show «Загружаю…».
- `TelegramWidget.viewer.id` is compared with row ids to hide self-actions; the bot must still enforce it.

## Caption
`👥 Пользователи: всего {all} · обычных {regular} · админов {admins} · в блоке {blocked}` (+ `· стр. {p+1}/{n}` when paged).

## Handler notes
- No background requests: the list changes only through the admin's own taps; the bot may still edit chat_data later (e.g. after a `/block` command) and the widget redraws.
- Validate every `id` against the stored page (or the DB) and every `g` against the four codes; clamp `n`.
- Keep `USERS_PAGE_SIZE = 8` for the widget (10 today); the old inline-keyboard path can stay at 10.
- Super-admin ids come from `.env` (`config.super_admins`); include them in the `m` group as `_users_conditions` already does.
