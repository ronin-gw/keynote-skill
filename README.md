# keynote-skill

[日本語](README-jp.md)

A Claude skill for reading, creating and editing Apple Keynote (`.key`) files **directly, without converting to pptx**.
It aims to be the Keynote counterpart of the [pptx skill in anthropics/skills](https://github.com/anthropics/skills/tree/main/skills/pptx). Requires macOS.

## How it works

A `.key` file holds `Index/*.iwa` files (Snappy-compressed Protobuf). The skill uses two routes:

| Route | What it does | Keynote app |
|---|---|---|
| **The file, directly** (`keynote_lib.py`) | All reading (tables as displayed, formatting can be read too); rewriting text and notes; position and size; fill, outline, shape type (arrows etc.), shadow, alignment, font, line spacing, bullets (levels, turning a text box into a bulleted list), table cell padding, background color; showing or hiding the title and slide number; chart data, type, colors, legend, axis range, value labels and per-bar colors | Not needed |
| **AppleScript** (`keynote_app.py`) | Rendering (images, PDF); adding, duplicating, deleting and moving slides and objects; creating tables, images and charts; writing table cells; importing PowerPoint files | Required |

AppleScript cannot change things like fill colors or text alignment, while editing the file directly is risky for structural changes such as adding slides, so the skill combines the two.
Direct edits give the edited object its own derived style, so other objects sharing the same theme style are not affected.
Unknown fields are kept as Protobuf, and `.iwa` files that were not changed are written back byte for byte.

## Scripts

| Script | Purpose |
|---|---|
| `key_text.py` | Prints the content as Markdown / JSON (titles, bullets, tables, chart data, image names, notes) |
| `key_render.py` | Exports slides to PNG/JPEG/PDF; also makes thumbnail sheets and layout sheets |
| `key_build.py` | Builds a new deck from a JSON spec (from a theme, or using an existing deck as a template) |
| `key_edit.py` | Applies text replacements and style / position changes directly to the `.key` (optionally to all slides at once) |
| `key_merge.py` | Brings slides from other decks in, still editable (merging decks; the source files are not modified) |
| `key_import.py` | Imports a `.pptx` as a Keynote document, restores PowerPoint-equivalent line spacing, and reports what was lost (charts, fonts) |
| `key_check.py` | Structural check of an edited file (`--open` checks that Keynote really opens it; `--layout` also finds objects off the slide and overlapping text) |
| `keynote_app.py` | Detects the installed Keynote apps, runs AppleScript, lists installed font names |
| `keynote_lib.py` | The Python library underlying all of the above |

See [`skills/keynote/SKILL.md`](skills/keynote/SKILL.md), the task guides in [`skills/keynote/references/`](skills/keynote/references/) and each script's `--help` for details.

## Installation

Install it as a Claude Code plugin. In Claude Code:

```
/plugin marketplace add ronin-gw/keynote-skill
/plugin install keynote@keynote-skill
```

Then install the Python dependencies (the plugin does not install them):

```bash
pip install keynote-parser Pillow
```

Update later with `/plugin marketplace update keynote-skill`.
Rendering and structural changes need macOS with Keynote installed.

## macOS permissions

The scripts drive Keynote from the command line with AppleScript, so macOS asks for consent the first time. The prompt names the app the commands run under — your terminal (Terminal, iTerm2, …) or Claude Code — not this skill.

| Prompt / setting | Needed? | What it is used for |
|---|---|---|
| **Automation → Keynote** ("… wants access to control Keynote") | Required for rendering, building, structural edits and .pptx import | Opening decks, exporting slide images, adding slides and objects, saving. Reading a deck and direct edits (`key_text.py`, `key_edit.py`) do not use Keynote and need no permission |
| **Automation → System Events** ("… wants access to control System Events") | Optional | Only `keynote_app.py info` and Keynote timeout errors use it, to see whether Keynote is showing a dialog |
| **Accessibility** (macOS asks on first use) | Optional | Together with the System Events permission, lets the same check read the dialog's text and button names |

- **Why the optional pair exists**: a dialog left open in Keynote (for example "… can't be opened") stalls every command that drives it, and without these permissions the scripts cannot tell you why. With them, `info` prints the dialog's text so you know what to dismiss.
- **The check only reads.** It lists Keynote's dialog windows and their text. The scripts never click buttons, type keys, or read any other app's windows.
- **Declining is safe.** Without the optional permissions everything else works; `info` prints one line, `dialogs not checked: no permission`, instead of the dialog text. That line is not an error.
- **Not used at all**: Screen Recording, Full Disk Access, Contacts, Camera, Microphone. If something asks for those while you use this skill, it is not this skill.
- **The app name in the prompt can look unfamiliar.** When the commands are run by Claude Code, macOS may show Claude Code's version number (for example `2.1.327`) instead of "Claude Code". That is still the app asking on this skill's behalf.
- **If you declined a prompt**, macOS does not ask again: the scripts keep reporting `dialogs not checked` (or, for Keynote itself, fail with a "not authorized" error) until you switch the entry on under System Settings → Privacy & Security → Automation / Accessibility. Meanwhile you can still run `python3 <skill dir>/scripts/keynote_app.py info` from a terminal that has the permission to see a dialog.
- To withdraw a permission, switch it off under System Settings → Privacy & Security → Automation (or Accessibility).

## Keynote versions

- Keynote 15.x (Keynote Creator Studio, `com.apple.Keynote`) and 14.x or earlier (`com.apple.iWork.Keynote`) can be installed side by side as separate apps. Their names collide, so the scripts always address the app by bundle ID.
- **Saving in 15.x silently upgrades the file format to 26.x, after which older Keynote versions can no longer open the deck.** For an existing deck, the scripts therefore pick the oldest installed Keynote that can open it (override with `--app 15` / `--app 14` or the `KEYNOTE_APP` environment variable). Direct edits never change the file format.
- Tested with Keynote 15.4 (format 26.4.0) and Keynote 13.2 (format 13.2.1) on macOS 27.2, plus reading decks created in 2018.
- Decoding uses [keynote-parser](https://github.com/psobot/keynote-parser) (officially supporting up to 14.5). Files from 15.4 contain a few unknown fields; they have been confirmed to survive a round trip unchanged.

## Not supported

Builds (object animations), hyperlinks, grouping, movies and audio, comments, applying layout (master) changes to existing slides, reading password-protected decks directly, and the Keynote '09 format.

## Tests

```bash
pytest tests                    # tests that read and write files
KEYNOTE_TESTS=1 pytest tests    # also run the tests that drive the Keynote app
```

The sample decks used by the tests are not included in the repository (they contain Apple theme artwork).
On the first run they are built automatically from `tests/fixtures/spec.json` with the installed Keynote and saved in `tests/fixtures/` (ignored by Git). Later runs do not need Keynote.

- A deck is built for Keynote 15 or later and for 14.x or earlier. If only one of them is installed, the tests for the other are skipped.
- Without Keynote, all tests that need a deck are skipped.

## Trademarks

Keynote, macOS and AppleScript are trademarks of Apple Inc. PowerPoint is a trademark of Microsoft Corporation.
This is an independent project and is not affiliated with, or endorsed by, Apple Inc.

## License

[MIT](LICENSE)
