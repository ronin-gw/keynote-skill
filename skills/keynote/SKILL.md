---
name: keynote
license: MIT
compatibility: "macOS. Needs Python 3 with keynote-parser and Pillow (pip install keynote-parser Pillow). Rendering and structural edits drive the Keynote app (14.x or 15+) via AppleScript; reading and direct edits work without it."
description: "Reads, creates, edits and renders Apple Keynote (.key) files natively on macOS. Use whenever a .key file is the input or the output, or the user mentions Keynote or wants slides 'in Keynote': extracting text, tables, chart data or speaker notes (even when the content is used elsewhere, such as a summary or an email); rendering slides to images or PDF; building a new deck; restyling or updating an existing one; adding, duplicating, deleting, reordering or merging slides; using Keynote themes and slide layouts; converting a .pptx to .key. Never round-trip a Keynote deck through .pptx."
---

# Keynote (.key) creation, editing, and analysis

A `.key` is a ZIP (sometimes a folder) of `Index/*.iwa` files: Snappy-compressed Protobuf objects that point at each other by numeric id. There are two ways in, and most jobs use both:

- **The file, directly** (`keynote_lib.py` and the scripts built on it). No Keynote needed. Reads everything; edits text and styling. Never changes the deck's file format version.
- **The Keynote app, by AppleScript** (`keynote_app.py`). The only renderer, and the only safe way to change structure: slides, objects, tables, images, charts.

## Scripts

`scripts/` is in this skill's directory — run each as `python3 <skill dir>/scripts/<name>.py …`, by absolute path. They need `keynote-parser` (and `Pillow` for contact sheets): `pip3 install keynote-parser Pillow`. Every script has `--help`; the spec and ops formats are documented there in full.

| Task | Command | Needs Keynote |
|---|---|---|
| **Read** content | `key_text.py deck.key [--slides 2,5-7] [--json] [--styles] [--layouts] [--no-skipped]` — markdown, one block per slide under `<!-- Slide number: N -->` (the marker also gives the layout index and name): titles, bullets indented by level, text boxes, tables, chart data, image file names + alt text, notes. `--json` adds each object's `item` number, `id`, `kind`, frame and bullet `levels` — what the edit tools address; `--styles` adds fill, outline, font, size, color, alignment and slide background; `--layouts` replaces the slide dump with the theme's layout list (index, name, placeholders) and prints nothing else | no |
| **Look** at slides | `key_render.py deck.key out/ [--slides …] [--jpeg]` → `out/slide-NNN.png`, numbered by deck position. `--grid f.jpg` contact sheet; `--layouts f.jpg` one sample slide per layout; `--pdf f.pdf [--notes]` | yes |
| **Create** a deck | `key_build.py spec.json out.key`. `--themes` lists themes; `--layouts THEME_OR_DECK` lists layouts with their roles and placeholder frames | yes |
| **Change text or styling** | `key_edit.py deck.key -o out.key --replace A B` / `--ops ops.json` — text, notes, bullet levels, fill, outline, shape, shadow, alignment, font, color, frame, stacking order, slide background, chart data and options | no |
| **Change structure** — slides, objects, tables, images | `keynote_app.py --for-file out.key run script.applescript [args]`. The failing line is shown. A document the script names and leaves open is closed (unsaved if the script failed, otherwise only when it has no unsaved changes; `--keep-open` to leave it) | yes |
| **Merge** decks | `key_merge.py base.key -o out.key --take other.key:3,7 [--take …] [--after N]` — whole slides into a copy of base.key, still editable, positions kept, sources untouched | yes |
| **Import** a .pptx | `key_import.py deck.pptx out.key [--app N]` — Keynote's importer driven safely, PowerPoint line spacing restored, and a report of what was lost | yes |
| **Verify** a file | `key_check.py out.key [--original src.key] [--open] [--layout]` — structural check; prints the file format version. `--open` has Keynote open it and says which Keynote did; `--layout` lists objects past the slide edge, overlapping text boxes and table rows too short for their text | only `--open` / `--layout` |
| Inspect the app | `keynote_app.py info` (installed Keynotes, open documents) / `fonts [word]` (installed PostScript font names) / `quit` | yes |
| Anything the scripts do not cover | `keynote_lib.py`, the Python library under all of the above | no |

**Read the matching reference in full before the work it covers.** Keynote fails silently, and each line in these files is a failure seen in practice.

| Before you… | Read |
|---|---|
| write a build spec | [references/creating.md](references/creating.md) |
| write `key_edit.py` ops or script `keynote_lib` | [references/editing.md](references/editing.md) |
| write AppleScript (slides, objects, tables, pictures, charts) | [references/applescript.md](references/applescript.md) |
| import a .pptx | [references/importing.md](references/importing.md) |

