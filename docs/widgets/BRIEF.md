# tj-bot → Telegram Widgets redesign — shared brief for all agents

Read this file completely before doing anything. It is the only contract between the agents
building the widgets, the backend shim and the showroom. Every rule here is binding.

Working root (all paths below are relative to it):
`/tmp/claude-1000/-home-sony-vault-01-Projects/bb9af73f-f1a9-4b9c-a15a-3680f1a5545f/scratchpad`

Files you must read first:
- `widget-rules.md` — the official Telegram widget rules (what works, what is removed, limits,
  the `TelegramWidget` JS API). Verbatim from Telegram. Anything out of the profile is silently
  removed by the clients, so a violation is a bug you will not see in a browser.
- `widgets.txt` — the full Telegram Widgets guide (plain text, 2000 lines) for details:
  gestures, the time in the corner, state/requests semantics, lifecycle, limits. Grep it when
  in doubt (`grep -n -i "touch-action" widgets.txt`).
- `harness.html` + `http://127.0.0.1:8765/harness.html?w=<name>&s=<scenario>&t=light|dark` —
  the preview stand (see "Preview stand" below).

Bot source (read-only reference, the live checkout, do not modify it):
`/home/sony/projects/TJ_BOT/src/tj_bot/` — aiogram 3 bot, handlers in `handlers/`, data access in
`db/repo.py`, models in `db/models.py`, qBittorrent client in `services/qbittorrent.py`, Jackett
client in `services/jackett.py`, text formatting helpers in `services/formatting.py`.

## 1. What the product is

tj-bot (`@tor_search_bot`) is a private, family-use Telegram bot: torrent search across 7 Jackett
indexers (RuTracker, RuTor, MegaPeer, NoNaMe Club, Tapochek, PC-torrent, Byrutor), results cached
in PostgreSQL, `.torrent` download into the chat, favorites with tracker-side update notifications,
search history. Admins additionally get: send-to-server into qBittorrent with a file picker, live
download progress, a full qBittorrent console, health/stats/indexers dashboards, user management
(block/promote/activity) and broadcasts. Everything today is text messages + inline keyboards.

Goal of the redesign: rebuild the whole UI on Telegram Widgets (Bot API 10.4, Nov 2026): rich
interactive cards drawn inside the message bubble, with animated paging and interactions, every
piece of information the data gives us, and the same quality bar for the admin tooling (file
picker, download progress, console, dashboards, users). UI language: **Russian**. Code, ids,
comments, protocol keys: **English**.

Hard platform facts that shape every design (details in `widget-rules.md`):
- Widgets work in production **only in the private chat with the bot** (that is where this bot is
  used). Forwards, groups and inline mode are test-server only: design for the private chat.
- HTML ≤ 32768 bytes and **≤ 4096 bytes after `zlib` level 9**. `data:` images do not compress:
  **no bitmap images in widgets**. Visuals are CSS, inline SVG and canvas.
- The HTML is fixed at send time. Only the bot changes state: `chat_data` (≤ 1024 bytes UTF-8).
  Requests to the bot: 1–64 bytes, one per tap; answers ≤ 1024 bytes. Background requests
  (no tap) only in the private chat: one when the widget appears, then ≤ 1 per 5 s while visible.
- No form controls (`input`, `select`, `textarea` are removed), no keyboard, no vertical drags
  (the chat scrolls), no scrolling inside the widget, bubble 200–600 px wide (design for 320,
  check 360 and 430), height ≤ 600 px (keep ≤ 560), clipped beyond.
- One `<button>` with no CSS already looks native; use theme CSS variables for every color.
- Nodes ≤ 2000, CSS rules ≤ 400, ≤ 16 "heavy" elements (blur/filter/opacity<1/animated ones)
  at once, CPU 300 ms per second. Animate with CSS transitions/keyframes, not per-frame DOM.

## 2. Design system (shared by every widget — the widgets must look like one family)

Layout
- `body{margin:0;padding:12px 14px}` as the base inset. Keep the bottom band clear with
  `padding-bottom:max(12px,env(safe-area-inset-bottom))` (the message time is drawn there when
  there is no caption below; we send widgets with a caption, which may be above the media).
- Keep the leading 20 px (left edge) free of controls that claim gestures (system back gesture).
- Stack: header row → hero/content → stat tiles → actions → footer meta. Sections separated by
  12 px gaps, never by borders unless it is a list.
