#!/usr/bin/env python3
"""Check that a .key file is structurally sound - run it on every deck you edit directly.

    python key_check.py out.key                       # structural checks on the file
    python key_check.py out.key --original src.key    # report only problems src.key did not already have
    python key_check.py out.key --open                # also have Keynote open it and count the slides
    python key_check.py out.key --layout [--slides 3-5]  # + objects past the slide edge, overlapping text boxes,
                                                      #   table rows too short for their text

Checks: the package unzips, every Index/*.iwa decodes, references point at
objects that exist, the slide list is intact, text attribute tables stay
inside their text and in order, media references resolve to files, and the
package metadata knows every object file. Exit code 0 = no problems.

A pass is not proof Keynote is happy: `--open` is the real test, and a render
(key_render.py) the real visual one.

--layout's table check reads the text height Keynote measured for each row
when it last saved the deck: a row whose text is cut off (a header that wraps
or holds a line break in a row kept one line tall) is reported. It is blind to
changes made in the file since Keynote last saved it (key_edit.py "replace" in
a cell, table "padding") - render to be sure after those. Text clipped inside a
shape or placeholder is not detected.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from keynote_lib import PARA_BREAKS, KeynoteError, KeynoteFile, _attr_tables, type_name, u16len  # noqa: E402


def _refs(msg, path=""):
    """Yield (field_path, identifier) for every TSP.Reference inside msg."""
    for fd, value in msg.ListFields():
        if fd.message_type is None:
            continue
        if fd.message_type.GetOptions().map_entry:
            continue
        items = value if fd.is_repeated else [value]
        name = f"{path}.{fd.name}" if path else fd.name
        if fd.message_type.full_name == "TSP.Reference":
            for v in items:
                yield name, int(v.identifier)
        else:
            for v in items:
                yield from _refs(v, name)


# Keynote itself leaves references to culled objects in these; they are weak by design.
_WEAK_HOLDERS = {"TSS.StylesheetArchive", "KN.UIStateArchive", "TSK.AnnotationAuthorStorageArchive"}


def check(path: str) -> list[str]:
    """Return a list of problem strings (empty = sound)."""
    try:
        doc = KeynoteFile(path)
    except KeynoteError as e:
        return [f"cannot read: {e}"]
    except Exception as e:  # zip / decode failure
        return [f"cannot read: {type(e).__name__}: {e}"]
    problems: list[str] = []

    # references (archives of unknown type are kept verbatim and skipped here)
    for o in doc.objects.values():
        if not hasattr(o.msg, "ListFields") or o.type in _WEAK_HOLDERS:
            continue
        for field, ident in _refs(o.msg):
            if ident and ident not in doc.objects:
                problems.append(f"dangling reference: {o.type} {o.id} .{field} -> {ident}")

    # slide list
    try:
        show = doc.first("KN.ShowArchive")
        node_ids = doc._slide_node_ids()
        if not node_ids:
            problems.append("deck has no slides")
        seen = set()
        for nid in node_ids:
            node = doc.get(nid)
            if node is None or node.type != "KN.SlideNodeArchive":
                problems.append(f"slide list entry {nid} is not a slide node")
                continue
            if nid in seen:
                problems.append(f"slide node {nid} is listed twice")
            seen.add(nid)
            so = doc.get(node.msg.slide.identifier)
            if so is None or so.type != "KN.SlideArchive":
                problems.append(f"slide node {nid} points at a missing slide {node.msg.slide.identifier}")
                continue
            owned = {int(r.identifier) for r in so.msg.owned_drawables}
            z = [int(r.identifier) for r in so.msg.drawables_z_order]
            if z and (set(z) != owned or len(z) != len(set(z))):
                problems.append(f"slide {so.id}: drawables_z_order and owned_drawables disagree")
        _ = show
    except KeynoteError as e:
        problems.append(str(e))

    # text storages
    for st in doc.find("TSWP.StorageArchive"):
        msg = st.msg
        text = msg.text[0] if len(msg.text) else ""
        n = u16len(text)
        starts = {0}
        pos = 0
        for ch in text:
            pos += u16len(ch)
            if ch in PARA_BREAKS:
                starts.add(pos)
        for name, table in _attr_tables(msg):
            entries = list(table.entries)
            if not entries or not hasattr(entries[0], "character_index"):
                continue
            idx = [e.character_index for e in entries]
            if idx != sorted(idx):
                problems.append(f"text storage {st.id}: {name} entries out of order")
            if len(idx) != len(set(idx)):
                problems.append(f"text storage {st.id}: {name} has two entries at one index")
            if idx and idx[-1] > n:
                problems.append(f"text storage {st.id}: {name} index {idx[-1]} is past the end of the text ({n})")
            if name == "table_para_style" and idx and idx[0] != 0:
                problems.append(f"text storage {st.id}: no paragraph style at index 0")
            if name in ("table_para_style", "table_para_data", "table_list_style"):
                off = [i for i in idx if i not in starts]
                if off:
                    problems.append(f"text storage {st.id}: {name} entry at {off[0]} is not at a paragraph start")

    # metadata
    metas = doc.find("TSP.PackageMetadata")
    if not metas:
        problems.append("no TSP.PackageMetadata (Index/Metadata.iwa)")
    else:
        meta = metas[0].msg
        top = max(doc.objects)
        if meta.last_object_identifier < top:
            problems.append(f"metadata last_object_identifier {meta.last_object_identifier} < highest object id {top}")
        comp_ids = {int(c.identifier) for c in meta.components}
        for name, f in doc.files.items():
            if name.endswith("Metadata.iwa"):
                continue
            root = int(f.chunks[0].archives[0].header.identifier)
            if root not in comp_ids:
                problems.append(f"{name}: root object {root} has no component entry in the metadata")
        import unicodedata

        files = {unicodedata.normalize("NFC", n) for n in doc.names}
        for d in meta.datas:
            fn = unicodedata.normalize("NFC", d.file_name or d.preferred_file_name)
            if fn and not d.HasField("remote_url") and f"Data/{fn}" not in files and not d.document_resource_locator:
                problems.append(f"media file missing from package: Data/{fn}")

    # archive headers: declared object references exist
    for o in doc.objects.values():
        for mi in o.archive.header.message_infos:
            for r in mi.object_references:
                if int(r) not in doc.objects:
                    problems.append(f"{o.type} {o.id}: header lists missing object {int(r)}")
    return problems


_opened_with = ""


def open_check(path: str, app_choice=None) -> list[str]:
    import keynote_app

    global _opened_with

    try:
        from keynote_lib import KeynoteFile as KF

        expect = len(KF(path).slides())
    except Exception:
        expect = None
    try:
        app = keynote_app.choose(app_choice, None if app_choice else path)
        _opened_with = f"Keynote {app['version']}"
        out = keynote_app.run_applescript(
            '''
set {d, wasOpen} to my openDeck(item 1 of argv)
tell {{APP}}
    set n to count of slides of d
    if not wasOpen then close d saving no
end tell
return n as text''',
            [os.path.realpath(path)], app, timeout=300,
        )
    except keynote_app.KeynoteAppError as e:
        return [f"Keynote could not open it: {e}"]
    if expect is not None and int(out) != expect:
        return [f"Keynote sees {out} slides, the file lists {expect}"]
    return []


def layout_report(path: str, app_choice: str | None = None, slides: set[int] | None = None) -> list[str]:
    """Ask Keynote for every object's laid-out frame and report what a glance at
    a thumbnail misses: objects past the slide edge and text boxes on top of each
    other. (Auto-sized text boxes have no size in the file - only the app knows.)"""
    import keynote_app

    doc = KeynoteFile(path)
    W, H = doc.size
    app = keynote_app.choose(app_choice, None if app_choice else path)
    out = keynote_app.run_applescript(
        '''
set {d, wasOpen} to my openDeck(item 1 of argv)
set out to ""
tell {{APP}}
    repeat with i from 1 to (count of slides of d)
        set n to count of iWork items of slide i of d
        repeat with j from 1 to n
            try
                set o to iWork item j of slide i of d
                set {px, py} to position of o
                set out to out & i & tab & j & tab & px & tab & py & tab & (width of o) & tab & (height of o) & linefeed
            end try
        end repeat
    end repeat
    if not wasOpen then close d saving no
end tell
return out''',
        [os.path.realpath(path)], app, timeout=900,
    )
    frames: dict[tuple[int, int], tuple[float, float, float, float]] = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 6:
            try:
                frames[(int(parts[0]), int(parts[1]))] = tuple(float(v.replace(",", ".")) for v in parts[2:])
            except ValueError:
                pass
    notes: list[str] = []
    for s in doc.slides():
        if slides and s.index not in slides:
            continue
        items = []
        for n, d in enumerate(s.drawables, 1):
            f = frames.get((s.index, n))
            if f is None:
                continue
            text = d.text.replace("\n", " ").strip() if d.storage is not None else ""
            label = f"item {n} {d.kind}" + (f" {text[:24]!r}" if text else "")
            x, y, w, h = f
            if d.kind != "slide_number" and w > 0 and h > 0:
                over = max(-x, -y, x + w - W, y + h - H)
                if over > 2:
                    side = "left" if -x == over else "top" if -y == over else "right" if x + w - W == over else "bottom"
                    notes.append(f"slide {s.index}: {label} runs {over:.0f} pt past the {side} edge  (frame {x:.0f},{y:.0f} {w:.0f}x{h:.0f})")
            if text and d.kind in ("text", "title", "body"):
                items.append((label, f))
            if d.kind == "table":
                try:
                    cut = [(r, size, need) for r, size, need in doc.table_row_fits(d) if need > size + 1]
                except Exception:
                    cut = []
                for r, size, need in cut:
                    notes.append(f"slide {s.index}: item {n} table: row {r + 1} is {size:.0f} pt tall but its text needs {need:.0f} pt "
                                 "- the text is cut off (Keynote's own measurement, saved in the file)")
        for a_i in range(len(items)):
            for b_i in range(a_i + 1, len(items)):
                (la, (ax, ay, aw, ah)), (lb, (bx, by, bw, bh)) = items[a_i], items[b_i]
                ix = min(ax + aw, bx + bw) - max(ax, bx)
                iy = min(ay + ah, by + bh) - max(ay, by)
                if ix > 2 and iy > 2 and ix * iy >= 0.15 * min(aw * ah, bw * bh):
                    notes.append(f"slide {s.index}: {la} and {lb} overlap by {ix:.0f}x{iy:.0f} pt")
    return notes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("deck")
    ap.add_argument("--original", help="the deck this one was derived from; its own problems are not reported")
    ap.add_argument("--open", action="store_true", help="also open the deck in Keynote")
    ap.add_argument("--layout", action="store_true", help="also list objects past the slide edge, overlapping text boxes and table rows too short for their text, measured by Keynote")
    ap.add_argument("--slides", help="with --layout: only these slides, e.g. 2,5-7")
    ap.add_argument("--app", help="a major version (16, 14 ...) or a bundle id (for --open / --layout)")
    a = ap.parse_args()
    if not os.path.exists(a.deck):
        print(f"error: no such file: {a.deck}", file=sys.stderr)
        return 2
    problems = check(a.deck)
    if a.original:
        base = set(check(a.original))
        inherited = [p for p in problems if p in base]
        problems = [p for p in problems if p not in base]
        if inherited:
            print(f"({len(inherited)} problem(s) already present in {os.path.basename(a.original)} not shown)")
    if a.open and not any(p.startswith("cannot read") for p in problems):
        problems += open_check(a.deck, a.app)
    if problems:
        shown = problems[:40]
        for p in shown:
            print(f"FAIL: {p}")
        if len(problems) > len(shown):
            print(f"... and {len(problems) - len(shown)} more")
        return 1
    if a.layout:
        want = None
        if a.slides:
            want = set()
            for part in a.slides.split(","):
                lo, _, hi = part.partition("-")
                want.update(range(int(lo), int(hi or lo) + 1))
        try:
            notes = layout_report(a.deck, a.app, want)
            if a.original and notes:
                import re as _re

                def key(n):  # the same finding, give or take a point of rounding
                    return _re.sub(r"\d+(?:\.\d+)?", "#", n)

                before = {key(n) for n in layout_report(a.original, a.app, None)}
                old = [n for n in notes if key(n) in before]
                notes = [n for n in notes if key(n) not in before]
                if old:
                    print(f"({len(old)} layout finding(s) already present in {os.path.basename(a.original)} not shown)")
        except Exception as e:  # the app is optional for this script
            notes = [f"layout check not run: {e}"]
        for n in notes:
            print(f"LAYOUT: {n}")
        if not notes:
            print("LAYOUT: nothing past the slide edge, no overlapping text boxes, no cut-off table rows")
        else:
            print("(LAYOUT lines are leads, not verdicts: boxes are compared, not glyphs - a full-width title box "
                  "'overlaps' a label beside a short title. Text clipped inside a shape or placeholder is NOT "
                  "detected - look at the render; a table row is checked as Keynote last saved it.)")
    fmt = KeynoteFile(a.deck).format_version
    print(f"OK: {a.deck} (file format {fmt}" + (f"; opens in {_opened_with})" if a.open else ")"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
