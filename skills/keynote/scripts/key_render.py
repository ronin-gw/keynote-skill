#!/usr/bin/env python3
"""Render a Keynote deck to slide images, a PDF, or a labeled thumbnail grid.

Uses the Keynote app (it is the only faithful renderer); the app is launched
if needed and the deck is closed again afterwards unless it was already open.

    python key_render.py deck.key out/                 # out/slide-001.png, slide-002.png, ...
    python key_render.py deck.key out/ --slides 2,5-7  # keep only these
    python key_render.py deck.key out/ --jpeg          # smaller files
    python key_render.py deck.key --pdf deck.pdf       # one PDF (add --notes for slides with presenter notes)
    python key_render.py deck.key --grid thumbs.jpg    # contact sheet labeled with slide numbers
    python key_render.py deck.key --layouts lay.jpg    # contact sheet of the deck's slide layouts

Image numbers are deck slide numbers: slide-007.png is slide 7 even when
earlier slides are skipped (skipped slides are rendered too, unless
--no-skipped). Earlier slide-NNN images in the folder are removed first. Prints
the files it wrote, absolute paths, one per line.
"""

from __future__ import annotations

import argparse
import glob
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import keynote_app  # noqa: E402

_SCRIPT = r'''
on run argv
    set deckPath to item 1 of argv
    set outPath to item 2 of argv
    set exportKind to item 3 of argv
    set withSkipped to (item 4 of argv) is "1"
    with timeout of 1800 seconds
        set {d, wasOpen} to my openDeck(deckPath)
        tell {{APP}}
            try
                if exportKind is "pdf" then
                    export d to (POSIX file outPath) as PDF with properties {PDF image quality:Best, skipped slides:withSkipped}
                else if exportKind is "pdfnotes" then
                    export d to (POSIX file outPath) as PDF with properties {PDF image quality:Best, skipped slides:withSkipped, export style:SlideWithNotes}
                else if exportKind is "jpeg" then
                    export d to (POSIX file outPath) as slide images with properties {image format:JPEG, compression factor:0.9, skipped slides:withSkipped}
                else
                    export d to (POSIX file outPath) as slide images with properties {image format:PNG, skipped slides:withSkipped}
                end if
            on error msg number n
                if not wasOpen then close d saving no
                error msg number n
            end try
            set skippedList to ""
            set total to count of slides of d
            repeat with i from 1 to total
                if skipped of slide i of d then set skippedList to skippedList & i & ","
            end repeat
            if not wasOpen then close d saving no
        end tell
        return (total as text) & "|" & skippedList
    end timeout
end run
'''


def parse_ranges(spec):
    if not spec:
        return None
    out = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        elif part:
            out.add(int(part))
    return out


def export_images(deck: str, outdir: str, fmt: str = "png", slides=None, with_skipped: bool = True, app=None) -> list[tuple[int, str]]:
    """Export slide images; returns [(slide_number, path)]."""
    app = app or keynote_app.choose(for_file=deck)
    deck = os.path.realpath(deck)
    outdir = os.path.abspath(outdir)
    os.makedirs(outdir, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="keyrender-", dir=outdir)
    try:
        res = keynote_app.run_applescript(_SCRIPT, [deck, tmp, fmt, "1" if with_skipped else "0"], app, timeout=1900)
        total, _, skipped_s = res.partition("|")
        files = sorted(glob.glob(os.path.join(tmp, "*.*")))
        n_total = int(total)
        skipped = {int(x) for x in skipped_s.split(",") if x}
        # Keynote leaves skipped slides out of image exports whatever `skipped slides` says
        if len(files) == n_total:
            numbers = list(range(1, n_total + 1))
        elif len(files) == n_total - len(skipped):
            numbers = [i for i in range(1, n_total + 1) if i not in skipped]
        else:
            raise keynote_app.KeynoteAppError(f"exported {len(files)} images for {n_total} slides ({len(skipped)} skipped)")
        ext = "jpg" if fmt == "jpeg" else "png"
        for old in glob.glob(os.path.join(outdir, "slide-[0-9][0-9][0-9].*")):
            # images from an earlier render of a deck that has since changed; with --slides only
            # the slides being redrawn are replaced, the others stay to be looked at
            if slides is None or int(os.path.basename(old)[6:9]) in slides:
                os.remove(old)
        out = []
        for num, f in zip(numbers, files):
            if slides is not None and num not in slides:
                continue
            dst = os.path.join(outdir, f"slide-{num:03d}.{ext}")
            os.replace(f, dst)
            out.append((num, os.path.abspath(dst)))
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def export_pdf(deck: str, pdf: str, notes: bool = False, with_skipped: bool = False, app=None) -> str:
    app = app or keynote_app.choose(for_file=deck)
    pdf = os.path.abspath(pdf)
    os.makedirs(os.path.dirname(pdf), exist_ok=True)
    keynote_app.run_applescript(
        _SCRIPT, [os.path.realpath(deck), pdf, "pdfnotes" if notes else "pdf", "1" if with_skipped else "0"], app, timeout=1900
    )
    return pdf


_LAYOUT_SCRIPT = r'''
on run argv
    set deckPath to item 1 of argv
    with timeout of 1800 seconds
        set {d, wasOpen} to my openDeck(deckPath)
        tell {{APP}}
            tell d
                set nOld to count of slides
                set nLay to count of slide layouts
                repeat with i from 1 to nLay
                    set s to make new slide with properties {base layout:slide layout i}
                    try
                        set object text of default title item of s to "Title"
                    end try
                    try
                        set object text of default body item of s to "Body text" & linefeed & "Second point"
                    end try
                end repeat
                repeat nOld times
                    delete slide 1
                end repeat
            end tell
            save d
            close d saving no
        end tell
    end timeout
end run
'''