- Cards and tiles: `background:var(--tg-theme-secondary-bg-color);border-radius:12px;padding:10px 12px`.
- Chips/badges: `display:inline-flex;align-items:center;gap:4px;padding:2px 8px;border-radius:999px;
  font-size:.8em;font-weight:600;background:var(--tg-theme-secondary-bg-color);color:var(--tg-theme-subtitle-text-color)`.
  Accent chip: `background:var(--tg-theme-button-color);color:var(--tg-theme-button-text-color)`.
- Tabs / segmented control: a row of chips, the active one accent-colored; `touch-action:pan-y`
  on a horizontally scrollable… no — nothing scrolls. Keep ≤ 4 tabs visible; overflow goes to a
  "…" chip that opens a sheet.
- Lists: rows of `padding:8px 0` with a 1 px `--tg-theme-section-separator-color` line between
  rows, ≤ 8 rows per page, pager chips at the bottom (`‹ 1 / 12 ›`).

Typography (system fonts only; sizes in em relative to the chat text size)
- Title: `font-size:1.05em;font-weight:650;line-height:1.25` (2 lines max, then ellipsis via a
  fixed-height box + `overflow:hidden` — `-webkit-line-clamp` is removed by the profile).
- Meta: `font-size:.85em;color:var(--tg-theme-hint-color)`.
- Numbers: `font-variant-numeric:tabular-nums` on every counter/speed/size.
- Headings inside widgets: `font-size:.75em;letter-spacing:.04em;text-transform:uppercase;
  color:var(--tg-theme-hint-color);font-weight:600`.

Color: ONLY the `--tg-theme-*` variables (see `widget-rules.md`). Semantic colors derived from
them: positive = `--tg-theme-accent-text-color`, danger = `--tg-theme-destructive-text-color`,
muted = `--tg-theme-hint-color`. A health/progress hue may use a fixed hsl ramp (green 140 →
amber 40 → red 0) because it carries meaning, but never for surfaces or text. Mute with color,
not opacity (opacity < 1 counts as a heavy element).

Buttons
- Primary: a plain `<button>` (native style). Full width when it is the one main action.
- Secondary/ghost: `<button class="ghost">` with
  `background:var(--tg-theme-secondary-bg-color);color:var(--tg-theme-text-color)`.
- Destructive: `class="danger"` → text in `--tg-theme-destructive-text-color`, ghost background.
- Icon buttons: ≥ 40×40 px tap target, SVG icon 18 px, no emoji in buttons (emoji are fine in
  text). Use inline SVG icons (stroke 1.75, `currentColor`), ≤ 12 distinct icons per widget,
  defined once in `<svg><symbol id=…>` and reused with `<use href="#id">`.
- Pressed feedback: `button:active{transform:scale(.97)}` (transition 120 ms). Disabled while a
  request is in flight (`[disabled]`).

Motion (respect `prefers-reduced-motion: reduce` → set all durations to 0 ms)
- Paging: horizontal slide, 220 ms `cubic-bezier(.2,.8,.2,1)`, old card out / new card in
  (two layers; translateX ±24 px with the muted color, not opacity; or a single layer that slides
  and gets its content swapped at the midpoint). Direction follows the tap (next → from right).
- Horizontal swipe to page: a `touch-action:pan-y` surface on the card body; pointer events
  track the drag (≥ 10 px starts it, ≥ 56 px or fast flick commits), rubber-band at the ends,
  `pointercancel` resets. Buttons stay as the discoverable path; swipe is the delight.
- Numbers: count-up 300 ms on change (requestAnimationFrame, ≤ 20 steps).
- Progress rings/bars: SVG `stroke-dashoffset` transition 400 ms.
- Entrance: none (draw the first state synchronously; nothing fades in).
- Haptics: `TelegramWidget.HapticFeedback.selectionChanged()` on page change,
  `impactOccurred('light')` on toggles, `notificationOccurred('success'|'error')` on request
  outcomes. Guard every call with `typeof` checks (the stub has them, old clients may not).

Feedback states (every widget)
- Pending: the tapped button shows a 3-dot pulse (`.dots` with three spans animated by
  `@keyframes`) and is disabled; the rest stays interactive only if the protocol allows (one
  request in flight at a time — queue nothing, just disable the others).
- Success: brief check/toast line in the footer (`.toast`, 1.6 s, then cleared).
- Error: toast in `--tg-theme-destructive-text-color` mapping `WidgetRequestError.code` to
  Russian: NOT_ALLOWED → «Откройте чат с ботом», BUSY → «Подождите…», RATE_LIMITED → «Слишком
  часто», TIMEOUT → «Бот не ответил», FAILED → «Ошибка связи», INVALID_ANSWER → «Ошибка ответа».
  Also show the bot's own error text when the answer is `{"err":"…"}`.
