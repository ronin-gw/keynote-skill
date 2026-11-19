#!/usr/bin/env python3
"""Import a PowerPoint file (.pptx / .ppt) as an editable Keynote deck.

    python key_import.py deck.pptx out.key              # Keynote's own importer + the usual repairs
    python key_import.py deck.pptx out.key --app 13     # with a specific Keynote (decides the file format)
    python key_import.py deck.pptx out.key --font-map "Helvetica=YuGo-Medium,Helvetica-Bold=YuGo-Bold"
    python key_import.py deck.pptx out.key --keep-spacing

Keynote does the conversion (text, shapes, tables, pictures, notes stay
editable); this script drives it safely - it never touches other documents
open in Keynote - and then:

  * converts line spacing to PowerPoint's (a "1.0" line in PowerPoint is about
    1.2 x the font size; Keynote uses the font's own, taller metrics, so every
    imported text block grows ~20% and overflows). `--keep-spacing` skips this.
  * reports what the importer is known to lose or change, so you know where to
    look: charts that did not come across (rebuild them natively), fonts the
    pptx names that this Mac lacks (Keynote substituted them), slide counts.

It prints a report; fix the listed slides, then render and compare. To get a
reference picture of the original, export the pptx to PDF from PowerPoint or
LibreOffice (`soffice --headless --convert-to pdf deck.pptx`) if one is
installed - Keynote cannot render a pptx without converting it.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import keynote_app  # noqa: E402
from keynote_lib import KeynoteError, KeynoteFile  # noqa: E402


def pptx_facts(path: str) -> dict:
    """{'slides': n, 'charts': {slide_no: count}, 'fonts': set, 'notes': n} read from the pptx zip."""
    out = {"slides": 0, "charts": {}, "fonts": set(), "notes": 0}
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        return out  # legacy .ppt
    names = z.namelist()
    order = []
    try:
        pres = z.read("ppt/presentation.xml").decode("utf-8", "replace")
        rels = z.read("ppt/_rels/presentation.xml.rels").decode("utf-8", "replace")
        target = dict(re.findall(r'<Relationship\b[^>]*\bId="([^"]+)"[^>]*\bTarget="([^"]+)"', rels))
        target.update({i: t for t, i in re.findall(r'<Relationship\b[^>]*\bTarget="([^"]+)"[^>]*\bId="([^"]+)"', rels)})
        for rid in re.findall(r'<p:sldId\b[^>]*r:id="([^"]+)"', pres):
            if rid in target:
                order.append("ppt/" + target[rid].lstrip("/").replace("ppt/", ""))
    except KeyError:
        pass
    if not order:
        order = sorted((n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)), key=lambda n: int(re.findall(r"\d+", n)[-1]))
    out["slides"] = len(order)
    for no, name in enumerate(order, 1):
        rel = name.replace("slides/", "slides/_rels/") + ".rels"
        if rel in names:
            r = z.read(rel).decode("utf-8", "replace")
            n = len(re.findall(r'Type="[^"]*/relationships/chart"', r))
            if n:
                out["charts"][no] = n
            for tgt in re.findall(r'Type="[^"]*/relationships/notesSlide"[^>]*Target="([^"]+)"', r) + re.findall(r'Target="([^"]+)"[^>]*Type="[^"]*/relationships/notesSlide"', r):
                npath = os.path.normpath(os.path.join(os.path.dirname(name), tgt)).replace(os.sep, "/")
                if npath in names:
                    xml = z.read(npath).decode("utf-8", "replace")
                    body = "".join(sp for sp in xml.split("<p:sp>") if 'type="body"' in sp)
                    if "".join(re.findall(r"<a:t>([^<]*)</a:t>", body)).strip():
                        out["notes"] += 1
        if name in names:
            out["fonts"].update(re.findall(r'typeface="([^"+][^"]*)"', z.read(name).decode("utf-8", "replace")))
    for name in names:
        if re.fullmatch(r"ppt/theme/theme\d+\.xml", name):
            xml = z.read(name).decode("utf-8", "replace")
            out["fonts"].update(t for t in re.findall(r'<a:(?:latin|ea)\b[^>]*typeface="([^"+][^"]*)"', xml) if t)
    return out


def convert(src: str, dst: str, app: dict) -> None:
    """Have Keynote open `src` and save it as `dst`, addressing only that document."""
    tmpdir = tempfile.mkdtemp(prefix="key_import_")
    stem = f"kimport{os.getpid()}"
    staged = os.path.join(tmpdir, stem + os.path.splitext(src)[1].lower())
    shutil.copyfile(src, staged)
    try:
        keynote_app.ensure_running(app)
        # `open` through LaunchServices: Keynote's AppleScript `open` silently ignores non-.key files on some versions
        subprocess.run(["open", "-g", "-b", app["id"], staged], check=True)
        keynote_app.run_applescript(
            '''
set stem to item 1 of argv
set dst to POSIX file (item 2 of argv)
tell {{APP}}
    set d to missing value
    repeat 600 times
        try
            set d to (first document whose name starts with stem)
            exit repeat
        end try
        delay 0.5
    end repeat
    if d is missing value then error "Keynote did not open the file (is an import dialog waiting in Keynote?)"
    delay 1
    save d in dst
    close d saving no
end tell''',
            [stem, dst], app, timeout=900,
        )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help=".pptx or .ppt")
    ap.add_argument("out", help="the .key file to write")
    ap.add_argument("--app", help="16, 14, 13 ... or a bundle id (default: the oldest installed Keynote, so every installed one can open the result)")
    ap.add_argument("--font-map", help='re-point fonts after import, e.g. "Helvetica=YuGo-Medium,Helvetica-Bold=YuGo-Bold"')
    ap.add_argument("--keep-spacing", action="store_true", help="leave Keynote's line spacing as imported")
    a = ap.parse_args()
    src, dst = os.path.abspath(a.source), os.path.abspath(a.out)
    if not os.path.isfile(src):
        print(f"error: no such file: {a.source}", file=sys.stderr)
        return 2
    if not dst.endswith(".key"):
        print("error: output must end in .key", file=sys.stderr)
        return 2
    if os.path.exists(dst):
        print(f"error: {a.out} exists; remove it or choose another name", file=sys.stderr)
        return 2
    try:
        app = keynote_app.choose(a.app, new_deck=True)
        facts = pptx_facts(src)
        convert(src, dst, app)
        doc = KeynoteFile(dst)
        slides = doc.slides()
        print(f"imported with Keynote {app['version']}: {len(slides)} slides, file format {doc.format_version}")
        if len(keynote_app.installed()) > 1 and not a.app:
            newest = keynote_app.installed()[0]["version"].split(".")[0]
            print(f"  (made by the oldest installed Keynote so that both can open it; --app {newest} imports with the newest instead)")
        notes = []
        if facts["slides"] and facts["slides"] != len(slides):
            notes.append(f"the pptx has {facts['slides']} slides, the deck {len(slides)}")
        for s in slides:
            want = facts["charts"].get(s.index, 0)
            have = sum(1 for d in s.walk() if d.kind == "chart")
            if want > have:
                notes.append(f"slide {s.index}: {want - have} chart(s) did not come across - rebuild natively "
                             "(AppleScript `add chart`, then key_edit.py chart_options); the numbers are in the pptx's ppt/charts/*.xml")
        have_notes = sum(1 for s in slides if s.notes.strip())
        if facts["notes"] and have_notes < facts["notes"]:
            notes.append(f"the pptx has speaker notes on {facts['notes']} slides, the deck on {have_notes}: some notes were lost")
        _ps, families = keynote_app.installed_fonts()
        if families:
            low = {f.lower() for f in families}
            missing = sorted(f for f in facts["fonts"] if f.lower() not in low and not f.startswith("+"))
            if missing:
                notes.append("fonts named in the pptx but not installed here (Keynote substituted them - see 'fonts in the imported deck'; text will reflow): "
                             + ", ".join(missing) + ". Re-point the substitutes to the closest installed faces in one go: "
                             "--font-map \"Helvetica=YuGo-Medium,Helvetica-Bold=YuGo-Bold\" here, or key_edit.py's replace_font op "
                             "(Windows 'Yu Gothic' = macOS 'YuGo-Medium' / 'YuGo-Bold'; find names with keynote_app.py fonts WORD)")
        if a.font_map:
            pairs = dict(p.split("=", 1) for p in a.font_map.split(",") if "=" in p)
            for dst_font in pairs.values():
                problem = keynote_app.font_problem(dst_font.strip())
                if problem:
                    print(f"warning: {problem}", file=sys.stderr)
            n = doc.replace_fonts({k.strip(): v.strip() for k, v in pairs.items()})
            doc.save(dst, overwrite=True)
            print(f"fonts: {n} style(s) re-pointed ({a.font_map})")
        used = doc.fonts_used()
        if used:
            print("fonts in the imported deck: " + ", ".join(f"{name} x{cnt}" for name, cnt in used[:12]))
        if not a.keep_spacing:
            n = doc.exact_line_spacing()
            doc.save(dst, overwrite=True)
            print(f"line spacing: {n} paragraphs set to PowerPoint's line height (font size x 1.2 x multiple)")
        for n in notes:
            print(f"CHECK: {n}")
        print("next: render both and compare slide by slide (key_render.py; key_check.py --layout lists overlaps and overflow)")
        print(dst)
    except (KeynoteError, keynote_app.KeynoteAppError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
