#!/usr/bin/env python3
"""Dump a Keynote deck's content as markdown (or JSON) straight from the .key file.

Keynote does not need to be running or even installed.

    python key_text.py deck.key                # markdown, one block per slide
    python key_text.py deck.key --slides 3-5,9 # only these slides
    python key_text.py deck.key --json         # every object: item number, id, kind, frame, text
    python key_text.py deck.key --json --styles  # + fill, stroke, font, size, color, alignment ...
    python key_text.py deck.key --layouts      # the theme's slide layouts

Each slide starts with a `<!-- Slide number: N | layout: INDEX NAME -->` marker
(INDEX is what AppleScript's `slide layout INDEX` and key_build's "layout"
take). A title that is switched off for the slide (text left in the file but
not drawn) appears as a comment instead of a heading. Table cells read as
displayed (1,234 / 45.60%), hand-bolded cells as **bold**, and a table's
visible name as a "Table: NAME" line. A frame with w/h 0 is an auto-sized text
box: only a render shows its real extent. A table's frame is its real drawn
size (sum of column widths x sum of row heights, as Keynote reports it) - not
the stale size stored with the table. A table whose name is shown gets
"caption_h": the name band at the top of the frame (the grid starts that much
lower); "caption_h": null means the file does not record it, and the frame's
h then covers the grid only. Text that does not fit a row is clipped, not
counted: the row height is what is drawn.
Output covers the title (line breaks joined), the body, then the other
objects top-to-bottom and left-to-right: text boxes and shapes (indented
bullets where the text is nested), tables, chart data, image/movie file names
with their alt text - and presenter notes. `--json` lists objects back to
front instead, numbered as "item".
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from keynote_lib import ATTACHMENT_CHAR, KeynoteError, KeynoteFile, storage_paragraphs  # noqa: E402


def parse_ranges(spec: str | None) -> set[int] | None:
    if not spec:
        return None
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        elif part:
            out.add(int(part))
    return out


def clean(text: str) -> str:
    return text.replace(ATTACHMENT_CHAR, "").strip()


def md_escape_cell(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", "<br>")


def paragraphs_md(storage, bullets: bool) -> list[str]:
    lines = []
    for level, text in storage_paragraphs(storage.msg):
        text = clean(text)
        if not text:
            continue
        text = text.replace("\n", " ")
        lines.append(("  " * level + "- " + text) if bullets else text)
    return lines


def position_key(d):
    f = d.frame or (0, 0, 0, 0)
    return (round(f[1] / 40), f[0])


def table_md(rows: list[list[str]]) -> list[str]:
    if not rows or not rows[0]:
        return []
    out = ["| " + " | ".join(md_escape_cell(c) for c in rows[0]) + " |", "|" + " --- |" * len(rows[0])]
    for r in rows[1:]:
        out.append("| " + " | ".join(md_escape_cell(c) for c in r) + " |")
    return out


def drawable_md(doc, d, out: list[str]) -> None:
    if d.kind in ("title", "slide_number"):
        return
    if d.kind == "group":
        for c in sorted(d.children, key=position_key):
            drawable_md(doc, c, out)
        return
    if d.kind == "table":
        try:
            bold: list = []
            rows = doc.table_cells(d, bold)
            for r, c in bold:  # hand-applied emphasis
                if rows[r][c]:
                    rows[r][c] = f"**{rows[r][c]}**"
        except KeynoteError as e:
            out += ["", f"[table: {e}]"]
            return
        name = doc.table_name(d)
        out += ["", *([f"Table: {name}", ""] if name else []), *table_md(rows)]
        return
    if d.kind == "chart":
        data = doc.chart_series(d)
        if data:
            out += ["", f"### Chart ({data['type']})"]
            rows = [[""] + [str(c) for c in data["categories"]]]
            for sr in data["series"]:
                rows.append([sr["name"]] + ["" if v is None else f"{v:g}" for v in sr["values"]])
            out += table_md(rows)
        return
    if d.kind in ("image", "movie", "media", "gallery"):
        name = d.data_name or "?"
        desc = clean(d.description)
        out += ["", f"![{desc}]({name})" if d.kind == "image" else f"[{d.kind}: {name}]"]
        return
    st = d.storage
    if st is None:
        return
    nested = any(lv for lv, _ in storage_paragraphs(st.msg))
    lines = paragraphs_md(st, bullets=(d.kind == "body" or nested))
    if lines:
        out += ["", *lines]


def slide_md(doc, s) -> str:
    flags = []
    if s.skipped:
        flags.append("skipped")
    if s.layout_name:
        n = doc.layout_index(s)
        flags.append(f"layout: {n} {s.layout_name}" if n else f"layout: {s.layout_name}")
    out = [f"<!-- Slide number: {s.index}" + ("".join(f" | {f}" for f in flags)) + " -->"]
    title = clean(s.title).replace("\n", " ")
    if title and s.title_visible:
        out.append(f"# {title}")
    elif title:
        out.append(f"<!-- title switched off, not shown on the slide (leftover text): {title} -->")
    ds = s.drawables
    body = [d for d in ds if d.kind == "body"]
    rest = sorted((d for d in ds if d.kind not in ("title", "body")), key=position_key)
    for d in body + rest:
        drawable_md(doc, d, out)
    notes = clean(s.notes)
    if notes:
        out += ["", "### Notes:", notes]
    return "\n".join(out)


STYLES = False


def drawable_json(doc, d, item: int | None = None) -> dict:
    f = d.frame
    j = {"id": d.id, "kind": d.kind, "type": d.obj.type}
    if item is not None:
        j = {"item": item, **j}
    if f:
        j["frame"] = {"x": round(f[0], 1), "y": round(f[1], 1), "w": round(f[2], 1), "h": round(f[3], 1)}
        g = d.geometry
        if g is not None and g.angle:
            j["rotation"] = round(g.angle, 1)
    st = d.storage
    if st is not None:
        j["text"] = d.text
        levels = [lv for lv, _ in storage_paragraphs(st.msg)]
        if any(levels):
            j["levels"] = levels
        j["storage_id"] = st.id
    if d.kind in ("image", "movie"):
        j["file"] = d.data_name
        if d.description:
            j["description"] = d.description
    if d.kind == "table":
        try:
            if doc.table_name(d):
                j["name"] = doc.table_name(d)
                cap = doc.table_caption_height(d)
                j["caption_h"] = None if cap is None else round(cap, 1)
            bold: list = []
            j["cells"] = doc.table_cells(d, bold)
            if bold:
                j["bold_cells"] = [list(rc) for rc in bold]
        except KeynoteError as e:
            j["error"] = str(e)
    if d.kind == "chart":
        j["chart"] = doc.chart_series(d)
    if STYLES:
        try:
            info = doc.style_info(d)
        except Exception as e:  # never let a style quirk break the dump
            info = {"error": str(e)}
        if info:
            j["style"] = info
    if d.children:
        j["children"] = [drawable_json(doc, c) for c in d.children]
    return j


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("deck")
    ap.add_argument("--slides", help="e.g. 1,3-5 (default: all)")
    ap.add_argument("--json", action="store_true", help="structured output with object ids and frames")
    ap.add_argument("--styles", action="store_true", help="with --json: each object's resolved look (fill, stroke, font, size, color, bold, align, shrink_to_fit, per-paragraph and per-run differences)")
    ap.add_argument("--layouts", action="store_true", help="list the theme's slide layouts instead of slides")
    ap.add_argument("--no-skipped", action="store_true", help="leave out skipped slides")
    a = ap.parse_args()
    global STYLES
    STYLES = a.styles
    if a.styles:
        a.json = True
    try:
        doc = KeynoteFile(a.deck)
    except KeynoteError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if a.layouts:
        for i, lay in enumerate(doc.layouts(), 1):
            kinds = [d.kind for d in doc._slide_drawables(lay)]
            ph = ", ".join(k for k in kinds if k in ("title", "body", "object_placeholder", "slide_number")) or "-"
            extra = sum(1 for k in kinds if k not in ("title", "body", "object_placeholder", "slide_number"))
            print(f"{i}. {lay.msg.name}  [placeholders: {ph}" + (f"; {extra} other objects" if extra else "") + "]")
        return 0
    want = parse_ranges(a.slides)
    slides = [s for s in doc.slides() if (want is None or s.index in want) and not (a.no_skipped and s.skipped)]
    if a.json:
        w, h = doc.size
        out = {
            "file_format_version": doc.format_version,
            "slide_size": {"w": w, "h": h},
            "slides": [
                {
                    "index": s.index,
                    "id": s.id,
                    "layout": s.layout_name,
                    "layout_index": doc.layout_index(s),
                    "skipped": s.skipped,
                    "title": s.title,
                    "title_visible": s.title_visible,
                    "slide_number_visible": doc.placeholder_visible(s, "slide_number"),
                    "notes": s.notes,
                    **({"background": doc.background_info(s)} if STYLES else {}),
                    "items": [drawable_json(doc, d, i) for i, d in enumerate(s.drawables, 1)],
                }
                for s in slides
            ],
        }
        json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
        print()
        return 0
    print("\n\n".join(slide_md(doc, s) for s in slides))
    return 0


if __name__ == "__main__":
    sys.exit(main())