- Stale: if the widget's state says the data is older than N seconds (see protocol), draw the
  meta line in hint color with «обновлено 12:40» and offer a refresh control.

Text (Russian, concise, no emoji walls)
- Use typographic quotes «», en dash –, thin spaces in numbers (`1 234`), units: ГБ/МБ/КБ,
  speeds `12,4 МБ/с`, ETA `1 ч 12 м`, dates `12.11.2026`, relative «3 мин назад» only for ≤ 1 ч.
- Formatting helpers inside widgets (write them once per widget, small): `fmtSize(bytes)`,
  `fmtSpeed(bps)`, `fmtEta(sec)`, `fmtInt(n)` (thin-space thousands), `fmtDate(iso)`.

## 3. Protocol conventions (bot ↔ widget)

State in `chat_data` (JSON, ≤ 1024 bytes): the full "view state" needed to redraw the widget
from scratch (the client destroys and re-creates widgets at will). Keep keys 1–2 chars. Always
include `"v":1` (schema version) and `"r":<int>` (revision, increases on every edit). Include
`"t":<unix seconds>` of the data snapshot when freshness matters.

Requests (`TelegramWidget.request(data)`, 1–64 bytes ASCII): `verb` or `verb:arg[:arg]`, e.g.
`n` (next), `p` (prev), `g:17` (go to index), `s:sz` (sort), `f:120` (filter code), `d` (download
.torrent), `fav`, `srv` (send to server), `rf` (refresh), `tg:3` (toggle file 3), `all`, `none`,
`go`, `x` (cancel), `pa` (pause), `re` (resume), `del:1` (delete with data), `blk:123`, `adm:123`,
`bg` (background refresh tick). Document every verb your widget sends in `PROTOCOL.md`.

Answers (`answer.result`, ≤ 1024 bytes): JSON. Either the new state fragment the widget applies
immediately (same shape as the relevant part of `chat_data`, with the new `r`), or `{"ok":1}`,
or `{"err":"<short Russian text>"}`. The widget applies an answer at once (animation), and also
listens to `chatDataChanged` (the bot edits `chat_data` after answering) — apply only if the
incoming `r` ≥ the newest `r` seen; otherwise ignore (stale).

Background refresh (private chat only): a widget that shows live data (download progress,
console, dashboard) calls `request('bg')` once at the start of its script (it is sent when the
widget appears) and then every ≥ 5 s while `TelegramWidget.isActive` (use `activated` /
`deactivated` events to start/stop the timer; never faster than 5 s — RATE_LIMITED otherwise).
The answer carries the fresh snapshot; nothing is edited in `chat_data` for ticks (no flood
limits), the bot edits `chat_data` only on real state changes (so a re-created widget starts
from something recent). Pure-static widgets (search card) make no background requests.

Fallback: the bot always sends a `caption` with the key facts (apps without widget support show
the caption instead of the widget) and may keep a minimal inline keyboard under the widget. Do
not design the widget to depend on that keyboard.

## 4. Data available per screen (what the bot can put into state/answers)

Torrent (search result / favorite): `hash` (40 hex, use the first 8 in requests), `title`
(≤ 300 chars, may contain Russian + English parts «Дюна: Часть вторая / Dune: Part Two (2024)
UHD BDRemux 2160p | 4K | HDR10 | Dolby Vision | D, P, A»), `uploader`, `description` (≤ 500
chars), `category` (Torznab: «Movies/HD», «TV/HD», «PC/Games», «Audio/MP3», «Books»…),
`tracker` (one of the 7 names), `details_url`, `seeders`, `peers`, `published_at` (date), `size`
(bytes). Optional enrichment the bot can compute server-side: quality chips parsed from the
title (`2160p|1080p|720p`, `HDR10|Dolby Vision|HDR`, `BDRemux|BDRip|WEB-DL|WEBRip|HDTV`,
`x265|HEVC|AV1|H.264`, `Dub|MVO|Sub`, `S01E05`, year), the Russian/English title split, a 4×6
ambient color mosaic (24 colors, 144 hex chars) when a poster could be fetched (see §5),
`grabs`/`freeleech` where the tracker gives them, and `is_fav`, `from_cache`, `is_admin`
(show «На сервер»).
Search context: `qh` (query hash, 32 hex → send the first 8), `query_text`, `index` (0-based),
`total`, `sort` ∈ {se, sz, za, dt, da} («Сиды ↓», «Размер ↓», «Размер ↑», «Дата ↓», «Дата ↑»),
filters code `"<seeders><size><date>"` with buckets seeders [любые, 1+, 10+, 50+], size [любой,
< 1 ГБ, 1–5 ГБ, 5–20 ГБ, > 20 ГБ], date [любая, неделя, месяц, год], `category` filter (token or
ALL) plus the list of categories with counts (≤ 8 shown; names ≤ 20 chars), `from_cache`.
List mode: 10 rows of {index, title, size, date, seeders, tracker}.

