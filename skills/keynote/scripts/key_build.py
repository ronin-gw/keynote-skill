#!/usr/bin/env python3
"""Build a Keynote deck from a JSON spec.

    python key_build.py spec.json out.key
    python key_build.py --themes                 # themes this Keynote offers (name + id)
    python key_build.py --layouts THEME_OR_DECK  # layouts of a theme / template deck, with roles

Keynote builds the deck (slides, text, tables, charts, images), then this
script styles it by editing the saved file (fills, outlines, corner radius,
alignment, bold, bullet levels, backgrounds) - things Keynote's scripting
interface cannot do. All coordinates are slide points from the top-left
corner; a default slide is 1920 x 1080.

Spec:

{
  "theme": "White",                  // English name, localized name or id (see --themes); default White
  "template": "lab-template.key",    // OR: start from this deck's layouts and styles (its slides are dropped)
  "keep_existing": false,            // with "template": keep its slides and append the new ones
  "size": [1920, 1080],              // only with "theme"
  "slides": [
    {
      "layout": "title_bullets",     // role, exact layout name, or 1-based index (see --layouts)
      "title": "Text",
      "body": ["Bullet", {"text": "Sub-bullet", "level": 1}],   // or one string
      "notes": "Presenter notes",
      "background": "#1E2761",
      "images": ["photo.jpg"],       // fills the layout's picture placeholders, in order
      "skipped": false,
      "transition": "dissolve",
      "title_style": {"color": "#FFFFFF", "align": "left", "size": 80, "bold": true, "font": "HelveticaNeue-Bold"},
      "body_style": {"color": "#CADCFC", "size": 40},
      "items": [
        {"type": "text",  "text": "Caption", "x": 100, "y": 900, "w": 800,
         "size": 28, "color": "#666666", "align": "center", "bold": false, "italic": false, "font": "..."},
        {"type": "shape", "shape": "rounded", "radius": 24, "x": 100, "y": 300, "w": 500, "h": 300,
         "fill": "#1E2761", "stroke": null, "shadow": true, "opacity": 100, "rotation": 0,
         "text": "Card", "size": 36, "color": "#FFFFFF", "align": "center", "valign": "middle", "padding": 24},
        {"type": "image", "path": "chart.png", "x": 1000, "y": 300, "w": 800, "h": 500, "alt": "Sales by quarter"},
        {"type": "line",  "x1": 100, "y1": 250, "x2": 1820, "y2": 250, "color": "#CCCCCC", "width": 2},
        {"type": "table", "x": 100, "y": 300, "w": 1700, "h": 500, "rows": [["Name", "Qty"], ["A", 3]],
         "header_rows": 1, "header_cols": 0, "font_size": 28, "col_widths": [1200, 500],
         "header_fill": "#1E2761", "header_color": "#FFFFFF", "align": ["left", "right"]},
        {"type": "chart", "chart": "column", "x": 100, "y": 300, "w": 1200, "h": 650,
         "categories": ["Q1", "Q2"], "series": [{"name": "2025", "values": [1, 2]}, {"name": "2026", "values": [3, 4]}],
         "colors": ["#1E2761", "#F96167"], "legend": true, "title": null}
      ]
    }
  ]
}

Text keys (text and shape items, title_style, body_style): size, color, bold, italic, font (PostScript
name), align (left | center | right | justify), line_spacing (multiple, e.g. 1.1) or line_spacing_pt
(exact line height in points), space_before, space_after (points; multi-line text and shape items
default to 0; space_before only opens a gap between paragraphs - it never moves the first one down).
valign (top | middle | bottom) and padding apply to shapes and body_style.
Weight: use the family's regular face plus "bold": true ("HiraginoSans-W3" + bold draws W6). A heavy
face name ("HiraginoSans-W6", "HelveticaNeue-Bold") turns bold on by itself; with "bold": false
Keynote draws the regular face whatever the name says.
Text in a shape that does not fit is CLIPPED at the shape's bottom edge (lines vanish) - check renders.
"runs": [{"find": "literal text", "bold": true, "color": "#C00", "size": 30, "italic": true, "underline": true}]
styles every occurrence of that text inside a text or shape item ("occurrence": 1 = only the first -
a short "find" like "6" also matches inside "2026").
"fill": null makes a shape see-through (no fill). A shape with a "fill" but no text "color" gets
black or white text, whichever reads on that fill.
Table extras: "row_height", "valign", "color" (body text), "merge": ["A3:B3"],
"cells": [{"cell": "B2", "fill": "#FFE066", "color": "#000000", "bold": true, "font": "...", "size": 30,
"align": "right"}] styles single cells (bold switches to the family's bold face). "header_cols" defaults to 0; a header
column keeps the theme's own color (header_fill colors header rows only).
Table "font" is a PostScript name for every cell. "padding": 16 (or [horizontal, vertical]) is the cells' inner
margin in points - Keynote's default is 4, which on a 1920-wide slide leaves text touching the borders; it takes
room from the columns, so widen narrow ones (col_widths) or text wraps.
A row whose text needs more height than it got (a header that wraps or holds "\n", with the final padding) is
made taller after Keynote has measured it, and a warning names the row and the table's new bottom edge -
the table can then end lower than "y" + "h" / the rows than "row_height".
"bullets": true turns a multi-line text or shape item into a real bulleted list (hanging indent; "levels":
[0, 1, 1] indents paragraphs; "bullets": "–" picks the glyph; "bullet_indent": points per level). Never type
"・" or "•" yourself - wrapped lines would not line up.
Chart: "colors" is one color per series; "legend" defaults to on when there is more than one series;
"title" draws a title inside the chart (it overlaps the legend - use one, or use the slide title).
"y_min" / "y_max" pin the value axis - Keynote otherwise picks its own range, which often does not
start at 0 and exaggerates differences; "thousands": true and "decimals": N format the axis numbers;
"font", "text_color", "text_scale" (multiplier) restyle all of the chart's labels.
"value_labels": "outside" prints each value above its bar / point; "inside" (= true) or "middle" put it
in the bar, in white. "value_label_decimals": N fixes their decimals (95 -> 95.0), "value_label_scale" /
"value_label_color" size and colour them; "value_axis": false / "gridlines": false hide the axis numbers / lines. "y_steps": N sets the number of
axis intervals. "bar_colors": ["#1E2761", "#F96167", ...] (one per category, one-series column / bar chart)
colours single bars - the chart is then built as a stacked chart with one series per colour.
A chart's x/y/w/h frame the PLOT AREA only: axis labels extend about 70 pt left and 60 pt below it and the
legend sits about 60 pt above. To fill a box X,Y,W,H with the whole chart use roughly
x = X+70, y = Y+60 (with a legend), w = W-80, h = H-120.
An unknown key is an error, so a typo cannot pass silently.
Relative paths in the spec ("template", image "path") are resolved from the spec file's folder, not from
where you run the command - absolute paths are safest.

"shape": rect | rounded | oval | arrow (block arrow pointing right) | arrow_left | arrow_up | arrow_down |
chevron | triangle | diamond.  "stroke": null (none) or {"color": "#333333", "width": 2}.
Line: "arrow": "end" | "start" | "both" adds arrowheads (the end is x2,y2).
"chart": column | bar | line | area | pie | scatter | stacked_column | stacked_bar | stacked_area
(+ _3d for column, bar, line, area, pie). Items stack in the order listed (later = on top).
An image given only "w" (or only "h") keeps its aspect ratio; with both it is fitted inside the box.
Relative paths are resolved against the spec file's folder.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import keynote_app  # noqa: E402
from keynote_app import KeynoteAppError  # noqa: E402
from keynote_lib import parse_color  # noqa: E402

DEFAULT_THEME = "Application/White/Standard"
CHART_TYPES = {
    "column": "vertical_bar_2d", "bar": "horizontal_bar_2d", "line": "line_2d", "area": "area_2d", "pie": "pie_2d",
    "scatter": "scatterplot_2d", "stacked_column": "stacked_vertical_bar_2d", "stacked_bar": "stacked_horizontal_bar_2d",
    "stacked_area": "stacked_area_2d", "column_3d": "vertical_bar_3d", "bar_3d": "horizontal_bar_3d",
    "line_3d": "line_3d", "area_3d": "area_3d", "pie_3d": "pie_3d",
}
TRANSITIONS = {
    "none": "no transition effect", "magic_move": "magic move", "dissolve": "dissolve", "push": "push", "wipe": "wipe",
    "move_in": "move in", "reveal": "reveal", "fade": "fade through color", "cube": "cube", "flip": "flip",
}


class SpecError(Exception):
    pass


def as_str(s) -> str:
    """Python string -> AppleScript string expression."""
    parts = str(s).replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lit = ['"' + p.replace("\\", "\\\\").replace('"', '\\"') + '"' for p in parts]
    return " & linefeed & ".join(lit) if len(lit) > 1 else lit[0]


def as_num(v) -> str:
    f = float(v)
    return str(int(f)) if f == int(f) else repr(f)


def as_color(spec) -> str:
    s = str(spec).lstrip("#")
    if len(s) < 6:
        raise SpecError(f"bad color {spec!r}")
    return "{" + ", ".join(str(int(s[i : i + 2], 16) * 257) for i in (0, 2, 4)) + "}"


def as_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return as_num(v)
    return as_str(v)


def image_size(path: str):
    try:
        r = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", path], capture_output=True, text=True, timeout=30)
        w = h = None
        for line in r.stdout.splitlines():
            if "pixelWidth" in line:
                w = float(line.split(":")[1])
            if "pixelHeight" in line:
                h = float(line.split(":")[1])
        return (w, h) if w and h else None
    except Exception:
        return None


# ---- themes & layouts -----------------------------------------------------


def list_themes(app) -> list[tuple[str, str]]:
    out = keynote_app.run_applescript(
        '''
set acc to ""
tell {{APP}}
    repeat with t in themes
        set acc to acc & (name of t) & tab & (id of t) & linefeed
    end repeat
end tell
return acc''',
        app=app, timeout=120,
    )
    return [tuple(line.split("\t")) for line in out.splitlines() if "\t" in line]


def resolve_theme(app, want: str | None) -> str:
    if not want:
        return DEFAULT_THEME
    themes = list_themes(app)
    for name, tid in themes:
        if want == tid or want == name:
            return tid
    low = want.lower().replace(" ", "")
    for name, tid in themes:
        key = tid.split("/")[1].split("_", 1)[-1].lower() if "/" in tid else ""
        if low in (key, tid.lower(), name.lower().replace(" ", "")):
            return tid
    raise SpecError(f"no theme {want!r}. Run key_build.py --themes for the list.")


def layout_table(deck_path: str) -> list[dict]:
    """Layouts of a saved deck: index, name, placeholders with frames, and guessed roles."""
    from keynote_lib import KeynoteFile

    doc = KeynoteFile(deck_path)
    W, H = doc.size
    rows = []
    for i, lay in enumerate(doc.layouts(), 1):
        ds = doc._slide_drawables(lay)
        info = {"index": i, "name": lay.msg.name, "title": None, "body": None, "pictures": 0, "other": 0, "roles": []}
        for d in ds:
            if d.kind == "title":
                info["title"] = [round(v) for v in d.frame]
            elif d.kind == "body":
                info["body"] = [round(v) for v in d.frame]
            elif d.kind == "image":
                info["pictures"] += 1
            elif d.kind != "slide_number":
                info["other"] += 1
        rows.append(info)
    seen = set()

    def give(role, pred):
        if role in seen:
            return
        for r in rows:
            if pred(r):
                r["roles"].append(role)
                seen.add(role)
                return

    plain = lambda r: r["pictures"] == 0 and r["other"] == 0  # noqa: E731
    top = lambda f: f is not None and f[1] + f[3] / 2 < H * 0.3  # noqa: E731
    give("blank", lambda r: plain(r) and not r["title"] and not r["body"])
    give("title", lambda r: plain(r) and r["title"] and r["body"] and not top(r["title"]))
    give("title_bullets", lambda r: plain(r) and r["title"] and r["body"] and top(r["title"]) and r["body"][3] > H * 0.4)
    give("title_only", lambda r: plain(r) and r["title"] and not r["body"] and top(r["title"]))
    give("title_center", lambda r: plain(r) and r["title"] and not r["body"] and not top(r["title"]))
    give("bullets", lambda r: plain(r) and r["body"] and not r["title"])
    give("title_bullets_photo", lambda r: r["pictures"] == 1 and r["title"] and r["body"] and top(r["title"]) and r["title"][2] > W * 0.6)
    give("photo", lambda r: r["pictures"] == 1 and not r["title"] and not r["body"])
    return rows


def resolve_layout(rows: list[dict], want, slide_no: int) -> dict:
    if want is None:
        want = "blank"
    if isinstance(want, int) or (isinstance(want, str) and want.isdigit()):
        i = int(want)
        if not 1 <= i <= len(rows):
            raise SpecError(f"slide {slide_no}: layout index {i} out of range 1-{len(rows)}")
        return rows[i - 1]
    for r in rows:
        if want in r["roles"]:
            return r
    for r in rows:
        if r["name"] == want:
            return r
    for r in rows:
        if r["name"].replace(" ", "").lower() == str(want).replace(" ", "").lower():
            return r
    names = "; ".join(f"{r['index']}={r['name']}" + (f" ({','.join(r['roles'])})" if r["roles"] else "") for r in rows)
    raise SpecError(f"slide {slide_no}: no layout {want!r}. Available: {names}")


def print_layouts(rows: list[dict]) -> None:
    for r in rows:
        bits = []
        if r["title"]:
            bits.append("title x={} y={} w={} h={}".format(*r["title"]))
        if r["body"]:
            bits.append("body x={} y={} w={} h={}".format(*r["body"]))
        if r["pictures"]:
            bits.append(f"{r['pictures']} picture placeholder(s)")
        if r["other"]:
            bits.append(f"{r['other']} other object(s)")
        role = f"  roles: {', '.join(r['roles'])}" if r["roles"] else ""
        print(f"{r['index']:>2}. {r['name']}{role}\n      " + ("; ".join(bits) or "empty"))


# ---- AppleScript generation -----------------------------------------------


def norm_body(body) -> tuple[str, list[int]]:
    if body is None:
        return "", []
    if isinstance(body, str):
        lines = body.split("\n")
        return "\n".join(lines), [0] * len(lines)
    texts, levels = [], []
    for it in body:
        if isinstance(it, str):
            texts.append(it)
            levels.append(0)
        else:
            texts.append(str(it.get("text", "")))
            levels.append(int(it.get("level", 0)))
    for t in texts:
        if "\n" in t:
            raise SpecError("a body bullet cannot contain a line break; split it into two bullets")
    return "\n".join(texts), levels


def item_script(it: dict, n: int, base: str, slide_no: int) -> list[str]:
    t = it.get("type")
    L: list[str] = []

    def need(*keys):
        for k in keys:
            if k not in it:
                raise SpecError(f"slide {slide_no}: {t} item needs \"{k}\"")

    def path_of(p):
        full = p if os.path.isabs(p) else os.path.join(base, p)
        if not os.path.exists(full):
            raise SpecError(f"slide {slide_no}: no such file: {p}")
        return os.path.abspath(full)

    if t == "text":
        need("text", "x", "y")
        props = [f"object text:{as_str(it['text'])}", f"position:{{{as_num(it['x'])}, {as_num(it['y'])}}}"]
        if "w" in it:
            props.append(f"width:{as_num(it['w'])}")
        L.append(f"set it{n} to make new text item with properties {{{', '.join(props)}}}")
    elif t == "shape":
        need("x", "y", "w", "h")
        props = [f"position:{{{as_num(it['x'])}, {as_num(it['y'])}}}", f"width:{as_num(it['w'])}", f"height:{as_num(it['h'])}"]
        props.append(f"object text:{as_str(it.get('text', ''))}")
        L.append(f"set it{n} to make new shape with properties {{{', '.join(props)}}}")
        if "opacity" in it:
            L.append(f"set opacity of it{n} to {as_num(it['opacity'])}")
        if it.get("rotation"):
            L.append(f"set rotation of it{n} to {as_num(it['rotation'])}")
    elif t == "image":
        need("path", "x", "y")
        full = path_of(it["path"])
        w, h = it.get("w"), it.get("h")
        x, y = float(it["x"]), float(it["y"])
        if w and h:
            sz = image_size(full)
            if sz:
                scale = min(float(w) / sz[0], float(h) / sz[1])
                fw, fh = sz[0] * scale, sz[1] * scale
                x, y = x + (float(w) - fw) / 2, y + (float(h) - fh) / 2
                w, h = fw, fh
        L.append(f"set it{n} to make new image with properties {{file:(POSIX file {as_str(full)}), position:{{{as_num(round(x))}, {as_num(round(y))}}}}}")
        if w:
            L.append(f"set width of it{n} to {as_num(round(float(w)))}")
        if h and not w:
            L.append(f"set height of it{n} to {as_num(round(float(h)))}")
        elif h:
            L.append(f"if (height of it{n}) is not {as_num(round(float(h)))} then set height of it{n} to {as_num(round(float(h)))}")
        L.append(f"set position of it{n} to {{{as_num(round(x))}, {as_num(round(y))}}}")
        if it.get("alt"):
            L.append(f"set description of it{n} to {as_str(it['alt'])}")
        if "opacity" in it:
            L.append(f"set opacity of it{n} to {as_num(it['opacity'])}")
    elif t == "line":
        need("x1", "y1", "x2", "y2")
        L.append(
            f"set it{n} to make new line with properties {{start point:{{{as_num(it['x1'])}, {as_num(it['y1'])}}}, "
            f"end point:{{{as_num(it['x2'])}, {as_num(it['y2'])}}}}}"
        )
    elif t == "table":
        need("rows", "x", "y")
        rows = it["rows"]
        nr, nc = len(rows), max(len(r) for r in rows)
        props = [
            f"row count:{nr}", f"column count:{nc}", f"header row count:{int(it.get('header_rows', 1))}",
            f"header column count:{int(it.get('header_cols', 0))}", "footer row count:0",
            f"position:{{{as_num(it['x'])}, {as_num(it['y'])}}}",
        ]
        if "w" in it:
            props.append(f"width:{as_num(it['w'])}")
        if "h" in it:
            props.append(f"height:{as_num(it['h'])}")
        L.append(f"set it{n} to make new table with properties {{{', '.join(props)}}}")
        L.append(f"tell it{n}")
        if "font_size" in it:
            L.append(f"set font size of cell range to {as_num(it['font_size'])}")
        if it.get("font"):
            L.append(f"set font name of cell range to {as_str(it['font'])}")
        for r, row in enumerate(rows, 1):
            for c, v in enumerate(row, 1):
                if v is None or v == "":
                    continue
                L.append(f"set value of cell {c} of row {r} to {as_value(v)}")
        for c, wv in enumerate(it.get("col_widths", []), 1):
            if wv:
                L.append(f"set width of column {c} to {as_num(wv)}")
        row_h = it.get("row_height")
        if row_h is None and "h" not in it:
            # with no height Keynote makes the table most of a slide tall, whatever the row count:
            # default to rows that just hold a line of text (rows still grow for wrapped text)
            pad = it.get("padding", 4)
            vpad = pad[1] if isinstance(pad, (list, tuple)) else pad
            row_h = round(float(it.get("font_size", 24)) * 1.9 + 2 * float(vpad))
        if row_h is not None:
            L.append(f"repeat with r in rows\nset height of r to {as_num(row_h)}\nend repeat")
        al = it.get("align")
        for a_ in ([al] if isinstance(al, str) else (al or [])) + [c.get("align") for c in it.get("cells", [])]:
            if a_ and a_ not in ("left", "center", "right", "justify"):
                raise SpecError(f"slide {slide_no}: table align must be left | center | right | justify, not {a_!r}")
        if isinstance(al, str):
            L.append(f"set alignment of cell range to {al}")
        elif al:
            for c, a in enumerate(al, 1):
                if a:
                    L.append(f"set alignment of column {c} to {a}")
        if it.get("valign"):
            va = {"middle": "center"}.get(it["valign"], it["valign"])  # AppleScript says center
            if va not in ("top", "center", "bottom"):
                raise SpecError(f"slide {slide_no}: table valign must be top | middle | bottom")
            L.append(f"set vertical alignment of cell range to {va}")
        hr = int(it.get("header_rows", 1))
        if hr and it.get("header_fill"):
            for r in range(1, hr + 1):
                L.append(f"set background color of row {r} to {as_color(it['header_fill'])}")
        if hr and it.get("header_color"):
            for r in range(1, hr + 1):
                L.append(f"set text color of row {r} to {as_color(it['header_color'])}")
        if it.get("color"):
            for r in range(hr + 1, nr + 1):
                L.append(f"set text color of row {r} to {as_color(it['color'])}")
        for cell in it.get("cells", []):
            ref = f'cell {as_str(cell["cell"])}'
            if "fill" in cell:
                L.append(f"set background color of {ref} to {as_color(cell['fill'])}")
            if "color" in cell:
                L.append(f"set text color of {ref} to {as_color(cell['color'])}")
            if cell.get("font"):
                L.append(f"set font name of {ref} to {as_str(cell['font'])}")
            if cell.get("size"):
                L.append(f"set font size of {ref} to {as_num(cell['size'])}")
            if cell.get("align"):
                L.append(f"set alignment of {ref} to {cell['align']}")
            if cell.get("bold"):
                # cells have no bold flag: switch to the family's bold face (W3 -> W6, Regular -> Bold, ...)
                L += [f"set fn to (font name of {ref}) as text", "set bf to fn",
                      'if fn ends with "-W3" or fn ends with "-W4" then',
                      'set bf to (text 1 thru -3 of fn) & "W6"',
                      'else if fn ends with "-Regular" then', 'set bf to (text 1 thru -8 of fn) & "Bold"',
                      'else if fn ends with "-Light" then', 'set bf to (text 1 thru -6 of fn) & "Bold"',
                      'else if fn ends with "-Medium" then', 'set bf to (text 1 thru -7 of fn) & "Bold"',
                      'else if fn does not contain "Bold" and fn does not contain "-W" then', 'set bf to fn & "-Bold"',
                      "end if", "try", f"set font name of {ref} to bf", "end try"]
        for m in it.get("merge", []):
            L.append(f"merge range {as_str(m)}")
        L.append("end tell")
        # setting widths/heights can shift the table; pin it again
        L.append(f"set position of it{n} to {{{as_num(it['x'])}, {as_num(it['y'])}}}")
    elif t == "chart":
        need("categories", "series")
        kind = CHART_TYPES.get(it.get("chart", "column"), it.get("chart"))
        if kind not in CHART_TYPES.values():
            raise SpecError(f"slide {slide_no}: unknown chart type {it.get('chart')!r}; use one of {', '.join(CHART_TYPES)}")
        cats = it["categories"]
        for s in it["series"]:
            if len(s["values"]) != len(cats):
                raise SpecError(f"slide {slide_no}: series {s.get('name')!r} has {len(s['values'])} values for {len(cats)} categories")
        rown = "{" + ", ".join(as_str(s.get("name", f"Series {i}")) for i, s in enumerate(it["series"], 1)) + "}"
        coln = "{" + ", ".join(as_str(c) for c in cats) + "}"
        data = "{" + ", ".join("{" + ", ".join(as_num(v) for v in s["values"]) + "}" for s in it["series"]) + "}"
        L.append("set current slide of d to s")  # `add chart` lands on the current slide, not the told one
        L.append(f"add chart row names {rown} column names {coln} data {data} type {kind} group by chart row")
        L.append(f"set it{n} to chart (count of charts)")
        if "w" in it:
            L.append(f"set width of it{n} to {as_num(it['w'])}")
        if "h" in it:
            L.append(f"set height of it{n} to {as_num(it['h'])}")
        if "x" in it and "y" in it:
            L.append(f"set position of it{n} to {{{as_num(it['x'])}, {as_num(it['y'])}}}")
    else:
        raise SpecError(f"slide {slide_no}: unknown item type {t!r} (text, shape, image, line, table, chart)")
    return L


def build_script(spec: dict, layouts: list[dict], base: str, n_existing: int, keep_existing: bool) -> str:
    L = ["on run argv", "set deckFile to POSIX file (item 1 of argv)", "with timeout of 3600 seconds", "tell {{APP}}",
         "set d to open deckFile", "repeat 100 times", "if d is not missing value then exit repeat", "delay 0.2",
         "try", "set d to (first document whose file is deckFile)", "end try", "end repeat", "end tell",
         'set cur to "opening the deck"', "try", "tell {{APP}}", "tell d"]
    for sn, sl in enumerate(spec["slides"], 1):
        lay = resolve_layout(layouts, sl.get("layout"), sn)
        L.append("set cur to " + as_str("slide %d (layout, title, body, notes)" % sn))
        L.append(f"set s to make new slide with properties {{base layout:slide layout {lay['index']}}}")
        L.append("tell s")
        if sl.get("title") is not None:
            if not lay["title"]:
                raise SpecError(f"slide {sn}: layout {lay['name']!r} has no title placeholder; use a text item or another layout")
            L.append(f"set object text of default title item to {as_str(sl['title'])}")
        text, _levels = norm_body(sl.get("body"))
        if sl.get("body") is not None:
            if not lay["body"]:
                raise SpecError(f"slide {sn}: layout {lay['name']!r} has no body placeholder; use a text item or another layout")
            L.append(f"set object text of default body item to {as_str(text)}")
        if sl.get("notes"):
            L.append(f"set presenter notes to {as_str(sl['notes'])}")
        pics = sl.get("images") or ([sl["image"]] if sl.get("image") else [])
        if len(pics) > lay["pictures"]:
            raise SpecError(f"slide {sn}: layout {lay['name']!r} has {lay['pictures']} picture placeholder(s), {len(pics)} images given")
        for i, p in enumerate(pics, 1):
            full = p if os.path.isabs(p) else os.path.join(base, p)
            if not os.path.exists(full):
                raise SpecError(f"slide {sn}: no such file: {p}")
            L.append(f"set file name of image {i} to (POSIX file {as_str(os.path.abspath(full))})")
        for n, it in enumerate(sl.get("items", []), 1):
            L.append("set cur to " + as_str("slide %d, item %d (%s)" % (sn, n, it.get("type"))))
            L += item_script(it, n, base, sn)
        L.append("end tell")
        if sl.get("skipped"):
            L.append("set skipped of s to true")
        if sl.get("transition"):
            eff = TRANSITIONS.get(sl["transition"], sl["transition"])
            L.append(f"set transition properties of s to {{transition effect:{eff}, transition duration:1.0, transition delay:0.5, automatic transition:false}}")
    if not keep_existing:
        L.append(f"repeat {n_existing} times")
        L.append("delete slide 1")
        L.append("end repeat")
    L += ["end tell", 'set cur to "saving"', "save d", "close d saving no", "end tell",
          "on error msg number num",
          "try", "with timeout of 20 seconds", "tell {{APP}} to close d saving no", "end timeout", "end try",
          'error "while building " & cur & ": " & msg number num',
          "end try", "end timeout", "end run"]
    return "\n".join(L)


# ---- styling pass ---------------------------------------------------------

_TEXT_KEYS = ("align", "bold", "italic", "size", "font", "color", "line_spacing", "line_spacing_pt", "space_before", "space_after")


def _text_style(doc, target, st: dict) -> None:
    kw = {k: st[k] for k in _TEXT_KEYS if st.get(k) is not None}
    if kw and target is not None and target.storage is not None:
        doc.style_paragraphs(target, **kw)


_SLIDE_KEYS = {"layout", "title", "body", "notes", "background", "images", "image", "skipped", "transition", "title_style", "body_style", "items"}
_STYLE_KEYS = set(_TEXT_KEYS) | {"valign"}
_ITEM_KEYS = {
    "text": {"type", "text", "x", "y", "w", "fill", "stroke", "shadow", "valign", "padding", "runs", "bullets", "levels", "bullet_indent", *_TEXT_KEYS},
    "shape": {"type", "shape", "radius", "x", "y", "w", "h", "fill", "stroke", "shadow", "opacity", "rotation", "text", "valign", "padding", "runs", "bullets", "levels", "bullet_indent", *_TEXT_KEYS},
    "image": {"type", "path", "x", "y", "w", "h", "alt", "opacity"},
    "line": {"type", "x1", "y1", "x2", "y2", "color", "width", "arrow"},
    "table": {"type", "x", "y", "w", "h", "rows", "header_rows", "header_cols", "font_size", "font", "col_widths", "row_height",
              "align", "valign", "header_fill", "header_color", "color", "cells", "merge", "padding"},
    "chart": {"type", "chart", "x", "y", "w", "h", "categories", "series", "legend", "title", "colors",
              "y_min", "y_max", "thousands", "decimals", "font", "text_color", "text_scale", "value_labels", "value_label_scale", "value_label_color", "value_label_decimals", "value_axis", "gridlines", "bar_colors", "y_steps"},
}


def font_warnings(spec: dict) -> list[str]:
    """Font names in the spec that this Mac does not have (Keynote substitutes silently)."""
    names: set[str] = set()

    def walk(v):
        if isinstance(v, dict):
            for k, x in v.items():
                if k == "font" and isinstance(x, str):
                    names.add(x)
                else:
                    walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    walk(spec)
    out = []
    for n in sorted(names):
        problem = keynote_app.font_problem(n)
        if problem:
            out.append(problem)
    return out


_PINS: list[tuple[int, int, float, float]] = []
_TABLES: list[tuple[int, int, int, int]] = []  # (deck slide, iWork item index, spec slide, spec item)


def pin_text_items(deck: str, app: dict) -> None:
    """Put every text item's top-left corner where the spec says, and have
    Keynote re-measure the text of every table this build made.

    A text box is auto-sized: its height is only known once the final font,
    size and spacing are in, and Keynote keeps a box's anchor (not its top)
    fixed while it grows. So after the styling pass the boxes sit a little off;
    Keynote, which knows the laid-out size, moves them back. Tables: the
    padding set in the file is unknown to the row-height measurements Keynote
    stored while building; touching the cells' text wrap makes it measure again.
    """
    if not _PINS and not _TABLES:
        return
    L = ["set {d, wasOpen} to my openDeck(item 1 of argv)", "tell {{APP}}"]
    for slide_no, idx, x, y in _PINS:
        L.append(f"set position of iWork item {idx} of slide {slide_no} of d to {{{as_num(round(x))}, {as_num(round(y))}}}")
    for slide_no, idx, _sn, _n in _TABLES:
        L.append(f"set text wrap of cell range of iWork item {idx} of slide {slide_no} of d to true")
    L += ["save d", "close d saving no", "end tell"]
    keynote_app.run_applescript("\n".join(L), [deck], app, timeout=900)


def fit_table_rows(deck: str, spec: dict) -> list[str]:
    """Grow table rows whose text Keynote measured as taller than the row.

    Keynote does not grow a row whose height was set (key_build always sets
    one): a header that wraps, or holds a line break, is otherwise cut off
    silently. The heights needed come from Keynote's own layout, saved in the
    file - no guessing from character counts."""
    from keynote_lib import KeynoteFile

    if not _TABLES:
        return []
    doc = KeynoteFile(deck)
    slides = doc.slides()
    W, H = doc.size
    notes = []
    for slide_no, idx, sn, n in _TABLES:
        ds = slides[slide_no - 1].drawables
        if not 1 <= idx <= len(ds) or ds[idx - 1].kind != "table":
            continue
        d, it = ds[idx - 1], spec["slides"][sn - 1]["items"][n - 1]
        m = d.obj.msg
        cache = m.layout_engine.width_height_cache.rows_fitting_entries if m.HasField("layout_engine") else []
        grown = []
        for r, size, need in doc.table_row_fits(d):
            if need > size + 0.5:
                doc.set_table_row_height(d, r, float(int(need + 0.999)))
                col = cache[r].fitting_index if r < len(cache) else 0
                row = it["rows"][r] if r < len(it["rows"]) else []
                text = str(row[col]) if col < len(row) and row[col] is not None else ""
                grown.append(f"row {r + 1} {size:g} -> {int(need + 0.999)} pt" + (f" ({text.replace(chr(10), ' / ')[:30]!r})" if text else ""))
        if grown:
            x, y, w, h = d.frame
            notes.append(f"slide {sn}: table (item {n}): rows grown so their text is not cut off: " + "; ".join(grown)
                         + f". The table is now {h:.0f} pt tall (bottom at y={y + h:.0f})"
                         + (f" - PAST the {H:g} pt slide bottom" if y + h > H + 1 else "")
                         + ". Widen the columns or shorten the text if the rows should stay one line.")
    if notes:
        doc.save(deck, overwrite=True)
    return notes


def unknown_keys(spec: dict) -> list[str]:
    """Keys the builder would silently ignore - almost always a typo."""
    out = []
    extra = set(spec) - {"theme", "template", "keep_existing", "size", "slides"}
    if extra:
        out.append(f"unknown top-level key(s): {', '.join(sorted(extra))}")
    for sn, sl in enumerate(spec.get("slides", []), 1):
        extra = set(sl) - _SLIDE_KEYS
        if extra:
            out.append(f"slide {sn}: unknown key(s) {', '.join(sorted(extra))}")
        for k in ("title_style", "body_style"):
            extra = set(sl.get(k) or {}) - _STYLE_KEYS
            if extra:
                out.append(f"slide {sn}: unknown {k} key(s) {', '.join(sorted(extra))}")
        for n, it in enumerate(sl.get("items", []), 1):
            known = _ITEM_KEYS.get(it.get("type"))
            if known and set(it) - known:
                out.append(f"slide {sn}: item {n} ({it['type']}): unknown key(s) {', '.join(sorted(set(it) - known))}")
            # value shapes that would otherwise die later with a bare Python traceback
            st = it.get("stroke")
            if st is not None and not isinstance(st, dict):
                out.append(f'slide {sn}: item {n}: "stroke" must be null or {{"color": "#333333", "width": 2}}, not {st!r}')
            if it.get("type") in ("text", "shape") and isinstance(it.get("padding"), (list, tuple)):
                out.append(f'slide {sn}: item {n}: "padding" of a {it["type"]} is one number (only a table takes [horizontal, vertical])')
    return out


def style_pass(deck: str, spec: dict, first_new: int) -> list[str]:
    from keynote_lib import KeynoteFile, set_paragraph_levels

    doc = KeynoteFile(deck)
    warnings = []
    slides = doc.slides()
    W, H = doc.size
    for sn, sl in enumerate(spec["slides"], 1):
        s = slides[first_new + sn - 1]
        for n, it in enumerate(sl.get("items", []), 1):
            xs = [it[k] for k in ("x", "x1", "x2") if k in it] + ([it["x"] + it["w"]] if "x" in it and "w" in it else [])
            ys = [it[k] for k in ("y", "y1", "y2") if k in it] + ([it["y"] + it["h"]] if "y" in it and "h" in it else [])
            if any(v < 0 or v > W for v in xs) or any(v < 0 or v > H for v in ys):
                warnings.append(f"slide {sn}: item {n} ({it.get('type')}) extends outside the {W:g} x {H:g} slide")
        if sl.get("background"):
            doc.set_background(s, sl["background"])
        _text, levels = norm_body(sl.get("body"))
        body = s.body_item
        if body is not None and any(levels):
            set_paragraph_levels(body.storage.msg, levels)
        if sl.get("title_style"):
            _text_style(doc, s.title_item, sl["title_style"])
        if sl.get("body_style"):
            _text_style(doc, body, sl["body_style"])
            if sl["body_style"].get("valign"):
                doc.set_text_layout(body, valign=sl["body_style"]["valign"])
        items = sl.get("items", [])
        if not items:
            continue
        made = [d for d in s.drawables if d.kind not in ("title", "body", "slide_number", "object_placeholder")]
        made = made[len(made) - len(items):] if len(made) >= len(items) else []
        if not made:
            warnings.append(f"slide {sn}: could not match items to objects; styling skipped")
            continue
        offset = len(s.drawables) - len(items)
        for n, it in enumerate(items, 1):
            if it["type"] == "text":
                _PINS.append((first_new + sn, offset + n, float(it["x"]), float(it["y"])))
            elif it["type"] == "table":
                _TABLES.append((first_new + sn, offset + n, sn, n))
        for it, d in zip(items, made):
            t = it["type"]
            expect = {"text": ("text",), "shape": ("shape",), "image": ("image",), "line": ("line", "shape"), "table": ("table",), "chart": ("chart",)}[t]
            if d.kind not in expect:
                warnings.append(f"slide {sn}: expected a {t}, found {d.kind}; styling skipped for it")
                continue
            if t in ("text", "shape"):
                if "fill" in it:
                    doc.set_fill(d, it["fill"])
                if "stroke" in it:
                    st = it["stroke"]
                    if st is None:
                        doc.set_stroke(d, width=0)
                    else:
                        doc.set_stroke(d, st.get("color"), st.get("width"))
                if t == "shape" and it.get("shape", "rect") != "rect":
                    doc.set_shape(d, it["shape"], float(it.get("radius", 20)))
                if "shadow" in it:
                    doc.set_shadow(d, bool(it["shadow"]))
                if it.get("valign") or it.get("padding") is not None:
                    doc.set_text_layout(d, valign=it.get("valign"), padding=it.get("padding"))
                if d.text:
                    st = dict(it)
                    if t == "shape" and "fill" in it and "color" not in it:
                        # theme shapes usually carry white text for their coloured fill: keep it readable
                        fill = it["fill"]
                        lum = 1.0
                        if fill:
                            r_, g_, b_, _a = parse_color(fill)
                            lum = 0.2126 * r_ + 0.7152 * g_ + 0.0722 * b_
                        st["color"] = "#000000" if lum > 0.55 else "#FFFFFF"
                    if "\n" in it.get("text", ""):  # theme text styles often carry paragraph spacing that pulls lines apart
                        st.setdefault("space_before", 0)
                        st.setdefault("space_after", round(0.4 * float(it["size"])) if it.get("bullets") and it.get("size") else 0)
                    _text_style(doc, d, st)
                    if it.get("levels") is not None:
                        set_paragraph_levels(d.storage.msg, it["levels"])
                    if it.get("bullets"):  # after the text style: the hanging indent follows the text size
                        b = it["bullets"]
                        doc.set_bullets(d, True, char=b if isinstance(b, str) else None, indent=it.get("bullet_indent"))
                for run in it.get("runs", []):
                    hits = doc.style_text(d, run["find"], occurrence=int(run.get("occurrence", 0)),
                                          **{k: run[k] for k in ("bold", "italic", "color", "size", "font", "underline") if k in run})
                    if not hits:
                        warnings.append(f"slide {sn}: run text {run['find']!r} not found in its item")
                    elif hits > 1 and "occurrence" not in run:
                        warnings.append(f"slide {sn}: run text {run['find']!r} matched {hits} places in its item and all were styled - add \"occurrence\": 1 to style only the first")
            elif t == "table":
                if it.get("padding") is not None:
                    doc.set_table_padding(d, it["padding"])
            elif t == "line":
                if it.get("color") or it.get("width"):
                    doc.set_stroke(d, it.get("color"), it.get("width"))
                arrow = it.get("arrow")
                if arrow:
                    if arrow not in ("end", "start", "both"):
                        raise SystemExit(f'slide {sn}: line "arrow" must be end | start | both')
                    doc.set_line_ends(d, start=arrow in ("start", "both"), end=arrow in ("end", "both"))
            elif t == "chart":
                legend = it.get("legend", len(it["series"]) > 1 or it.get("chart", "").startswith("pie"))
                if legend and it.get("title"):
                    warnings.append(f"slide {sn}: chart has both a legend and a title; Keynote draws them on top of each other - drop one")
                doc.set_chart_options(d, legend=bool(legend), title=it.get("title"), colors=it.get("colors"),
                                      **{k: it[k] for k in ("y_min", "y_max", "thousands", "decimals", "font", "text_color", "text_scale", "value_labels", "value_label_scale", "value_label_color", "value_label_decimals", "value_axis", "gridlines", "chart_type", "bar_colors", "y_steps") if it.get(k) is not None})
    doc.save(deck, overwrite=True)
    return warnings


# ---- driver ---------------------------------------------------------------


def new_blank_deck(app, theme_id: str, size, out: str) -> None:
    w, h = (size or [1920, 1080])[:2]
    keynote_app.run_applescript(
        f'''
set outFile to POSIX file (item 1 of argv)
tell {{{{APP}}}}
    set d to make new document with properties {{document theme:theme id {as_str(theme_id)}, width:{int(w)}, height:{int(h)}}}
    save d in outFile
    close d saving no
end tell''',
        [out], app, timeout=300,
    )


def build(spec: dict, out: str, base: str, app_choice: str | None = None, verbose: bool = True) -> list[str]:
    if not spec.get("slides"):
        raise SpecError('spec needs a non-empty "slides" list')
    bad = unknown_keys(spec)
    if bad:
        raise SpecError("; ".join(bad) + ". See key_build.py --help for the spec.")
    out = os.path.abspath(out)
    if not out.endswith(".key"):
        raise SpecError("output must end in .key")
    for a in keynote_app.installed():
        if keynote_app.is_open(out, a):
            raise SpecError(f"{out} is open in Keynote; close it first")
    template = spec.get("template")
    if template:
        template = template if os.path.isabs(template) else os.path.join(base, template)
        if not os.path.exists(template):
            raise SpecError(f"no such template: {template}")
    app = keynote_app.choose(app_choice, template if (template and not app_choice) else None, new_deck=True)
    work = tempfile.mkdtemp(prefix="keybuild-")
    tmp = os.path.join(work, os.path.basename(out))
    try:
        if template:
            if os.path.isdir(template):
                shutil.copytree(template, tmp, symlinks=True)
            else:
                shutil.copyfile(template, tmp)
        else:
            new_blank_deck(app, resolve_theme(app, spec.get("theme")), spec.get("size"), tmp)
        from keynote_lib import KeynoteFile

        n_existing = len(KeynoteFile(tmp).slides())
        layouts = layout_table(tmp)
        keep = bool(spec.get("keep_existing")) and bool(template)
        script = build_script(spec, layouts, base, n_existing, keep)
        keynote_app.run_applescript(script, [tmp], app, timeout=3700)
        del _PINS[:]
        del _TABLES[:]
        warnings = style_pass(tmp, spec, n_existing if keep else 0)
        try:
            pin_text_items(tmp, app)
        except KeynoteAppError as e:
            warnings.append(f"text items could not be re-positioned / tables re-measured after styling ({e}); check the render")
        warnings += fit_table_rows(tmp, spec)
        warnings = font_warnings(spec) + warnings
        if os.path.lexists(out):
            shutil.rmtree(out) if os.path.isdir(out) else os.remove(out)
        shutil.move(tmp, out)
    except SpecError:
        raise
    except BaseException:
        # a build that failed or timed out half-way leaves its temporary deck open in Keynote;
        # close it unsaved (best effort, short timeout - the original error is what gets reported)
        closed = keynote_app.close_documents_in(work, app)
        if closed:
            print(f"(closed the unfinished temporary deck {', '.join(closed)} in Keynote without saving)", file=sys.stderr)
        elif closed is None:
            print(f"(Keynote did not answer: if it still shows {os.path.basename(tmp)} from {work}, that is this failed "
                  "build's temporary deck - close it without saving)", file=sys.stderr)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return warnings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec", nargs="?", help="JSON spec file (- for stdin)")
    ap.add_argument("out", nargs="?", help="output .key")
    ap.add_argument("--themes", action="store_true", help="list available themes")
    ap.add_argument("--layouts", metavar="THEME_OR_DECK", help="list the layouts of a theme or of a .key deck")
    ap.add_argument("--app", help="15, 14 or a bundle id")
    a = ap.parse_args()
    try:
        if a.themes:
            app = keynote_app.choose(a.app, new_deck=True)
            for name, tid in list_themes(app):
                print(f"{name}\t{tid}")
            return 0
        if a.layouts:
            if os.path.exists(a.layouts):
                print_layouts(layout_table(a.layouts))
            else:
                app = keynote_app.choose(a.app, new_deck=True)
                work = tempfile.mkdtemp(prefix="keylayouts-")
                try:
                    tmp = os.path.join(work, "theme.key")
                    new_blank_deck(app, resolve_theme(app, a.layouts), None, tmp)
                    print_layouts(layout_table(tmp))
                finally:
                    shutil.rmtree(work, ignore_errors=True)
            return 0
        if not a.spec or not a.out:
            ap.error("give a spec file and an output .key (or --themes / --layouts)")
        if a.spec == "-":
            spec, base = json.load(sys.stdin), os.getcwd()
        else:
            with open(a.spec, encoding="utf-8") as f:
                spec = json.load(f)
            base = os.path.dirname(os.path.abspath(a.spec))
        for w in build(spec, a.out, base, a.app):
            print(f"warning: {w}", file=sys.stderr)
        print(os.path.abspath(a.out))
        return 0
    except (SpecError, KeynoteAppError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as e:
        print(f"error: spec is not valid JSON: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
