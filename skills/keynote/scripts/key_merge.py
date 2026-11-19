#!/usr/bin/env python3
"""Bring slides from other decks into a deck - whole slides, still editable, looking as they did.

    python key_merge.py base.key -o out.key --take other.key:3,7 --after 2
    python key_merge.py base.key -o out.key --take a.key:3,7 --take b.key:1-4       # appended in order
    python key_merge.py base.key -o out.key --take a.key:all --after 0             # before slide 1

`--take DECK:SLIDES` names slides of another deck (1-based: 3,7  2-5  all); they arrive in the
order written. `--after N` places the first --take after slide N of base.key (default: at the
end); each further --take follows the previous one. Nothing is rasterised: text, shapes, tables,
charts and pictures stay editable and keep their positions, and each slide brings its own layout.

Neither base.key nor the source decks are modified: Keynote works on copies (moving a slide out
of a document is the only cross-document transfer Keynote scripting offers - `duplicate ... to`
another document silently does nothing).

Afterwards: reorder / delete / retitle with the usual tools, and render the result next to the
sources. Slides keep their own slide size's coordinates - merge decks of the same slide size.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import keynote_app  # noqa: E402
from key_render import parse_ranges  # noqa: E402
from keynote_lib import KeynoteFile  # noqa: E402

_SCRIPT = r'''
on run argv
    with timeout of 900 seconds
        set basePath to item 1 of argv
        set afterN to (item 2 of argv) as integer
        set {d, wasOpen} to my openDeck(basePath)
        set moved to 0
        tell {{APP}}
            set k to 3
            repeat while k < (count of argv)
                set srcPath to item k of argv
                set nums to my splitText(item (k + 1) of argv, ",")
                set {sd, srcWasOpen} to my openDeck(srcPath)
                try
                    -- a document cannot give away its last slide: leave a spare one behind
                    if (count of nums) ≥ (count of slides of sd) then make new slide at end of slides of sd
                    repeat with n in nums -- indexes already allow for the slides that left before
                        if afterN is 0 then
                            move slide (n as integer) of sd to before slide 1 of d
                        else
                            move slide (n as integer) of sd to after slide afterN of d
                        end if
                        set afterN to afterN + 1
                        set moved to moved + 1
                    end repeat
                on error msg number num
                    close sd saving no
                    close d saving no
                    error msg number num
                end try
                close sd saving no
                set k to k + 2
            end repeat
            save d
            set total to count of slides of d
            close d saving no
        end tell
        return (moved as text) & "|" & (total as text)
    end timeout
end run

on splitText(t, delim)
    set AppleScript's text item delimiters to delim
    set parts to text items of t
    set AppleScript's text item delimiters to ""
    return parts
end splitText
'''


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("base")
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--take", action="append", required=True, metavar="DECK:SLIDES", help="e.g. other.key:3,7 or other.key:all (repeatable)")
    ap.add_argument("--after", type=int, help="insert after this slide of the base deck (0 = before the first; default: at the end)")
    ap.add_argument("--app", help="15, 14 or a bundle id")
    a = ap.parse_args()

    out = os.path.abspath(a.output)
    if os.path.exists(out):
        print(f"error: {out} exists", file=sys.stderr)
        return 1
    takes = []
    for t in a.take:
        path, _, spec = t.rpartition(":")
        if not path or not os.path.exists(path):
            print(f"error: --take wants DECK:SLIDES, got {t!r}", file=sys.stderr)
            return 1
        n = len(KeynoteFile(path).slides())
        nums = list(range(1, n + 1)) if spec.strip() == "all" else []
        if not nums:
            for part in spec.split(","):  # keep the order as written
                nums.extend(sorted(parse_ranges(part) or []))
        bad = [x for x in nums if not 1 <= x <= n] or [x for x in set(nums) if nums.count(x) > 1]
        if bad or not nums:
            print(f"error: {os.path.basename(path)} has {n} slides; cannot take {spec}", file=sys.stderr)
            return 1
        takes.append((os.path.abspath(path), nums))
    base = KeynoteFile(a.base)
    n_base = len(base.slides())
    after = n_base if a.after is None else a.after
    if not 0 <= after <= n_base:
        print(f"error: --after {after}: {os.path.basename(a.base)} has {n_base} slides", file=sys.stderr)
        return 1
    sizes = {os.path.basename(a.base): base.size}
    for path, _ in takes:
        sizes[os.path.basename(path)] = KeynoteFile(path).size

    try:
        # the oldest Keynote that can open every deck involved keeps the base deck's format
        apps = [keynote_app.choose(a.app, p) for p in [a.base] + [p for p, _ in takes]]
        app = max(apps, key=lambda x: keynote_app._vtuple(x["version"]))
        fmt_before = keynote_app.file_format_version(a.base)
        work = tempfile.mkdtemp(prefix="keymerge-", dir=os.path.dirname(out) or ".")
        try:
            shutil.copy2(a.base, out)
            argv = [out, str(after)]
            for i, (path, nums) in enumerate(takes):
                cp = os.path.join(work, f"take{i}-{os.path.basename(path)}")
                shutil.copy2(path, cp)  # slides are MOVED out of this copy; the user's deck stays whole
                gone: list[int] = []  # slide numbers shift down as earlier ones leave the copy
                live = []
                for x in nums:
                    live.append(x - sum(1 for g in gone if g < x))
                    gone.append(x)
                argv += [os.path.realpath(cp), ",".join(str(x) for x in live)]
            res = keynote_app.run_applescript(_SCRIPT, argv, app, timeout=1900)
        except Exception:
            if os.path.exists(out):
                os.remove(out)
            raise
        finally:
            shutil.rmtree(work, ignore_errors=True)
    except keynote_app.KeynoteAppError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    moved, _, total = res.partition("|")
    print(out)
    print(f"{moved} slides brought in; {total} slides now" + (f" (inserted after slide {after})" if after != n_base else " (appended)"))
    fmt_after = keynote_app.file_format_version(out)
    if fmt_before != fmt_after:
        print(f"CHECK: file format went {fmt_before} -> {fmt_after} (a source deck needed Keynote {app['version']}); older Keynotes cannot open the result")
    if len({(round(s[0]), round(s[1])) for s in sizes.values()}) > 1:
        print("CHECK: slide sizes differ - " + ", ".join(f"{k} {round(v[0])}x{round(v[1])}" for k, v in sizes.items())
              + ". Brought-in objects keep their coordinates, so they may sit off-centre or past the edge: render and fix.")
    print("Next: render the new slides beside their sources (key_render.py), then key_check.py.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