qBittorrent torrent (`torrents/info`): `hash`, `name`, `state` (downloading, stalledDL, metaDL,
forcedDL, queuedDL, checkingDL, allocating, uploading, stalledUP, forcedUP, queuedUP, checkingUP,
stoppedDL, stoppedUP, pausedDL, pausedUP, error, missingFiles), `progress` (0–1), `size`,
`total_size`, `dlspeed`, `upspeed`, `eta` (s, 8640000 = ∞), `num_seeds`, `num_leechs`,
`num_complete`, `num_incomplete`, `ratio`, `category`, `added_on`, `completion_on`, `downloaded`,
`uploaded`, `priority`, `save_path`, `seq_dl`, `f_l_piece_prio`. Transfer (`transfer/info`):
`dl_info_speed`, `up_info_speed`, `dl_info_data`, `up_info_data`, `dht_nodes`,
`connection_status`, plus `free_space` (bytes) and `alt_speed` (bool). Actions available in the
bot: stop/start (pause/resume), force start on/off, queue priority (top/up/down/bottom), delete
(keep/with data), global stop/start all, alt-speed toggle, set file priority, add tags.
Torrent files (`torrents/files`): `index`, `name` (path with folders, may be long), `size`,
`progress`, `priority` (0 = skip, 1 = normal, 6 = high, 7 = max). A torrent may have 1…2000+
files: the picker pages (8–10 per page) and groups by top folder.
Russian labels for states already exist in `services/formatting.py` (`STATE_VIEW`): reuse the
words.

Health: `uptime` (s), `db_size` (bytes), `disk_free` (bot host bytes), qbit summary {active,
total, dl, up, free}, indexers list {id, name, ok, error (≤ 120 chars), results} (7 items).
Stats: `torrents` cached, `queries`, `events_window`, `users_window` (7-day window), `top`
(≤ 10 {text, count}), optionally per-day counts for 7 days (sparkline).
Users: {user_id, username, full_name, blocked, admin, super_admin, first_seen, last_seen,
searches, downloads}; tabs: all / regular / admins / blocked, 10 per page; actions block/unblock,
promote/demote (super-admins never changeable); activity: last 10 searches and downloads
{text|title, kind, ts}. Broadcast: there is no text input in widgets → the admin types the
message in the chat (`/broadcast text`); the widget is the preview + confirm/cancel + delivery
report (delivered / total, progress).
History (per user): ≤ 10 recent {query_id, text, ts}. Favorites: ≤ 10 per page {id, title,
tracker, size, published_at, seeders, category, updated (bool: changed on the tracker since
saved)}, actions download / send-to-server (admins) / remove.

## 5. Posters (do not block on this)

