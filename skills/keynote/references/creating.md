# Creating a deck with `key_build.py`

`python3 scripts/key_build.py --help` documents every spec key. This file is the worked example and the footguns.

Contents: [Example spec](#example-spec) · [Slide size and units](#slide-size-and-units) · [Layouts, themes and templates](#layouts-themes-and-templates) · [Text and shapes](#text-and-shapes) · [Fonts](#fonts) · [Charts](#charts) · [Tables](#tables) · [Other keys](#other-keys)

## Example spec

```json
{
  "theme": "White",
  "slides": [
    {"layout": "title", "title": "Reef Recovery 2026", "body": "Field survey — Okinawa",
     "background": "#0B3C5D", "title_style": {"color": "#FFFFFF", "bold": true}, "body_style": {"color": "#9FD3E6"},
     "notes": "Open with the headline number."},
    {"layout": "title_bullets", "title": "What we measured", "title_style": {"align": "left"},
     "body": ["Coral cover across 12 sites", {"text": "Three 50 m transects per site", "level": 1}, "Fish biomass"],
     "body_style": {"space_after": 10},
     "items": [
       {"type": "shape", "shape": "rounded", "radius": 28, "x": 1180, "y": 320, "w": 600, "h": 300,
        "fill": "#F96167", "stroke": null, "shadow": true, "text": "38%\nmean coral cover",
        "size": 96, "bold": true, "color": "#FFFFFF", "align": "center", "valign": "middle",
        "runs": [{"find": "mean coral cover", "size": 30, "bold": false}]}
     ]},
    {"layout": "title_only", "title": "Cover by year",
     "items": [
       {"type": "chart", "chart": "column", "x": 120, "y": 260, "w": 1000, "h": 700,
        "categories": ["2024", "2025", "2026"], "colors": ["#0B3C5D", "#F96167"],
        "series": [{"name": "North", "values": [27, 33, 41]}, {"name": "South", "values": [21, 30, 35]}]},
       {"type": "table", "x": 1200, "y": 300, "w": 620, "h": 240, "rows": [["Site", "2026"], ["North", 41], ["South", 35]],
        "font_size": 26, "header_fill": "#0B3C5D", "header_color": "#FFFFFF", "align": ["left", "right"]},
       {"type": "image", "path": "reef.jpg", "x": 1200, "y": 740, "w": 620, "h": 260, "alt": "Reef transect"}
     ]}
  ]
}
```

## Slide size and units

- **Units are slide points, and the default slide is 1920 × 1080 — twice PowerPoint's.** Type sizes scale with it: titles 80–110 pt, body 36–48 pt, captions 24–30 pt. A 16 pt label is unreadable.
- **A template deck may be 1024 × 768**: check `--layouts` / `key_text.py --json` and scale everything to it — every size in this file is roughly halved there.
- The build warns about items outside the slide.

## Layouts, themes and templates

- **`layout` is a role, an exact name, or a 1-based index.** Roles (`title`, `title_bullets`, `title_only`, `title_center`, `bullets`, `blank`, `photo`, `title_bullets_photo`) are detected from the layout's placeholders and work in any language. Each role names one layout; layouts without a role (quote, multi-photo…) are reached by index.
- **Layout names are localized** ("Title & Bullets" is "タイトル&箇条書き" on a Japanese Mac) and a user's template may hold several layouts with the same name — list them with `key_build.py --layouts` and use the index when in doubt.
- `"theme"` takes the English name ("White"), the localized one, or the id.
- Most content slides sit on `title_only` or `blank` with composed items.
- **`title` / `body` only work if the layout has that placeholder** — the build stops and says which layout lacks it.
- **To use the user's own look, pass `"template": "their-deck.key"`** instead of `"theme"`: the new deck gets that deck's layouts, fonts and colors, and its slides are dropped (`"keep_existing": true` appends instead). Run `key_render.py their-deck.key --layouts lay.jpg` and look before choosing layouts.

## Text and shapes

- **Bullets come from the layout's list style; never type a literal `•` or `・`.** A layout without a body placeholder (blank, title-only — common in users' own decks) still gets real bullets: `"bullets": true` on a multi-line `text` or `shape` item (hanging indent, so wrapped lines align; `"levels": [0, 1, 0]` indents; `"bullets": "–"` picks the glyph).
- **Text items need `w`** whenever they are aligned or long: without it the box is zero-width and grows from its left edge, so centered text lands off its mark.
- A text item's `x`/`y` is its top-left corner and it grows downward with its text (it has no `h`). When a block must have a fixed height or sit vertically centered in an area, use a `shape` with `"fill": null, "stroke": null` and `valign`.
- **Text wraps silently**: a label a little too wide for its shape or `w` drops its last characters to a second line. Nothing warns; look for orphaned line ends in the render and widen the box or shorten the text.
- **Text that does not fit a shape is clipped** — lines past the shape's bottom edge vanish from the render while `key_text.py` still lists them. Size shapes for their text and check the render.
- **A title placeholder is anchored at its bottom edge**, so a title that wraps to two lines grows *upward* and no longer lines up with one-line titles. At 1920 wide keep titles under ~35 characters at 90 pt (or lower the size with `title_style`).
- Tighten or loosen bullets with `body_style`: `{"space_after": 12, "line_spacing": 1.0}`. `space_before` / `space_after` act between paragraphs: `space_before` does not move the first paragraph, so on a one-paragraph body (the subtitle of a `title` layout) it changes nothing — to push a subtitle down from the title, move the body with `key_edit.py`'s `frame` op after the build.
- **Shapes take text styling for the whole shape** (`size`, `color`, `bold`, `align`, `valign`: top | middle | bottom); style part of the text with `runs`. `find` is literal text and every occurrence is styled — a short `find` such as `"6"` also hits the 6 in "2026", so add `"occurrence": 1` or use a longer string.
- `"fill": null` gives an outline-only or invisible box. **`"stroke": null` removes the outline**; leaving `stroke` out keeps the theme default, which on many themes is no outline but on some is a visible line.
- **Diagrams**: `"shape"` is `rect | rounded | oval | arrow | arrow_left | arrow_up | arrow_down | chevron | triangle | diamond`; a line takes `"arrow": "end" | "start" | "both"`. Boxes + arrows cover flowcharts and pipelines.

