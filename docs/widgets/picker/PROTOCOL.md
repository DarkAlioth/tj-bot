# picker — protocol (bot ↔ widget)

Shown right after the torrent is added **stopped** to qBittorrent (`begin_torrent_send` /
`begin_magnet_send` in `handlers/server_download.py`). The selection itself still lives in
qBittorrent (file priorities), exactly as today: the bot rebuilds the view state from
`torrents/files` on every request, so a bot restart loses nothing.

## chat_data (≤ 1024 B)

```json
{"v":1,"r":3,"m":"pick",
 "n":"Дом Дракона / House of the Dragon [S02, 1–8 из 8] (2024) WEB-DL 1080p | Amedia, Eng",
 "c":40,"s":41282337,"k":40,"z":41282337,"fs":125829120,
 "p":0,"q":7,
 "cl":[["v",14,14],["a",8,8],["s",16,16],["o",2,2]],
 "d":[["Season 2",16,16],["Subs",16,16],["Extras",8,8]],
 "f":[[0,"S02E01.1080p.WEB-DL.Rus.Eng…",4453376,"v",1,0], "…≤ 6 rows"]}
```

| key | type | meaning |
|-----|------|---------|
| `v` | 1 | schema version |
| `r` | int | revision; +1 on every edit and every answer that changes the selection |
| `m` | `pick` \| `go` \| `x` \| `gone` | mode: picker / started (final compact view) / cancelled / torrent disappeared |
| `n` | str ≤ 90 | torrent name (widget clamps to 2 lines) |
| `c`, `s` | int | file count, total size **KiB** |
| `k`, `z` | int | selected count, selected size KiB |
| `fs` | int | free space on the save-path disk, KiB; `-1` = unknown (`free_space()` returned -1) |
| `p`, `q` | int | current page (0-based), page count; **page size 6** |
| `cl` | `[[cls,count,selected]]` | extension classes present (`v` video, `a` audio, `s` subs, `r` archive, `o` other), in that order, classes with 0 files omitted |
| `d` | `[[name ≤ 16, count, selected]]` | top-level folders, ≤ 6 entries (see truncation) |
| `f` | `[[index, name ≤ 28, sizeKiB, cls, sel 0/1, folderIdx]]` | rows of the current page only; `index` = qBittorrent file index; `folderIdx` = index into `d`, `-1` = file at the root (no header row) |

Rules the bot applies when building the state (`mock_gen.py` is the reference implementation):
- classes by extension: video `mkv mp4 avi m2ts ts mov wmv`, audio `flac mp3 aac ac3 dts m4a wav ape mka`,
  subs `srt ass sub idx sup`, archive `rar zip 7z iso`, else other;
- the torrent root folder (first path component shared by all files) is stripped; grouping is by the
  next component; files without a folder get `-1`;
- rows are **ordered by folder, then by name**, so a page crosses at most a few folders; folder header
  rows are drawn by the widget whenever the folder changes within the page;
- more than 6 folders → the first 5 stay, the rest are merged into «Другие папки» (index 5);
- folder names > 16 chars → 15 chars + `…`; file names: the longest common prefix inside a folder
  (≥ 3 files, prefix ≥ 6 chars, cut at the last `.`) is stripped, then the name is cut to 27 chars + `…`
  (the widget adds its own one-line ellipsis; the class icon replaces the extension);
- `n` is cut to 90 chars.

Byte budget (measured, `mock_gen.py` asserts ≤ 1024): header + totals ≈ 150 B, `cl` ≤ 60 B, `d` ≤ 6 × 30 B
= 180 B, `f` 6 rows × ≤ 65 B = 390 B → worst case ≈ 780 B (2000-file pack with 6 folders: 731 B).
Terminal modes (`go`, `x`, `gone`) carry only `v r m n` (≈ 130 B).

## Requests (1–64 B ASCII) and what the bot does

| request | bot action | answer |
|---------|-----------|--------|
| `tg:<index>` | toggle one file: read current priority, `set_file_priority(hash, index, 0 or 1)` | page fragment |
| `tf:<folderIdx>` | folder toggle: if every file of the folder is selected → priority 0 for all of them, else 1 for all (`set_file_priority(hash, "i1\|i2\|…", p)`) | page fragment |
| `qp:<cls>` | quick pick for a class (`v a s r o`): same all-or-nothing toggle over the files of that class | page fragment |
| `all` / `none` | priority 1 / 0 for every file | page fragment |
| `pg:<n>` | nothing in qBittorrent; answer with page `n` (clamped) | `{"r":<same>,"p":n,"f":[…]}` |
| `go` | same checks as today's `start_download`: ≥ 1 selected file else `{"err":"Выберите хотя бы один файл"}`; `start_torrents(hash)`, `record_download(...)`, then **send the progress widget as a new message** (`sendWidget` with the `progress` html, caption, chat_data snapshot) and start the watcher-equivalent (bg ticks drive it; see progress/PROTOCOL.md) | `{"r":<r+1>,"m":"go"}` and `editMessageWidgetData` with `{"v":1,"r":<r+1>,"m":"go","n":…}` |
| `x` | `delete_torrents(hash, delete_files=True)` (the torrent never started, nothing to keep) | `{"r":<r+1>,"m":"x"}` + edit chat_data to `m:"x"` |

Page fragment = `{"r":<r+1>,"p":<page>,"k":…,"z":…,"cl":[…],"d":[…],"f":[…]}` — the widget merges it
over its state (`Object.assign`) and redraws. Every bulk verb invalidates the widget's local page cache,
so the fragment must carry the **current** page (`p` as in the stored state).

Errors: `{"err":"<short Russian>"}` → toast in destructive color + haptic error; for `tg:` the widget
rolls the optimistic toggle back. Use the existing texts: «qBittorrent недоступен», «Загрузка не
найдена — возможно, удалена из qBittorrent» (then also edit chat_data to `m:"gone"`), «Выберите хотя бы
один файл». A rejected request (`FAILED`, `TIMEOUT`, …) is shown by the widget itself.

Revision rule: an answer without `r` (e.g. `{"ok":1}`) is applied as a no-op; a fragment with `r` < the
newest `r` seen is ignored; `chatDataChanged` with `r` ≥ newest replaces the whole state.

Rate limits: 5 requests / 5 s per user. The widget sends at most one at a time (others disabled).

## Caption template

`📤 <b>{name}</b> — выберите файлы · {k} из {c}, {size(z)} из {size(s)}` (edited to
`⬇️ Запущено: {name}` on `go`, `❌ Удалено из qBittorrent` on `x`). The caption is the fallback for
clients without widgets; keep the two old inline buttons («▶️ Начать», «❌ Отмена») under it only if a
non-widget client matters — the widget does not need them.

## Notes for the Python handler author

- `WidgetQuery` carries no state: keep `(chat_id, message_id) → (hash, tag, page, rev)` in a small
  table (`widget_messages`) so a restart keeps the picker alive; today's `Fs` callback_data carried the
  hash — the widget cannot.
- Build the state with one `torrent_files` + one `torrent_info` + one `free_space` call per request (as
  `render_selection` does), answer within 15 s, and `editMessageWidgetData` **after** answering (the
  widget applies the answer immediately and ignores an older `r` from the edit).
- `rev` must increase on every answer that changes the selection, including `tg`.
- Validate `index`/`folderIdx`/`cls`/`n` against the current file list (untrusted input).
- Admin check as in `_picker_context`; a non-admin tap → `{"err":"Недостаточно прав"}`.
