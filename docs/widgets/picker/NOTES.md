# picker — notes

## Size budget
`widget.html` 8 939 B raw / **3 995 B** zlib-9 (limit 4 096, aim 3 900). Both widgets had to be
trimmed hard: Russian strings are the expensive part (2 B/char, unique, incompressible). The
remaining ~100 B of headroom is reserved; anything added must remove something.

## Layout decisions
- Page size 6 (BRIEF says 8–10) — at 320 px the bubble with 8 rows was 634 px (clipped at 600).
  With 6 rows, a 2-line title, a wrapped capacity line and two rows of chips the widget is **524 px**
  at 320 px (≤ 560), 488 px at 430 px with a 2000-file pack.
- Three static containers (`#A` header+chips+heading, `#L` list, `#B` footer) so the list element
  keeps its transforms while its content is replaced; everything is re-rendered from state by
  `draw()` (≤ 60 nodes, no per-frame DOM work).
- Folder header rows are compact (24 px) because a page may cross several folders; the folder
  check is tri-state: full = accent box + check, partial = accent border, none = hint border.
- Chips carry counts («Видео 14») and light up when the whole class is selected — the toggle
  semantic (all-or-nothing) is the same for chips, folder rows and «Все»/«Ничего».
- Capacity line: «Выбрано k из c файлов · z из s · свободно fs»; the bar is `z / fs` (or `z / s`
  when `fs` is unknown); `z > fs` → destructive bar and «не хватает X».
- Terminal modes replace everything by the title plus one line («Запущено ✓ · прогресс ниже»,
  «Удалено из qBittorrent», «Нет в qBittorrent»).

## Interaction
- Optimistic toggle on `tg:` (row flips, `k`/`z` adjust), rollback on `{"err"}` or a rejected
  request; the tapped row shows pulsing dots inside its check box, the list gets
  `pointer-events:none`, every button is `[disabled]`.
- Paging: `‹ 1 / 7 ›` in the heading row; `pg:<n>` unless the page is cached locally (pages come
  only from answers; the cache is dropped on every bulk verb and on `chatDataChanged`). Slide
  animation 2 × 110 ms with `cubic-bezier(.2,.8,.2,1)`; reduced motion → 0 s via the media query.
- Haptics: `impactOccurred('light')` on taps, `selectionChanged` on page turns, `notificationOccurred`
  success/error on `go`/`x` outcomes and every failure.

## Tried and rejected
- Horizontal swipe to page (pointer events + `touch-action:pan-y`): implemented and removed to get
  under 4 096 B compressed (−280 B). The buttons are the discoverable path anyway; re-add it if the
  ERR map or the quick-pick counts are ever dropped.
- `-webkit-line-clamp` for the title: out of the profile → fixed-height box (`max-height:2.5em`).
- Native `<input type=checkbox>`: removed by the profile → custom 22 px rounded square + check path.
- Middle-ellipsis file names from the bot: double ellipsis with the CSS one-line ellipsis; now the
  bot cuts the head only and the class icon stands in for the extension.
- A dedicated «Список пуст» state: impossible after metadata (the bot never sends an empty page).
- 3-dot pulse as three animated `<i>`: kept here (the picker has the bytes), the progress widget
  uses a blinking «•••» instead.

## Known limitations
- `fs` is the free space of the default save path; a category with its own path may differ
  (same as today).
- At 320 px the file name column shows ~22 characters; the bot's prefix stripping keeps the
  episode token visible. `S01E05` tokens are bolded by the widget.
- The harness keeps the iframe height after the content shrinks (`go`/`x`); real clients resize.

## Verification
- Size: `python3 -c 'import sys,zlib;b=open(sys.argv[1],"rb").read();print(len(b),len(zlib.compress(b,9)))' widget.html`.
- Browser: no Playwright MCP was available; `_test/cdp.mjs` drives the Playwright-cached headless
  Chromium over CDP (`node _test/cdp.mjs "<harness url>" wait:300 shot:x.png "click:[data-a=nx]" log`).
  Screenshots in `shots/` (dark/light, 320/360/430, pending, page 2, confirm, errors, terminal views).
- Profile audit (grep for out-of-profile tokens): clean; 52 CSS rules, 1 keyframes; no opacity,
  filters, images, inputs or focus handling.

## QA pass 2026-11-12

- Overflow-proofing: title box gets `overflow-wrap:anywhere` (80-char names without spaces wrap inside the
  2-line clamp); chips are `white-space:nowrap` (never break inside a chip, the row wraps instead); the
  heading label (`.hd b`) is a one-line ellipsis.
- Height: the stress page (5 class chips, 3 folder headers between 6 files, 2-line capacity line, «не
  хватает») was 620 px at 320. Chips are now 24 px / `.75em` / 8 px padding with 5 px gaps (7 chips fit in two
  rows at 320), and the toast moved from its own line into the «Файлы» heading label (`.tm`, same row as
  the pager, so it never changes the height); card/heading/footer margins 10 → 8 / 6 px and rows 4 → 3 px
  padding → 549 px at 320 with the 22 px time band (539 with a caption below), 503 px for the typical scenario.
- Reduced motion: the old `*{transition-duration:0s}` lost to the class selectors; now
  `*{transition:none!important;animation:none!important}`.
- `mock.json`: top-level `caption` (needed for `cap=below` in the harness) and scenario #8 «Стресс» with
  raw 80-char file names, deep folder paths, 5 classes, page 124 / 334 and a free-space deficit. The raw
  names deliberately exceed the protocol caps (28 / 16 / 90) to prove the CSS; the caps stay as they are
  (`mock_gen.py` does not emit this scenario — it is hand-written in `mock.json`).
- Size: 8 988 B raw / **3 995 B** zlib-9.
