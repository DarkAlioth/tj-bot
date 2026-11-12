# hub — design notes

## What it is

Three tabs in one bubble. «Главная» = greeting («Привет, Алексей» from `viewer.first_name`),
the single instruction «Напишите /s и название» with a command sample and «Скопировать /s»
(`copyText('/s ')`), «Недавние запросы» chips (≤ 5, tap replays the search as a new message),
the favorites tile («★ 7 в избранном / 2 обновились на трекере», tap → tab), admin shortcuts
(Консоль / Панель / Пользователи) and the «Jackett ✓ · qBittorrent ✗ · 12:40» status line.
«История» = ≤ 10 one-line rows {query, count · relative time} + «Очистить историю» with inline
Да/Нет. «Избранное» = «Все N» / «Обновлённые M» chips, 4 rows per page {2-line title with the
floated «обновлено» accent badge, tracker chip, size · date · ⇡seeders}, tap → accordion with
Скачать / На сервер (admins) / На трекере ↗ / Убрать (inline confirm), pager `‹ 1 / 2 ›`.

## Decisions

- **Server-side formatting.** Sizes, dates and «N мин назад / 12:40 / 02.11» come from the bot
  as display strings (`f[].m`, `h[].a`). Tried widget-side `fmtSize/fmtDate/ago/fmtInt` first:
  with three tabs, the Russian copy and the error map the file was 4.3–4.9 KB compressed.
  Moving formatting to Python (where `format_size` already exists) cut ~250 bytes and the
  state grew by only ~10 bytes per row. BRIEF §2 lists the helpers as a convention; this widget
  documents the deviation here.
- **4 favorites per page, not 8.** A 2-line title + meta row is ~74 px; with tabs, filter chips,
  pager and the toast line, 5 rows reach 582 px at 320 px and the open accordion 617 px.
  4 rows: 518 px closed, 553 px with the accordion (measured in the harness).
- **Accordion focus mode.** When a row is open, the other rows clamp their title to one line
  (`.o .rw:not(.x) .tt{white-space:nowrap}`), which keeps the open page ≤ 560 px.
- **«обновлено» floated into the title** instead of a third chip in the meta row — at 320 px
  the meta row wrapped to a second line on updated rows (+24 px per row).
- **Lazy `fo:<id>` on first expand** instead of shipping `details_url` per row: a URL is
  40–60 bytes per row (4 rows ≈ 200 bytes of the 1024 budget) and `openLink` must be called
  synchronously in a tap — so the link is fetched once per row and cached in memory for the
  widget's lifetime; «На трекере ↗» appears only for `https://`.
- **Tabs are static markup**, the content container is replaced per draw (`replaceWith`) so the
  slide-in keyframe restarts without per-frame JS; direction follows tab order / page delta via
  the `--x` custom property (±24 px, muted color → text color, 220 ms, reduced-motion → none).
- **Pending state**: the tapped control's inner HTML becomes the 3-dot pulse (`.ds`), the whole
  body gets `pointer-events:none` (`.bz`) so nothing else can be tapped while one request is in
  flight; restored only if the element is still in the DOM.
- **Revision guard**: answers with `r` and `chatDataChanged` apply only when `r` ≥ / > the newest
  seen; the bot's post-answer edit (same `r`) is therefore a no-op redraw-free event.
- **No SVG icons.** A 6-symbol sprite cost ~450 compressed bytes; text labels and the ★ glyph
  read fine in Telegram's button style and keep the file under the limit. Chevrons are ‹ ›.
- **Rating pills (`im`/`kp`) dropped** — no data source today (BRIEF §5); the bot can append
  «· IMDb 7,8» to `f[].m` when it ever has them.

## Size budget

`widget.html`: 9 189 bytes raw, **4 045 bytes** zlib-9 (limit 4 096; the 3 900 target was not
reached — the three Russian tab views plus the mandatory error map are the floor; every
remaining byte is copy or a CSS rule the design system asks for). Nodes: ≤ ~120 per tab.
CSS rules: 47. Keyframes: 2. Heavy elements: 0 (disabled buttons are muted with color, not
opacity). Layout reads per draw: 0 (none; the slide uses a new node).