## Fonts

- **Fonts are PostScript names** (`HiraginoSans-W3`, `YuGo-Medium`, `HelveticaNeue`), not family names. A name the Mac lacks is drawn in another font without complaint, so the build and `key_edit.py` warn about it — heed the warning.
- **Font weight is the `bold` flag, not the face name.** Use the regular face plus `"bold": true` (`"HiraginoSans-W3"` + bold draws W6). A heavy face name (`HiraginoSans-W6`, `HelveticaNeue-Bold`) switches bold on for you; with `"bold": false` Keynote draws the regular face whatever the name says.

## Charts

**Charts are native — keep them native** so the user can edit the numbers. Only for a chart type Keynote lacks, place a rendered image with `alt` text.

- **Frame**: a chart's `x/y/w/h` frame the *plot area*. Axis numbers hang about 90 pt to the left for ticks like `37.5` (~155 pt for `700,000`), category labels ~45 pt below, a legend ~45–60 pt above — leave that room. The same holds for a chart's `position` / `width` / `height` in AppleScript.
- **Colors**: give `"colors"` (one per series), or the chart comes out in the theme's default blue/green, which will not match your palette.
- **Single bars**: one series = one color. To color single bars of a one-series column / bar chart pass `"bar_colors"`, one color per category (`["#1E2761", "#1E2761", "#F96167", …]`). The chart becomes a stacked chart with one series per color, which looks the same — tell the user the highlighted value sits in its own data row.
- **Legend and title**: a legend is drawn above the plot when there is more than one series (`"legend": false` hides it). Do not also set a chart `"title"` — it overlaps the legend; the slide title does that job.
- **Value axis**: Keynote picks the range itself and often does not start at 0 (88–95 for values around 90), which exaggerates differences and gives ticks like 93.25 — set `"y_min"` / `"y_max"` deliberately. `"y_steps": 7` sets the number of axis intervals (0–700,000 in 7 steps = ticks every 100,000; Keynote's default 4 gives 175,000). `"thousands": true` / `"decimals": N` format the axis numbers.
- **Label text**: `"font"`, `"text_color"` and `"text_scale"` restyle the chart's labels to match the deck. `text_scale` multiplies the current sizes, so applying it twice compounds; it scales value labels too, and `value_label_scale` multiplies on top of it (0.5 and 0.5 give a quarter). Value labels start about 1.6 × larger than the axis text, so they remain the biggest text after `text_scale`. Chart text is sized for a full-slide chart: for a chart about a third of the slide wide start around `0.6–0.8`; on a 1024 × 768 deck start at `0.6`.
- **Value labels**: `"value_labels": "outside"` prints each value above its bar. `"inside"` / `"middle"` put it in the bar, in white — unreadable on pale bars. `"value_label_scale"` / `"value_label_color"` size and color just those; `"value_label_decimals": 1` keeps `95.0` from printing as `95`. Collisions are not resolved for you: in a dense chart (25 bars) Keynote leaves some labels out, but in 2 series × 5 categories at 680 pt wide every label was drawn and they overlapped at `value_label_scale` 1 and 0.75 (clean at 0.5) — look at the render and shrink them. In some templates `"outside"` labels default to white (invisible on white): set `value_label_color`. With values printed, `"value_axis": false, "gridlines": false` can declutter (optional).

## Tables

- **Set `"padding"`** — the cells' inner margin, e.g. `16` or `[20, 8]` (horizontal, vertical). Keynote's default of 4 pt leaves text touching the cell borders on a 1920-wide slide (use about 16–24 there, 8–12 on 1024 × 768). Padding takes room from the columns: widen narrow ones with `col_widths` or their text wraps.
- Rows are one text line tall unless you give `row_height` or `h`. Either is a minimum: the build makes a row taller when Keynote measures its text (wrapped, or holding `\n`, after `padding`) as not fitting, and warns with the table's new bottom edge — the table can end below `y + h`, so re-check what sits under it. A failed build closes its temporary deck in Keynote; if Keynote is blocked, the error names the deck to close by hand.
- `"cells": [{"cell": "B2", "bold": true, "fill": "#FBE5D6", "color": "#C00000", "align": "right"}]` styles single cells (how to highlight a best value); `"font"` sets the face for the whole table.
- Cell values are written as text exactly as given (`"1,234,567"`, `"45.6%"`).
- `header_fill` / `header_color` color header *rows*. A header *column* (`header_cols: 1`) keeps the theme's color and will clash — leave `header_cols` at 0 and bold the first column instead.

## Other keys

- **Speaker notes go in `notes`**, never in a text box on the slide.
- **An unknown key is an error**, so a typo cannot be silently ignored.
- Transitions are supported (`"transition": "dissolve"`); builds, object animations and hyperlinks are not.
