#!/usr/bin/env python3
"""Edit a .key file directly - no Keynote needed, and the deck's file format is left as it is.

    python key_edit.py deck.key -o out.key --replace "FY2025" "FY2026"
    python key_edit.py deck.key -o out.key --replace "Draft" "Final" --slides 1,3
    python key_edit.py deck.key -o out.key --ops ops.json
    python key_edit.py deck.key --in-place --ops ops.json      # keeps deck.backup.key

ops.json is a list of operations, applied in order. Each names a slide (1-based)
and usually a target on it - one of:
    "item": 3            n-th object on the slide, as numbered by `key_text.py --json`
    "kind": "title"      or "body"; or "slide_number", "chart", "table", "image" ... when the slide has just one
    "match": "some text" the object whose text contains this
    "id": 123456         object id from `key_text.py --json`

[
  {"op": "set_text",   "slide": 2, "kind": "title", "text": "New title"},
  {"op": "set_text",   "slide": 2, "kind": "body",  "text": "One\nTwo\nThree", "levels": [0, 1, 1]},
  {"op": "set_notes",  "slide": 2, "text": "Say this."},
  {"op": "style",      "slide": 2, "match": "Card", "fill": "#1E2761", "stroke": null, "shape": "rounded", "radius": 24,
                       "shadow": true, "valign": "middle", "padding": 20, "shrink": false,
                       "align": "center", "bold": true, "italic": false, "size": 36, "font": "HelveticaNeue", "color": "#FFFFFF",
                       "paragraphs": [0]},
  {"op": "frame",      "slide": 2, "item": 4, "x": 120, "y": 300, "w": 600, "h": 200, "rotation": 0},
  {"op": "style_run",  "slide": 2, "kind": "body", "find": "important", "bold": true, "color": "#C0392B"},
  {"op": "levels",     "slide": 2, "kind": "body", "levels": [0, 1, 1]},
  {"op": "background", "slide": 1, "color": "#0B3C5D"},
  {"op": "chart_data", "slide": 3, "item": 2, "categories": ["Q1", "Q2"], "series": [{"name": "2026", "values": [3, 4]}]},
  {"op": "chart_options", "slide": 3, "item": 2, "legend": true, "colors": ["#1E2761", "#F96167"], "title": "",
                       "y_min": 0, "y_max": 100, "thousands": true, "decimals": 0, "font": "HiraginoSans-W3", "text_color": "#333333", "text_scale": 1.2,
                       "value_labels": true},
  {"op": "reorder",    "slide": 2, "item": 4, "to": "back"},
  {"op": "show",       "slide": 4, "what": "slide_number", "visible": true},
  {"op": "replace_font", "map": {"Helvetica": "YuGo-Medium", "Helvetica-Bold": "YuGo-Bold"}},
  {"op": "replace",    "find": "old", "replace": "new", "slides": [2, 3]}
]

"replace" (and --replace) covers slide text, table cells and presenter notes alike, and keeps each
word's styling; "set_text" rewrites the whole object in its FIRST paragraph's style, so on a box that mixes
styles (a small label over a big number) use "replace" instead.
"set_text" may carry "levels" (one indent level per paragraph); "levels" alone re-indents
existing text. "style_run" styles every occurrence of "find" in the target unless
"occurrence": N (1 = first) is given. "valign" is top | middle | bottom; "to" is front | back.
"chart_options": "colors" is one per series (null keeps one); "title": "" hides the title.
"style" takes any subset of its keys; "stroke" is null (no outline) or
{"color": "#333333", "width": 2}; "shape" is rect | rounded | oval | arrow | arrow_left |
arrow_up | arrow_down | chevron | triangle | diamond; "arrow_start" / "arrow_end": true|false
put arrowheads on a line; "shrink": false stops Keynote shrinking text to fit its box (a
placeholder with shrink on draws long text smaller than the size the file states); text keys
apply to whole paragraphs ("paragraphs": 0-based list, default all), plus "line_spacing_pt"
(exact line height) beside "line_spacing" (multiple). A word that carries its own colour / size /
font keeps it unless you add "override_runs": true (unifying titles usually wants that).
"replace_font" re-points every text style in the deck from one font to another (bold and regular
faces are separate names: map both). "chart_options" also takes "value_labels": true | "outside" |
"inside" | "middle", "value_label_scale", "value_label_color", "value_label_decimals", "value_axis": false, "gridlines": false.
"chart_options" can also change the drawing: "chart_type": column | bar | line | area | stacked_column |
stacked_bar | stacked_area (data, fonts and frame stay; pie / scatter need a new chart), "y_steps": N
(number of value-axis intervals), and "bar_colors": one colour per category for a one-series column /
bar chart (it becomes a stacked chart with one series per colour - looks the same, single bars coloured).
A chart_data value may be null (no bar / point there).
"style" with "bullets": true makes a text box / shape a real bulleted list (hanging indent; "bullets": "–"
picks the glyph, "bullet_indent": points per level, false turns it off; indent paragraphs with a "levels" op).
"style" on a table takes "padding": 16 or [horizontal, vertical] - the cells' inner margin (default 4 pt).
"show" switches a slide's "title" | "body" | "slide_number" placeholder on or off (Keynote's
Format > Slide checkboxes); it fails on a slide whose layout has no such placeholder.
"frame" moves / resizes an object (any subset of x, y, w, h, rotation; slide points from the
top-left). Tables and charts can be moved but not resized here.
"all_slides": true (or "slides": [2, 3, 5]) instead of "slide" applies an op to many slides - with
"kind": "title" it is the quick way to unify titles, and it works for "background" too. With
all_slides, slides that lack the target are skipped and listed.
See what an object looks like now with `key_text.py deck.key --json --styles`.

Not possible here (use the Keynote app - see SKILL.md): adding, duplicating or
deleting slides and objects, tables' cells, swapping images. Slides from another deck: key_merge.py.
Close the deck in Keynote before editing it in place.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from keynote_lib import KeynoteError, KeynoteFile, set_paragraph_levels  # noqa: E402

_PARA_KEYS = ("align", "bold", "italic", "size", "font", "color", "line_spacing", "line_spacing_pt", "space_before", "space_after")


def target(doc, op: dict):
    if "slide" not in op:
        raise KeynoteError(f'{op.get("op")}: needs "slide"')
    s = doc.slide(int(op["slide"]))
    if "item" in op:
        return s, doc.item(s, int(op["item"]))
    if "id" in op:
        for d in s.walk():
            if d.id == int(op["id"]):
                return s, d
        raise KeynoteError(f"slide {s.index}: no object with id {op['id']}")
    if op.get("kind") in ("title", "body"):
        d = s.title_item if op["kind"] == "title" else s.body_item
        if d is None:
            raise KeynoteError(f"slide {s.index} has no {op['kind']} placeholder")
        return s, d
    if op.get("kind") and "match" not in op:
        hits = [d for d in s.walk() if d.kind == op["kind"]]
        if not hits:
            raise KeynoteError(f"slide {s.index} has no {op['kind']}")
        if len(hits) > 1:
            raise KeynoteError(f"slide {s.index} has {len(hits)} objects of kind {op['kind']}; use \"item\" instead")
        return s, hits[0]
    if "match" in op:
        hits = [d for d in s.walk() if d.storage is not None and op["match"] in d.text]
        if not hits:
            raise KeynoteError(f"slide {s.index}: no object containing {op['match']!r}")
        if len(hits) > 1:
            raise KeynoteError(f"slide {s.index}: {len(hits)} objects contain {op['match']!r}; use \"item\" instead")
        return s, hits[0]
    raise KeynoteError(f'{op.get("op")}: give "item", "kind", "match" or "id"')


def apply(doc, op: dict) -> str:
    kind = op.get("op")
    if kind not in ("replace", "replace_font") and (op.get("all_slides") or isinstance(op.get("slides"), list)):
        # fan one op out over many slides; with all_slides, slides lacking the target are skipped
        nums = op["slides"] if isinstance(op.get("slides"), list) else [s.index for s in doc.slides()]
        base = {k: v for k, v in op.items() if k not in ("all_slides", "slides")}
        done, skipped = [], []
        for n in nums:
            try:
                apply(doc, {**base, "slide": n})
                done.append(n)
            except KeynoteError:
                if not op.get("all_slides"):
                    raise
                skipped.append(n)
        if not done:
            raise KeynoteError(f"{kind}: no slide had a matching target")
        return f"{kind}: slides {done}" + (f" (no such target on {skipped})" if skipped else "")
    if kind == "replace":
        n = doc.replace_text(op["find"], op["replace"], op.get("slides"))
        return f"replace {op['find']!r}: {n} occurrence(s)"
    if kind == "set_notes":
        s = doc.slide(int(op["slide"]))
        doc.set_notes(s, op["text"])
        return f"slide {s.index}: notes set"
    if kind == "replace_font":
        mapping = dict(op.get("map") or {})
        if op.get("from") and op.get("to"):
            mapping[op["from"]] = op["to"]
        if not mapping:
            raise KeynoteError('replace_font: give "from" and "to", or "map": {"Old": "New"}')
        n = doc.replace_fonts(mapping)
        return f"replace_font: {n} style(s) re-pointed"
    if kind == "show":
        s = doc.slide(int(op["slide"]))
        doc.show_placeholder(s, op["what"], bool(op.get("visible", True)))
        return f"slide {s.index}: {op['what']} {'shown' if op.get('visible', True) else 'hidden'}"
    if kind == "background":
        s = doc.slide(int(op["slide"]))
        doc.set_background(s, op["color"])
        return f"slide {s.index}: background {op['color']}"
    s, d = target(doc, op)
    where = f"slide {s.index} {d.kind} {d.id}"
    if kind == "set_text":
        doc.set_text(d, op["text"])
        if "levels" in op:
            set_paragraph_levels(d.storage.msg, op["levels"])
        return f"{where}: text set"
    if kind == "levels":
        set_paragraph_levels(d.storage.msg, op["levels"])
        return f"{where}: levels set"
    if kind == "style":
        if d.kind == "table":
            if set(op) - {"op", "slide", "slides", "all_slides", "item", "kind", "match", "id", "padding"} or "padding" not in op:
                raise KeynoteError(f"{where}: a table takes only \"padding\" here (cells are styled through AppleScript)")
            n = doc.set_table_padding(d, op["padding"])
            return f"{where}: cell padding set ({n} cell styles)"
        if "fill" in op:
            doc.set_fill(d, op["fill"])
        if "stroke" in op:
            st = op["stroke"]
            doc.set_stroke(d, width=0) if st is None else doc.set_stroke(d, st.get("color"), st.get("width"))
        if "shape" in op:
            doc.set_shape(d, op["shape"], float(op.get("radius", 20)))
        if "shadow" in op:
            doc.set_shadow(d, bool(op["shadow"]))
        if "arrow_start" in op or "arrow_end" in op:
            doc.set_line_ends(d, start=op.get("arrow_start"), end=op.get("arrow_end"))
        if op.get("valign") or op.get("padding") is not None or op.get("shrink") is not None:
            doc.set_text_layout(d, valign=op.get("valign"), padding=op.get("padding"), shrink=op.get("shrink"))
        kw = {k: op[k] for k in _PARA_KEYS if op.get(k) is not None}
        if kw:
            doc.style_paragraphs(d, op.get("paragraphs"), **kw)
            if op.get("override_runs"):
                doc.override_runs(d, **{k: kw[k] for k in ("font", "size", "color", "bold", "italic") if k in kw})
        if "bullets" in op:
            b = op["bullets"]
            doc.set_bullets(d, bool(b), char=b if isinstance(b, str) else None, indent=op.get("bullet_indent"))
        return f"{where}: styled"
    if kind == "frame":
        doc.set_frame(d, **{k: op[k] for k in ("x", "y", "w", "h", "rotation") if op.get(k) is not None})
        return f"{where}: frame set"
    if kind == "style_run":
        kw = {k: op[k] for k in ("bold", "italic", "color", "size", "font", "underline") if k in op}
        n = doc.style_text(d, op["find"], occurrence=int(op.get("occurrence", 0)), **kw)
        if not n:
            raise KeynoteError(f"{where}: text {op['find']!r} not found")
        return f"{where}: styled {n} run(s)"
    if kind == "chart_data":
        if d.kind != "chart":
            raise KeynoteError(f"{where} is not a chart")
        doc.set_chart_series(d, op["categories"], op["series"])
        return f"{where}: chart data replaced"
    if kind == "chart_options":
        if d.kind != "chart":
            raise KeynoteError(f"{where} is not a chart")
        doc.set_chart_options(d, legend=op.get("legend"), title=op.get("title"), colors=op.get("colors"),
                              **{k: op[k] for k in ("y_min", "y_max", "thousands", "decimals", "font", "text_color", "text_scale", "value_labels", "value_label_scale", "value_label_color", "value_label_decimals", "value_axis", "gridlines", "chart_type", "bar_colors", "y_steps") if op.get(k) is not None})
        return f"{where}: chart options set"
    if kind == "reorder":
        doc.reorder(d, op.get("to", "front"))
        return f"{where}: moved to {op.get('to', 'front')}"
    raise KeynoteError(f"unknown op {kind!r}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("deck")
    ap.add_argument("-o", "--output", help="write the edited deck here")
    ap.add_argument("--in-place", action="store_true", help="edit the deck itself (a .backup.key copy is kept)")
    ap.add_argument("--no-backup", action="store_true", help="with --in-place: do not keep a backup")
    ap.add_argument("--replace", nargs=2, action="append", metavar=("FIND", "REPLACE"), default=[], help="literal text replacement (repeatable)")
    ap.add_argument("--slides", help="limit --replace to these slides, e.g. 1,3-5")
    ap.add_argument("--ops", help="JSON file with a list of operations (- for stdin)")
    a = ap.parse_args()
    if bool(a.output) == bool(a.in_place):
        ap.error("give exactly one of -o OUT.key or --in-place")
    if not a.replace and not a.ops:
        ap.error("nothing to do: give --replace and/or --ops")
    out = a.deck if a.in_place else a.output
    try:
        import keynote_app

        for path in {os.path.abspath(out), os.path.abspath(a.deck)} if a.in_place else {os.path.abspath(out)}:
            if os.path.exists(path) and keynote_app.is_open(path):
                print(f"error: {path} is open in Keynote. Close it there first (Keynote would overwrite this edit).", file=sys.stderr)
                return 1
    except Exception:
        pass  # no Keynote on this machine: nothing can have it open
    try:
        doc = KeynoteFile(a.deck)
        slides = None
        if a.slides:
            from key_text import parse_ranges

            slides = sorted(parse_ranges(a.slides))
        for find, rep in a.replace:
            n = doc.replace_text(find, rep, slides)
            print(f"replace {find!r}: {n} occurrence(s)")
            if n == 0:
                print(f"warning: {find!r} not found", file=sys.stderr)
        if a.ops:
            ops = json.load(sys.stdin) if a.ops == "-" else json.load(open(a.ops, encoding="utf-8"))
            if not isinstance(ops, list):
                raise KeynoteError("ops file must be a JSON list")
            fonts = {str(op[k]) for op in ops if isinstance(op, dict) for k in ("font", "to") if op.get(k) and (k == "font" or op.get("op") == "replace_font")}
            fonts |= {str(v) for op in ops if isinstance(op, dict) and op.get("op") == "replace_font" for v in (op.get("map") or {}).values()}
            if fonts:
                try:
                    import keynote_app

                    for f in sorted(fonts):
                        problem = keynote_app.font_problem(f)
                        if problem:
                            print(f"warning: {problem}", file=sys.stderr)
                except Exception:
                    pass
            for i, op in enumerate(ops, 1):
                try:
                    print(apply(doc, op))
                except (KeynoteError, KeyError, TypeError, ValueError) as e:
                    msg = f"missing key {e}" if isinstance(e, KeyError) else str(e)
                    raise KeynoteError(f"op {i} ({op.get('op')}): {msg}; nothing was written")
        if a.in_place and not a.no_backup:
            root, ext = os.path.splitext(a.deck.rstrip("/"))
            backup = f"{root}.backup{ext}"
            if os.path.isdir(a.deck):
                if os.path.exists(backup):
                    shutil.rmtree(backup)
                shutil.copytree(a.deck, backup, symlinks=True)
            else:
                shutil.copy2(a.deck, backup)
            print(f"backup: {backup}")
        doc.save(out, overwrite=a.in_place)
    except KeynoteError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as e:
        print(f"error: ops file is not valid JSON: {e}", file=sys.stderr)
        return 1
    print(os.path.abspath(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