def layout_grid(deck: str, out: str, app=None) -> list[str]:
    """Contact sheet with one sample slide per layout of `deck` (the deck itself is not touched)."""
    from keynote_lib import KeynoteFile

    app = app or keynote_app.choose(for_file=deck)
    work = tempfile.mkdtemp(prefix="keylayouts-")
    try:
        tmp = os.path.join(work, "layouts.key")
        if os.path.isdir(deck):
            shutil.copytree(deck, tmp, symlinks=True)
        else:
            shutil.copyfile(deck, tmp)
        names = [lay.msg.name for lay in KeynoteFile(tmp).layouts()]
        keynote_app.run_applescript(_LAYOUT_SCRIPT, [tmp], app, timeout=1900)
        imgs = export_images(tmp, os.path.join(work, "img"), "jpeg", None, True, app)
        labels = {n: f"Layout {n}: {names[n - 1] if n <= len(names) else '?'}" for n, _ in imgs}
        return make_grid(imgs, out, labels=labels)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _label_font(size: int):
    from PIL import ImageFont

    for path in (
        "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/Helvetica.ttc",
    ):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def make_grid(images: list[tuple[int, str]], out: str, cols: int = 4, cell_w: int = 480, per_sheet: int = 20, labels: dict | None = None) -> list[str]:
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        sys.exit("--grid needs Pillow: pip install Pillow")
    base, ext = os.path.splitext(out)
    ext = ext or ".jpg"
    sheets = [images[i : i + per_sheet] for i in range(0, len(images), per_sheet)]
    written = []
    font = _label_font(20)
    for si, sheet in enumerate(sheets, 1):
        thumbs = []
        for num, path in sheet:
            im = Image.open(path).convert("RGB")
            h = round(im.height * cell_w / im.width)
            thumbs.append((num, im.resize((cell_w, h), Image.LANCZOS)))
        ch = max(t.height for _, t in thumbs)
        pad, label = 16, 30
        rows = (len(thumbs) + cols - 1) // cols
        ncols = min(cols, len(thumbs))
        W = pad + ncols * (cell_w + pad)
        H = pad + rows * (ch + label + pad)
        canvas = Image.new("RGB", (W, H), "white")
        draw = ImageDraw.Draw(canvas)
        for i, (num, t) in enumerate(thumbs):
            x = pad + (i % cols) * (cell_w + pad)
            y = pad + (i // cols) * (ch + label + pad)
            draw.text((x, y), (labels or {}).get(num, f"Slide {num}"), fill="black", font=font)
            canvas.paste(t, (x, y + label))
            draw.rectangle([x - 1, y + label - 1, x + cell_w, y + label + t.height], outline="#999999")
        name = f"{base}{ext}" if len(sheets) == 1 else f"{base}-{si}{ext}"
        canvas.save(name, quality=88)
        written.append(os.path.abspath(name))
    return written


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("deck")
    ap.add_argument("outdir", nargs="?", help="folder for slide images")
    ap.add_argument("--slides", help="keep only these slides, e.g. 1,3-5")
    ap.add_argument("--jpeg", action="store_true", help="JPEG instead of PNG")
    ap.add_argument("--no-skipped", action="store_true", help="leave out skipped slides")
    ap.add_argument("--pdf", metavar="FILE", help="export a PDF instead (skipped slides left out unless --with-skipped)")
    ap.add_argument("--notes", action="store_true", help="with --pdf: one slide per page with its presenter notes")
    ap.add_argument("--with-skipped", action="store_true", help="with --pdf: include skipped slides")
    ap.add_argument("--grid", metavar="FILE", help="write a labeled contact sheet (split into FILE-N past 20 slides)")
    ap.add_argument("--layouts", metavar="FILE", help="write a contact sheet of the deck's slide layouts (one sample slide each)")
    ap.add_argument("--app", help="15, 14 or a bundle id (default: oldest installed Keynote able to open the deck)")
    a = ap.parse_args()
    if not os.path.exists(a.deck):
        print(f"error: no such file: {a.deck}", file=sys.stderr)
        return 1
    if not (a.outdir or a.pdf or a.grid or a.layouts):
        ap.error("give an output folder, --pdf FILE, --grid FILE or --layouts FILE")
    try:
        app = keynote_app.choose(a.app, None if a.app else a.deck)
        if a.layouts:
            for p in layout_grid(a.deck, a.layouts, app):
                print(p)
        if a.pdf:
            print(export_pdf(a.deck, a.pdf, a.notes, a.with_skipped, app))
        if a.outdir or a.grid:
            outdir = a.outdir or tempfile.mkdtemp(prefix="keygrid-")
            imgs = export_images(a.deck, outdir, "jpeg" if (a.jpeg or not a.outdir) else "png", parse_ranges(a.slides), not a.no_skipped, app)
            if a.outdir:
                for _, p in imgs:
                    print(p)
            if a.grid:
                for p in make_grid(imgs, a.grid):
                    print(p)
            if not a.outdir:
                shutil.rmtree(outdir, ignore_errors=True)
    except keynote_app.KeynoteAppError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