## Before anything else: which Keynote, and is the deck open?

Run `python3 scripts/keynote_app.py info --for-file deck.key` (plain `info` when creating a new deck) once at the start of any job that will use the app. It lists the installed Keynotes, stars the one that will be driven, and shows what each has open.

- **Two different apps can be installed side by side**: Keynote 15+ ("Keynote Creator Studio", bundle id `com.apple.Keynote`) and Keynote 14.x or earlier (`com.apple.iWork.Keynote`). Their names collide, so always address the app by id — in scripts write `{{APP}}` and let `keynote_app.py run` fill it in. Never write `tell application "Keynote"`.
- **Saving in a newer Keynote silently upgrades the file format, and the older Keynote can then no longer open the deck** (Keynote 15 writes format 26.x with no warning). The scripts guard against this:
  - `key_render.py`, `key_check.py --open` and `key_build.py` with a `"template"` drive the *oldest* installed Keynote that can open the deck.
  - **`keynote_app.py run` only does so when you pass `--for-file deck.key`** — without it, it drives the newest Keynote and a `save` upgrades the deck.
  - A brand-new deck (`key_build.py` with a `"theme"`, `key_import.py`) is made by the *oldest* installed Keynote so that every installed Keynote can open it; pass `--app 15` only when the user wants the newest app's format.
  - `--app N` or `KEYNOTE_APP=N` forces a version everywhere. Direct edits never change the format.
  - To confirm a deck was not upgraded, compare the "file format" that `key_check.py` prints before and after.
- **A deck that is open in Keynote must not be edited directly**: Keynote does not notice the file changed and its next save discards your edit. `key_edit.py` refuses; either go through AppleScript (which edits the open document — then `save` it), or ask the user to close it.
- **Never touch the user's original unless asked to edit it in place.** Write to a new path; for an in-place request use `key_edit.py --in-place` (keeps `name.backup.key`).
- **Keynote may be shared** with the user or another job, and it serves one request at a time. Touch only documents you opened by path, and keep work files in a directory of your own (not a bare `/tmp/w`). If a call times out, retry once; if it times out again, a dialog is waiting in Keynote (first launch, missing fonts, a macOS Automation permission prompt) — say so rather than retrying in a loop. The timeout error and `keynote_app.py info` show the dialog's text when the terminal has Accessibility permission, and otherwise say it could not be checked.
- **Never hand Keynote a path that does not exist**: it answers with a modal "can't be opened" alert that stalls every job until a person dismisses it. `keynote_app.py` and `openDeck` refuse such a path up front; check paths in anything you write yourself.

## Reading

```bash
python3 scripts/key_text.py deck.key                             # everything
python3 scripts/key_text.py deck.key --slides 3 --json           # objects on slide 3, with item numbers and frames
python3 scripts/key_render.py deck.key --grid deck-thumbs.jpg    # then view the image
```

- **The text dump is not what the audience sees.** What a picture or pasted chart shows, colors, emphasis outside tables, overlaps, whether text is actually visible — all need a render. When the question is about what the audience sees, or a slide is mostly pictures (`![alt](file)` lines and little else), render and look before answering.
- Skipped slides are included and marked `skipped`.
- A title that is switched off for a slide still has (often stale) text in the file: it is reported as an HTML comment, not a `#` heading — do not present it as slide content.
- Table cells read as displayed (`1,234,567`, `45.60%`), cells bolded by hand as `**bold**`, a table's visible name as a `Table: NAME` line. A table saved before 2019 and never re-saved reads as `[table: …]` — read it through the app instead (`value of cell` in AppleScript).
- A table's frame is its drawn size (columns × rows). When the table's name is shown, the frame starts at the name band and `caption_h` gives the band's height (`null`: unknown, and `h` covers the grid only).
- Frames are in points from the slide's top-left; the slide size is in the `--json` header (commonly 1920×1080 or 1024×768). A frame with `w`/`h` of 0 is an auto-sized text box (common in decks imported from PowerPoint); only a render shows its real extent.
- Link targets behind text are not extracted.

## Creating a deck

Write a JSON spec, build, render, look, fix, rebuild. Building takes seconds, so iterate on the spec rather than patching the output. The spec format, a worked example and its footguns are in [references/creating.md](references/creating.md).

```bash
python3 scripts/key_build.py spec.json out.key
python3 scripts/key_render.py out.key qa/ --jpeg
```

## Editing an existing deck