chat_data: see PROTOCOL.md (686 / 886 / 810 bytes for the three typical tabs; the handler
drops history rows past 1000 bytes).

## Heights (harness, 320 px, dark)

main user 443 px · main admin + status 438 px (2 chips) / ~500 px (5 chips) · history 10 rows
527 px (+ confirm row 549) · favorites 4 rows 518 px · accordion open 553 px · empty states
≈ 300 px. Nothing above 560.

## Known limitations / open points

- `f[].u` has no backing column today (favorites_watcher only notifies); needs
  `updated_at`/`seen_at` or an updates table.
- History relative times are the bot's snapshot: a widget re-created hours later still says
  «3 мин назад» until the next edit. Acceptable for a history list; the bot may re-edit the
  state when it next touches the message.
- `fo:` adds one round-trip before the action buttons appear (≈ 300 ms); the row shows the
  pending dots in its meta line meanwhile.
- No swipe between tabs (budget); tabs and pager buttons are the discoverable path.
- The harness only grows the iframe; real clients shrink the bubble when content gets shorter.

## Checklist against widget-rules.md

- No form controls, no iframes/images, no external resources, no `vh`, no `position:sticky`,
  no `-webkit-line-clamp` (2-line clamp = `max-height:2.5em;overflow:hidden`), no `:has()`,
  no `!important`, no nesting, no `text-shadow`, no `opacity<1`, no filters.
- Fonts: system default + `monospace` for the command sample only.
- Colors: only `--tg-theme-*` variables.
- `copyText` / `openLink` / `request` are called synchronously inside the click handler.
- Body padding keeps `max(12px, var(--tg-content-safe-area-inset-bottom))` clear; the toast line
  is centered and ends above the time band.
- Controls start 14 px from the left edge; nothing claims `touch-action`.
- `localStorage` not used (nothing per-viewer to remember).
- `TelegramWidget` guarded everywhere (`TW&&…`, try/catch around haptics and `ready()`).

## Files

- `widget.html`, `mock.json`, `PROTOCOL.md`, `NOTES.md` — deliverables.
- `gen/mock_gen.py` — regenerates `mock.json` and prints chat_data sizes (`python3 -I gen/mock_gen.py`).
- `gen/shot.js` — Playwright screenshot runner used for the harness checks.
- `preview/*.png` — harness screenshots (dark/light, 320–430 px).

## QA pass 2026-11-12 (device feedback: overflow, cramped screens, responsiveness)

- Overflow-proofing: every `<button>` is now `min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis`
  (padding 8 → 6 px so «Пользователи» fits the 3-column admin row at 320 px); the greeting (`.hi`) and the
  favorite titles (`.tt`) get `overflow-wrap:anywhere` (60-char titles without spaces wrap inside the 2-line
  box instead of running past the bubble). Recent-query chips are capped at `max-width:48%` (+ `box-sizing:
  border-box`, the padding used to push a 100 %-wide chip 20 px past the edge): a 40-char query shows ~18
  chars with an ellipsis and never takes a full row, so 5 chips are ≤ 3 rows (main tab 599 → 512 px at 320).
- Caption is now below the widget: nothing depends on the time band; `padding-bottom:max(12px, inset)` gives
  12 px with `cap=below`. Verified with and without the caption.
- Motion unchanged (tab/pager slide 220 ms with direction, 3-dot pending, `#t` toast line has a fixed
  `min-height` so toasts never shift the layout). Reduced motion already handled by selector.
- Stress scenarios added to `mock.json` (#7 main, #8 history, #9 favorites): 40-char queries (one without
  spaces), 5-digit counts, 1234 favorites («1 / 309»), 60-char titles without spaces. Heights at 320:
  main 512, history 527, favorites 518 / 543 with the accordion.
- Size: 9 326 B raw / **4 079 B** zlib-9.
