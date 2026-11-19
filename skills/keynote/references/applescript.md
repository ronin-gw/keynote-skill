# Structural edits: AppleScript through `keynote_app.py run`

Slides, objects, tables, images and new charts are changed by the Keynote app.

Contents: [Skeleton](#skeleton) · [Running a script](#running-a-script) · [Slides](#slides) · [Addressing objects](#addressing-objects) · [AppleScript traps](#applescript-traps) · [What AppleScript can and cannot set](#what-applescript-can-and-cannot-set) · [Charts](#charts) · [Tables](#tables) · [Pictures](#pictures)

## Skeleton

```applescript
-- structure.applescript   run on a COPY:  cp deck.key out.key
--   python3 scripts/keynote_app.py --for-file out.key run structure.applescript "$PWD/out.key"
set {d, wasOpen} to my openDeck(item 1 of argv)          -- always open this way; argv paths must be absolute
tell {{APP}}
    tell d
        duplicate slide 3 to after slide 3
        duplicate slide 4 to end of slides
        move slide 7 to before slide 2
        delete slide 9
        set skipped of slide 5 to true
        set s to make new slide at after slide 1 with properties {base layout:slide layout 6}
        -- same layout as an existing slide:  {base layout:(base layout of slide 2)}
        tell s
            set object text of default title item to "Agenda"
            set object text of default body item to "First" & linefeed & "Second"
            set presenter notes to "Keep it short."
            set pic to make new image with properties {file:(POSIX file "/abs/path/fig.png"), position:{1000, 300}, width:800}
            set description of pic to "Alt text"
            set t to make new table with properties {row count:3, column count:2, position:{100, 300}, width:800}
            set value of cell 2 of row 1 of t to "Total"
            delete iWork item 4
        end tell
        set object text of iWork item 3 of slide 4 to "Replaced"   -- item numbers = key_text.py --json
        set file name of image 1 of slide 6 to (POSIX file "/abs/path/new.png")   -- swap a picture, keep its frame
    end tell
    save d
    if not wasOpen then close d saving no
end tell
```

## Running a script

- **Run it on a copy**: `save d` writes over the file the document was opened from.
- **Always pass `--for-file`** so the deck's own Keynote version does the saving (otherwise the newest Keynote runs the script and `save` upgrades the file format).
- **Open decks with `my openDeck(path)` only — never write `open (POSIX file p)` yourself, and do not define your own `openDeck`** (`run` supplies it only when the script has none). A hand-written `set d to open (POSIX file p)` on Keynote 13.2 returned `missing value` (the next line fails -1728 `slide 7 of missing value`) and in two runs raised a modal Keynote dialog ("cannot open … operation not permitted") that stalled every job using that Keynote until a person dismissed it. `openDeck` opened the same decks on 13.2 and 15.4. If such a dialog appears, do not retry in a loop or dismiss it: say so.
- **Address only the document `openDeck` returned — never `front document` or `document 1`.** Other documents may be open (the user's, or another job's). For the same reason never click menus through System Events (they act on whatever window is in front), and do not `quit` Keynote if `keynote_app.py info` shows documents you did not open.
- **A new document must be saved in the same script that makes it** (`make new document …` then `save d in POSIX file "/abs/out.key"`). Left untitled, Keynote 15 auto-saves it into the user's iCloud Drive.
- The scripting dictionary is `/Applications/Keynote.app/Contents/Resources/Keynote.sdef` (`sdef` needs Xcode; grep the file instead).

## Slides

- **Work out all slide numbers before you start, and remember they shift** after each add / delete / move. Do structural changes first, text and styling after.
- **To add a slide to someone's deck, duplicate one of theirs and replace its contents** rather than `make new slide`. Authors move titles and add rules and logos by hand on every slide, so a fresh slide from the bare layout looks foreign next to them. Render a neighbor and the new slide side by side; use `make new slide` only when the deck's slides really do match their layouts.
- **Template slots ≠ your items.** If a duplicated slide shows four cards and you have three, delete the fourth card's objects (`delete iWork item N`), not just its text — then look for orphaned shapes in the render.
- **Slides from another deck: use `key_merge.py`.** In AppleScript `duplicate slide … to … of otherDocument` silently does nothing; only `move` crosses documents, and it removes the slide from its source — the script does that on copies. Build a combined deck by starting from the deck whose look should win, merging the others' slides in, then duplicating one of its slides for dividers / summary.
- **Names are localized and can repeat; indexes are not.** Use `slide layout 6` (the index is in each slide's `key_text.py` marker and in `--layouts`), never `slide layout "Title & Bullets"`. Themes: `theme id "Application/White/Standard"`.

## Addressing objects

- Address objects as `iWork item N` (the item numbers of `key_text.py --json`). `text items` of a slide include the placeholders and every shape; `shapes` excludes placeholders.
- **Object references are positional.** `iWork item N`, `text item N` and a reference kept in a variable all point at "the N-th object", so after a delete, a `make new`, or a change of `base layout` (which adds or removes placeholders) they can address a different object. Re-find objects (by their text) after any such change.
- **Delete from the highest number down.** Deleting inside `repeat with x in text items` skips and misaddresses objects — collect the indexes first.
- Changing `base layout` is how a slide gains a slide-number or title placeholder its layout lacked (showing or hiding one that exists is `key_edit.py`'s `show` op).
- `position`, `width` and `height` come back rounded to whole points and must be read into a variable before use (`set p to position of img`, then `item 1 of p`). When a frame must match exactly, set it afterwards with `key_edit.py`'s `frame` op using the numbers from `--json`.

## AppleScript traps

- **Paragraph breaks are `linefeed`**, not `return`. A tab does not indent a bullet — set levels afterwards with `key_edit.py` (`levels`).
- **`object text of x` in a variable is a plain string**: write `color of object text of x`, `size of object text of x` directly.
- `it`, `kind`, `text`, `name`, `ref`, `rd` and other short words can collide with AppleScript or Keynote terms — use descriptive variable names (`theSlide`, `rowData`).
- Your own handlers (`on fmt(x) … end fmt`) may sit anywhere in the script file.

## What AppleScript can and cannot set

- **Cannot**: fill and outline colors, shape type, arrowheads, text alignment, bullet levels, slide backgrounds, chart data and axis settings after creation, builds, layout (master) edits. All but the last two are covered by `key_edit.py` / `keynote_lib` — run those **after** the AppleScript step, on the saved and closed file.
- **Can**: text color, font and size (`set color of object text of x to {65535, 0, 0}`, `set size of paragraph 2 of object text of x to 40`), which is handy while the deck is open.

## Charts

**`add chart` puts the chart on the *current* slide**, not the slide you `tell`:

1. `set current slide of d to slide N`
2. Inside `tell slide N`: `add chart row names {"A"} column names {"x", "y"} data {{1, 2}} type vertical_bar_2d group by chart row`
3. Take it as `chart (count of charts)` and set `position` / `width` / `height`. These frame the *plot area* only (checked on 13.2 and 15.4: a chart set to `{300, 300}`, 800 × 500 has its axes box exactly there, also when an existing chart is resized): axis numbers hang about 95 pt left of it (~155 pt for `700,000`), category labels ~45 pt below, and a legend (off in a new chart; `key_edit.py` `legend`) ~45 pt above — frame the chart inside its area minus those margins. Label text is full-slide size: shrink it with `text_scale` ([creating.md](creating.md#charts)).
4. Style it afterwards with `key_edit.py` `chart_options`.

## Tables

Cells are edited through the app: `value`, `font name`, `font size`, `text color`, `background color`, `alignment`, `format` of a cell; `name` of the table is its caption.

```applescript
tell table 1 of slide 1 of d
    set c to cell 3 of row 2                      -- or  cell "C2"
    set format of c to text                       -- THEN the value: the cell shows exactly this string
    set value of c to "96.0%"
    set font name of c to "HiraKakuProN-W6"       -- bold = the family's bold face (cells have no bold flag);
    set font name of cell 3 of row 3 to "HiraKakuProN-W3"   --   un-bold = back to the regular face
    set name to "Results 2026"                  -- the caption above the table
end tell
```

**Cell values**

- **A percent cell stores a fraction**: write `0.4567` to show 45.67% (writing `45.67` shows 4567%). Cells that merely *look* like percentages may instead be numbers with a custom "%" suffix (write `45.6`). Compare `value of c` with `formatted value of c` before writing a column.
- **A number written into a formatted cell is re-formatted by that cell**: `96.0` in a percent cell shows `96%`, and the string `"96.0%"` becomes 0.96 and shows `0.96%`. Decimal places cannot be set from AppleScript. When the displayed text matters, switch the cell to `text` format first, as above.
- Check the result with `formatted value of c` or `key_text.py`, which prints cells as displayed and hand-bolded cells as `**bold**`.
- Read the current face first (`font name of c`) so bold and regular come from the same family.
- `bold_cells` in `--json` are 0-based [row, col]; AppleScript rows and columns count from 1.

**Rows and columns**

- `set row count of t to 7` / `set column count of t to 5` (there is no "add row" command), `delete row 3`, `merge range "A3:B3"`.
- **A new row copies the cell fills of the row above but not its number formats** — a highlight color comes along, while `1234567` shows without separators and a percent as `0.456`. For each new row: `tell t to clear range "A7:H7"` (inside `tell t`, not `tell slide`) if the row above was highlighted, then write the cells as text (as above) or set `format` first; re-check with `key_text.py`.
- A cell's fill cannot be set to "none" — `clear` (content and formatting) is the way back to the table style.
- A header cell with a line break does not grow its row: `set height of row 1 of t to 60`. The same `height of row` is how a table that grew past the slide is brought back in (tables cannot be resized with `frame`).

## Pictures

`set file name of image N to (POSIX file "/abs/new.png")` keeps the old picture's frame **and its crop**: a replacement with a different aspect ratio comes out cropped and zoomed. When the shape differs:

1. Note `position` and `width` of the old image, then `delete` it.
2. `make new image with properties {file:…, position:…, width:…}` (height follows the aspect ratio).
3. Restore the stacking order with `key_edit.py`'s `reorder` if something must stay on top.