Read first: `key_text.py --json` for the slides you will touch (add `--styles` to see fills, fonts, sizes, colors, alignment and the background), plus a render. Then pick the route per change:

| Change | Route |
|---|---|
| Text, notes, bullet levels and bullets on/off; fill, outline, shape type, arrowheads, shadow; font, size, color, alignment, line spacing; position and size; stacking order; slide background; showing or hiding a title / body / slide-number placeholder; chart data, type, colors, axis, value labels; table cell padding | `key_edit.py` (direct) — [references/editing.md](references/editing.md) |
| Adding, duplicating, deleting, moving or skipping slides; adding or deleting objects; images; table cells, rows and columns; new charts; a slide's layout | AppleScript — [references/applescript.md](references/applescript.md) |
| Slides from another deck | `key_merge.py` |
| Builds, layout (master) edits | not possible |

When a job needs both routes, the order is: copy → AppleScript (structure) → `key_edit.py --in-place --no-backup` on that same output file (text and styling) → check → render.

```bash
cp deck.key out.key
python3 scripts/keynote_app.py --for-file out.key run structure.applescript "$PWD/out.key"
python3 scripts/key_edit.py out.key --in-place --no-backup --ops ops.json     # out.key is already a copy
python3 scripts/key_check.py out.key --original deck.key --open
```

## Deck content and design

- **One message per slide**, stated in the title. Lead with the conclusion; size a live deck at roughly 1.5–2 minutes per slide.
- **Follow the deck you are editing.** Match its fonts, sizes, colors and alignment; a slide added to someone's deck should be indistinguishable from theirs. Only a deck you create from scratch is yours to design.
- **For a new deck, choose a palette that belongs to the topic** — one dominant color, one or two supporting tones, one accent — and carry it through titles, shapes and table headers. Dark title and closing slides with light content slides is a reliable structure.
- **Give every slide something to look at**: a chart, table, image, big number, or a small set of cards. Vary the composition from slide to slide; keep titles in one place.
- **Left-align body text**; center only titles and short labels inside shapes.
- **Leave margins** (≥ 100 pt at 1920 wide) and consistent gaps (40–60 pt) between blocks.
- Avoid: decorative lines under titles, colored stripes along slide or card edges, text-only slides, low-contrast text, body text under 32 pt on a 1920-wide slide, a title ending in a period.

## QA (required)

Your first render usually has real problems. Find them, fix them, re-render the slides you changed.

1. **Content**: `python3 scripts/key_text.py out.key` — missing or duplicated text, wrong order, leftover placeholder text. After filling a template: `python3 scripts/key_text.py out.key | grep -iE "lorem|ipsum|\bTODO|xxx|click to|ダブルクリック|テキストを入力"`.
2. **File** (after any direct edit): `python3 scripts/key_check.py out.key --original deck.key --open`. `--original` hides problems the source deck already had. A structural pass is not proof — `--open` is.
3. **Layout leads**: `python3 scripts/key_check.py out.key --layout [--original src.key]` has Keynote measure every object and lists those past the slide edge and text boxes lying on top of each other — the things a thumbnail hides (auto-sized text boxes have no size in the file, so only this and a render can tell). Run it on the source deck too when the job is to tidy one. It also lists table rows too short for their text, as Keynote measured them when it last saved the deck — after `key_edit.py` changed cells or padding, judge from a render instead. It does not see text clipped inside a shape.
4. **Visual**: `python3 scripts/key_render.py out.key qa/ --jpeg --slides <changed>` and look at every image. A `--grid` sheet is for orientation only — judge overlaps and clipping on full-size slide images. This is Keynote's own rendering with the real fonts, so what you see is what the user gets. Look for:
   - text overflowing or clipped at a box or slide edge — check this first
   - overlapping objects; objects off the slide
   - a body placeholder left empty behind your items (its prompt text does not render, but a `title_bullets` layout with no body wastes the slide — use `title_only`)
   - low contrast (dark text on a dark fill, theme-default text color on your new background)
   - uneven gaps, misaligned columns, titles at different heights on like slides
   - a duplicated slide's old content showing through

   Looking at your own output you tend to see what you intended; a fresh subagent given only the images finds more.

## Limits to state plainly

- macOS only for anything that renders or changes structure; reading and direct edits work anywhere Python does.
- Password-protected decks cannot be read directly; open them in Keynote (the user enters the password) and work through AppleScript.
- Not supported: builds / object animations, hyperlinks, grouping, movies and audio, comments. Say so rather than approximating.
- Keynote '09 (`.key` folders with `index.apxl`) is a different format: have the user open and re-save it in a current Keynote.