The 7 trackers give no `poster` via Jackett (verified: 0 of 302 results; only PC-torrent and
Byrutor give `coverurl` through Jackett's image proxy). A separate agent researches poster
sources (tracker page scraping, TMDB by parsed title). Even when a poster exists it cannot be
embedded in the widget (size limit). The search card therefore has two visual modes, both must
look finished:
- `m` present (144 hex chars = 4 columns × 6 rows of `rrggbb`): draw an **ambient mosaic**
  backdrop — a 4×6 grid of colored cells (divs or SVG rects) under a single `filter:blur(18px)`
  container (1 heavy element), with a bottom gradient to the bubble color so text stays legible.
- `m` absent: a **category-driven gradient** backdrop (Movies → deep indigo/violet, TV → teal,
  Games → amber/orange, Audio → magenta, Books → olive, other → graphite), still from hsl() so
  both themes work, plus big typographic treatment.
The full-size poster, when available, is sent by the bot as a separate photo message on
request (`img`), so the card may show a «Постер» ghost button only when `has_poster` is true.

## 6. Deliverable format for a widget agent

Create `widgets/<name>/` with:
- `widget.html` — the widget: a fragment or full document, inline `<style>` and `<script>`, no
  external anything, **no `window.TelegramWidget` stub inside** (the harness/bot provides it),
  guarded for `window.TelegramWidget` being undefined. Must pass:
  `python3 -c 'import sys,zlib;b=open(sys.argv[1],"rb").read();print(len(b),len(zlib.compress(b,9)))' widgets/<name>/widget.html`
  → second number ≤ 4096 (aim ≤ 3900) and first ≤ 32768. Minify by hand (short class names,
  no comments, no dead code) — do not use a minifier that may emit out-of-profile CSS.
- `mock.json` — `{"scenarios":[{"name":"…","chatData":{…},"publicData":null,"answers":{"<request
  data>":{…result JSON…}, "re:^n$": {…}}}]}`. ≥ 4 scenarios covering: typical, long title /
  many items, empty/edge, error. Answers keyed by exact request string or by `re:<regex>`; an
  answer object may include `"chatData":{…}` to simulate the bot editing the state after
  answering, and `"delay":<ms>`. A background `bg` answer may be a list `["…","…"]` cycled per tick.
- `PROTOCOL.md` — the chat_data schema with byte budget estimate, every request verb, every
  answer shape, error cases, what the bot must do on each verb (one line each), the caption text
  template, and notes for the Python handler author.
- `NOTES.md` — design decisions, what was tried/rejected, known limitations, size budget.

Preview stand: `python3 -m http.server 8765` is already serving the working root. Open
`http://127.0.0.1:8765/harness.html?w=<name>&s=<scenario index>&t=dark&wd=360` with a browser
tool if you have one (Playwright MCP `browser_navigate` + `browser_take_screenshot`, or the
`mcp__cloudcli-browser__*` tools), and inspect both themes and widths 320/360/430. The harness
shows a phone-like chat with the bubble, logs every request to the panel below, answers from
`mock.json`, and fires `chatDataChanged` when an answer carries `chatData`. If no browser tool
works, validate by reading your code against `widget-rules.md` twice and keep a checklist in
`NOTES.md`.

Quality bar: this must look like a product designed by a strong team, not a demo. No lorem,
no placeholder boxes, no emoji-as-icon in controls, consistent spacing, every state drawn
(loading/pending/error/empty/stale), both themes, 320 px width, Russian copy proofread.

## 7. Backend facts for the shim/handler author

- aiogram 3.31.0 is the latest release and supports Bot API 10.3; Bot API 10.4 (widgets) is in
  open PR aiogram/aiogram#2004. The bot pins `aiogram>=3.30.0` (lock has 3.30.0).
- `TelegramObject` uses `extra="allow"`, so `Update` parsed from `getUpdates` keeps `widget_query`
  as an extra attribute; `Update.event_type` raises `UpdateTypeLookupError` → the dispatcher
  warns and `SkipHandler`s. Routing must therefore intercept at `dp.update` outer-middleware
  level before `_listen_update`, and `start_polling(allowed_updates=[...])` must list
  `widget_query` explicitly (the auto-resolved list will not include it).
- Custom methods: subclass `aiogram.methods.base.TelegramMethod[T]` with `__returning__` and
  `__api_method__`, pydantic fields matching the Bot API parameter names.
- Bot API 10.4 widget surface: methods `sendWidget` (chat_id, html, allow_queries, is_shared,
  public_data, chat_data, width, height, caption, parse_mode, caption_entities,
  show_caption_above_media, disable_notification, protect_content, message_thread_id,
  reply_parameters, reply_markup, message_effect_id, business_connection_id,
  allow_paid_broadcast), `sendSharedWidget` (widget_id, chat_data, caption…),
  `editMessageWidgetData` (chat_id+message_id | inline_message_id, chat_data, public_data),
  `editSharedWidgetData` (widget_id, public_data, cache_time), `answerWidgetQuery`
  (widget_query_id, data). Types: `Widget` {html, width, height, public_data, chat_data, id},
  `Message.widget`, `WidgetQuery` {id, from, chat, message_id, inline_message_id, chat_instance,
  chat_type, widget_id, type (WidgetQueryType: data | sign_data | sent_transaction), data},
  `SharedWidgetQuery` {id, from, widget_id, type, data}, `Update.widget_query`,
  `Update.shared_widget_query`. Verify names against https://core.telegram.org/bots/api
  (section "Widgets") before writing them — the page is the source of truth.
