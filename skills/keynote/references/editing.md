# Direct edits: `key_edit.py` and `keynote_lib`

Text and styling of an existing deck, written straight into the file. Keynote is not involved, and the deck must not be open in it. `python3 scripts/key_edit.py --help` documents every op.

Contents: [Ops](#ops) · [Targets](#targets) · [Text](#text) · [Type size and line height](#type-size-and-line-height) · [Item numbers, frames and comparisons](#item-numbers-frames-and-comparisons) · [Scripting keynote_lib](#scripting-keynote_lib)

## Ops

```bash
python3 scripts/key_edit.py deck.key -o out.key --replace "FY2025" "FY2026"
python3 scripts/key_edit.py deck.key -o out.key --ops ops.json
```

```json
[
  {"op": "set_text",   "slide": 2, "kind": "title", "text": "Results"},
  {"op": "set_text",   "slide": 2, "kind": "body", "text": "Finding one\nDetail\nFinding two", "levels": [0, 1, 0]},
  {"op": "style",      "slide": 4, "item": 3, "fill": "#1E2761", "stroke": null, "shape": "rounded", "color": "#FFFFFF", "align": "center"},
  {"op": "style",      "all_slides": true, "kind": "title", "font": "Helvetica", "size": 44, "bold": false, "color": "#000000", "shrink": false},
  {"op": "frame",      "slide": 4, "item": 3, "x": 120, "y": 300, "w": 600, "h": 200},
  {"op": "style_run",  "slide": 2, "kind": "body", "find": "Finding", "bold": true},
  {"op": "chart_data", "slide": 5, "item": 2, "categories": ["Q1", "Q2"], "series": [{"name": "2026", "values": [3, null]}]},
  {"op": "chart_options", "slide": 5, "item": 2, "chart_type": "line", "colors": ["#1E2761"], "y_min": 80, "y_max": 100, "y_steps": 4},
  {"op": "set_notes",  "slide": 2, "text": "Pause here."},
  {"op": "background", "all_slides": true, "color": "#FFFFFF"}
]
```

An op that fails stops the run and nothing is written.

- `style` also takes `"shape"` (same names as the builder), `"arrow_start"` / `"arrow_end"` for lines, `"line_spacing_pt"`, `"shrink"`, `"bullets": true` (turns a text box into a real bulleted list) and, for a table, `"padding"`: `{"op": "style", "slide": 4, "kind": "table", "padding": [20, 8]}`.
- `frame` moves / resizes (any of `x`, `y`, `w`, `h`, `rotation`). Tables and charts move but do not resize.
- `{"op": "levels", "slide": 2, "kind": "body", "levels": [0, 1, 0]}` re-indents existing paragraphs.
- `{"op": "replace", "find": …, "replace": …, "slides": [2]}` is `--replace` limited to some slides.
- `{"op": "replace_font", "map": {"Helvetica": "YuGo-Medium", "Helvetica-Bold": "YuGo-Bold"}}` re-points every text style in the deck (regular and bold faces are separate names). `python3 scripts/keynote_app.py fonts yugo` lists installed names.
- `{"op": "show", "slide": 4, "what": "slide_number", "visible": true}` switches a slide's `title` / `body` / `slide_number` placeholder on or off. `--json` reports `title_visible` and `slide_number_visible`; `null` means the slide's layout has no such placeholder — change `base layout` in AppleScript first.
- `{"op": "chart_options", "slide": 5, "item": 2, "colors": ["#1E2761", "#F96167"], "legend": true, "y_min": 0, "thousands": true, "font": "HiraginoSans-W3"}` — the chart keys of the builder ([creating.md](creating.md#charts)).
- `{"op": "reorder", …, "to": "back"}` changes the stacking order.

## Targets

- A target is one of: `"item": N` (the object's number in `key_text.py --json`: `slides[].items[].item`); `"kind": "title"|"body"`, or another kind such as `"slide_number"`, `"chart"`, `"table"` when the slide has exactly one; `"match": "text it contains"`; `"id"`.
- `"all_slides": true` or `"slides": [2, 3, 5]` in place of `"slide"` fans an op out. With `all_slides`, slides lacking the target are skipped and listed.
- **Styling an object gives it its own style copy**, so other objects sharing the theme style are untouched. Existing slides do not follow later changes to their layout either: to restyle a whole deck, apply the op to each slide.

## Text

- **Replacing text keeps its formatting**: `--replace` / `replace` edit inside the existing runs; `set_text` keeps the first paragraph's style for the whole new text. Write one paragraph per list item (`\n`), then `levels` for indentation.
- **Paragraph styling does not reach words that carry their own formatting** (a colored word in a title, a bold phrase): add `"override_runs": true` to the `style` op to make them follow, or target them with `style_run`.
- `style_run` styles every occurrence of `find` unless `"occurrence": 1`. Keys: `bold`, `italic`, `underline`, `size`, `color`, `font` — e.g. `{"op": "style_run", "slide": 2, "kind": "body", "find": "Q3", "font": "HelveticaNeue", "italic": true, "size": 60}`.
- **`italic: true` on a face without an italic (`YuGo-*`, `HiraKakuProN-*`, `HiraginoSans-*`) reports success and draws upright.** Give the run a Latin font that has an oblique face (`HelveticaNeue` as above); Japanese characters inside it stay upright.
- `"paragraphs"` counts real paragraphs: a soft line break inside a paragraph does not start a new one, so line numbers in `key_text.py` output can run ahead of paragraph numbers. Count from `--json --styles` (`"paragraphs"`).

## Type size and line height

- **Shrink-to-fit hides size differences.** Title and body placeholders (especially in decks imported from PowerPoint) often have it on: Keynote then draws long text smaller than the size in the file, and both `--styles` and AppleScript still report the nominal size. `--styles` shows `"shrink_to_fit": true`; when unifying sizes set `"shrink": false`.
- A few titles will then be too long for one line: **keep the reference size** and let those wrap to two lines. Make the title box tall enough (lines that do not fit a placeholder are clipped, i.e. vanish), set `line_spacing_pt`, and move that slide's content down. Do not shrink every title in the deck to fit the longest one, and do not reword unless asked.
- **Japanese text runs tall**: a line of Hiragino / Yu Gothic takes about 1.7 × the font size by default, so a two-line title needs a box over 3.4 × the size or the second line is clipped (silently — `key_text.py` still shows it). Either enlarge the box (`frame`) or fix the line height with `"line_spacing_pt"` (about 1.2 × the size).
- `line_spacing_pt` is an exact line height — steadier than a multiple where Japanese and Latin text mix. `doc.exact_line_spacing()` in `keynote_lib` applies it to a whole deck or given slides, and also cures lines that sit on top of each other in decks that came from PowerPoint.

## Item numbers, frames and comparisons

- **`show` (and any op that adds a placeholder) puts the new object last** in the slide's item numbering: re-read `--json` before addressing items by number.
- **Frames of auto-sized text boxes** (`w`/`h` of 0 in `--json`, common in imported decks): the file's `y` is not the box's top edge as AppleScript's `position` reports it. Do not mix coordinates from the two sources for such boxes; move them by a delta (new = old + shift) within one source.
- **Comparing before/after**: `key_text.py` orders objects by position, so moving things reorders its output. To prove content is unchanged compare `--json` (z-order, stable) or sorted text.

## Scripting `keynote_lib`

For anything the ops do not cover:

```python
import sys; sys.path.insert(0, "<this skill's directory>/scripts")
from keynote_lib import KeynoteFile, set_paragraph_levels

doc = KeynoteFile("deck.key")
for s in doc.slides():                       # s.index, s.title, s.notes, s.skipped, s.layout_name
    for d in s.walk():                       # d.kind, d.text, d.frame, d.id; walk() descends into groups
        if d.kind == "shape" and "Risk" in d.text:
            doc.set_fill(d, "#B85042")
    doc.set_background(s, "#F2F2F2")         # restyle every slide = loop over the slides
doc.save("out.key")
```

- `s.drawables` is a property (a list); `s.walk()` is a method.
- `doc` methods: `replace_fonts`, `fonts_used`, `override_runs`, `exact_line_spacing`, `show_placeholder`, `set_text`, `set_notes`, `replace_text`, `style_paragraphs`, `style_text`, `set_fill`, `set_stroke`, `set_shape`, `set_line_ends`, `set_shadow`, `set_text_layout`, `set_frame`, `set_background`, `reorder`, `set_chart_series`, `set_chart_options`, `style_info`, `background_info`, `table_cells`, `table_name`, `chart_series`, `item(slide, n)`, `find_drawables(text=, kind=)`.
- `doc.save(path)` refuses to overwrite unless `overwrite=True`.
- Every object is a live Protobuf message (`d.obj.msg`), so any field can be read, and changed with care — run `key_check.py` afterwards.
