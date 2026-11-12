# users — notes

## Result
- `widget.html`: 8 141 B raw, **3 574 B zlib-9**.
- Heights at 320 px (harness, 22 px time band included): list of 8 + pager 538 px; row open 543 px (others condensed); block confirm 518 px; activity open 486 px (other rows + pager hidden); admins tab 280–328 px; empty 266 px. All ≤ 560.
- Verified in Chromium via the harness (`shots/`): dark/light, 320/360/430, tabs, pager, accordion, confirm, block → counts/badge update + toast, activity, self/super without toggles, blocked → unblock → empty group, error toasts (bot `err`, FAILED, TIMEOUT, NOT_ALLOWED), long names/usernames at the caps.

## Design decisions
- Tabs are chips with counts (`c`); the active one accent-colored; chips are `.75em` with 8 px padding so four fit in 292 px.
- Row = avatar (deterministic `hsl(id*137 % 360, 55%, 45%)`, white initials from the first two words), name with badges («супер»/«админ» accent chip, «блок» destructive chip), hint line `@username · last seen`, right column with two tiny SVG icons (search / download) and counts.
- Accordion: tapping a row opens its detail block (first seen · ID) with a 2-column action grid + full-width «Активность». While a row is open the other rows condense to 22 px avatars + name only (`.x .ur:not(.o)`), which keeps the page ≤ 560 px without losing the list. Block uses an inline confirm (question line + «Заблокировать» danger / «Отмена»).
- Activity (`act:<id>`) renders inline in the detail block (two headed lists with `dd.mm, hh:mm`), hides every other row and the pager (`.a`), and offers «Скрыть». It is not persisted in chat_data.
- Answers to actions are the full state, so the widget never patches rows itself; it keeps the open row if still present.
- Last-seen text is relative to the device clock, so a widget re-created days later still reads correctly.

## Tried / rejected
- Hiding the other rows entirely while a row is open: loses context when switching between users; condensed rows chosen instead.
- Three stacked full-width action buttons: +44 px, broke the 560 px cap with 8 rows; 2-column grid with short labels («В админы») chosen.
- Timestamps in seconds: +4 B per row; minutes keep the worst case under 1024 B.

## Known limitations
- Worst-case chat_data is 1019 B (5 B margin) — the bot's `fit()` must cut names to 20 chars when over; typical pages are ~600 B.
- Rows with a 24-char name and 5-digit counts truncate the name with an ellipsis at 320 px (by design).
- Mock last-seen values were generated relative to the time the mock was written; after a day the harness shows «вчера»/dates instead of «3 мин назад».

## Rules checklist (widget-rules.md)
- No form controls, no `:disabled`/`:focus`, no opacity/filter/blur (0 heavy elements), no `vh`, no `:has`, no line-clamp, no nesting, no fonts, no `img`; inline SVG uses only `symbol`/`use`/`circle`/`path`. 44 CSS rules, 1 `@keyframes`, ~120 nodes on a full page.
- `request()` is called synchronously in the click handler; one in flight; no background requests.
- `touch-action` auto everywhere; row taps are plain clicks; the leading 20 px hold only the avatar (not a control).
- Body padding keeps the time band clear; the bot sends a caption anyway.
