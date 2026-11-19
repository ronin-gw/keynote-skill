#!/usr/bin/env python3
"""Drive the Keynote app from the command line (macOS, AppleScript underneath).

    python keynote_app.py info                         # installed Keynotes, which one is used, open documents, open dialogs
    python keynote_app.py fonts hiragino               # installed PostScript font names matching a word
    python keynote_app.py run script.applescript [args...]   # run a script against the chosen Keynote
    python keynote_app.py run - [args...] < script     # same, script on stdin
    python keynote_app.py quit                         # quit Keynote if it has no unsaved documents

In a script, write `{{APP}}` wherever AppleScript wants the application:

    set {d, wasOpen} to my openDeck(item 1 of argv)
    tell {{APP}}
        ...
        if not wasOpen then close d saving no
    end tell

`run` wraps a script that has no `on run` handler in `on run argv ... end run`
with a 10-minute `with timeout`, so `argv` is always available (a script that
defines its own handlers must write `on run argv` itself). Every script can
call `my openDeck(posixPath)`, which returns {document, wasAlreadyOpen}: it
reuses the document if Keynote already has that file open, else opens it and
waits until it is ready. A deck that does not exist is refused before Keynote
sees it: `run` fails at once on a missing --for-file, or on a missing
.key / .pptx argument when the script neither saves `in` a path nor exports
(Keynote answers a missing path with a modal alert - "can't be opened ...
operation not permitted" - that stalls every job until someone dismisses it).

`info` also shows, per running Keynote, any dialog or sheet it has open and
its text, read through System Events. That needs Accessibility permission
for the terminal; without it `info` says the dialogs could not be checked.
When a Keynote call times out, the error message includes that dialog text
too, when it can be read.

Which Keynote: two different apps can be installed side by side -
Keynote 15+ ("Keynote Creator Studio", bundle id com.apple.Keynote) and
Keynote 14.x and earlier (com.apple.iWork.Keynote). They are addressed by
bundle id because their names collide. The default is the newest one
installed - except for brand-new decks (key_build.py, key_import.py), which
the OLDEST installed Keynote makes so that every installed Keynote can open
them; set KEYNOTE_APP=15, KEYNOTE_APP=14 or a bundle id to force one, or
pass --app. Saving a deck in a newer Keynote upgrades its file format, after
which an older Keynote can no longer open it - pass `for_file=` /
`--for-file deck.key` to pick the oldest installed Keynote able to open that
deck instead.
"""

from __future__ import annotations

import argparse
import os
import plistlib
import re
import subprocess
import sys
import zipfile

NEW_ID = "com.apple.Keynote"  # Keynote 15+ (Creator Studio)
OLD_ID = "com.apple.iWork.Keynote"  # Keynote 14.x and earlier
_CANDIDATES = [
    "/Applications/Keynote Creator Studio.app",
    "/Applications/Keynote.app",
    os.path.expanduser("~/Applications/Keynote Creator Studio.app"),
    os.path.expanduser("~/Applications/Keynote.app"),
]


class KeynoteAppError(Exception):
    pass


def _vtuple(v: str) -> tuple[int, ...]:
    out = []
    for p in str(v).split("."):
        try:
            out.append(int(p))
        except ValueError:
            break
    return tuple(out)


def installed() -> list[dict]:
    """Installed Keynote apps, newest first: [{'id', 'version', 'path'}]."""
    found = {}
    paths = list(_CANDIDATES)
    try:
        r = subprocess.run(
            ["mdfind", f"kMDItemCFBundleIdentifier == '{NEW_ID}' || kMDItemCFBundleIdentifier == '{OLD_ID}'"],
            capture_output=True, text=True, timeout=10,
        )
        paths += [p for p in r.stdout.splitlines() if p.endswith(".app")]
    except Exception:
        pass
    for p in paths:
        plist = os.path.join(p, "Contents", "Info.plist")
        if not os.path.exists(plist):
            continue
        try:
            with open(plist, "rb") as f:
                info = plistlib.load(f)
        except Exception:
            continue
        bid = info.get("CFBundleIdentifier")
        if bid in (NEW_ID, OLD_ID) and bid not in found:
            found[bid] = {"id": bid, "version": info.get("CFBundleShortVersionString", "?"), "path": p}
    return sorted(found.values(), key=lambda a: _vtuple(a["version"]), reverse=True)


_FONTS: tuple[set, set] | None = None


def installed_fonts() -> tuple[set, set]:
    """(PostScript names, family names) of the fonts this Mac has - empty sets if they cannot be listed."""
    global _FONTS
    if _FONTS is None:
        js = ('ObjC.import("AppKit"); var m = $.NSFontManager.sharedFontManager; '
              'ObjC.deepUnwrap(m.availableFonts).join("\\n") + "\\n--\\n" + ObjC.deepUnwrap(m.availableFontFamilies).join("\\n")')
        try:
            r = subprocess.run(["osascript", "-l", "JavaScript", "-e", js], capture_output=True, text=True, timeout=60)
            ps, _, fam = r.stdout.partition("\n--\n")
            _FONTS = ({x.strip() for x in ps.splitlines() if x.strip()}, {x.strip() for x in fam.splitlines() if x.strip()})
        except Exception:
            _FONTS = (set(), set())
    return _FONTS


_RESOLVED: dict[str, bool] = {}


def _font_resolves(name: str) -> bool:
    """Whether macOS can instantiate a font by this exact PostScript name."""
    if name not in _RESOLVED:
        js = ('ObjC.import("AppKit"); function run(argv) { var f = $.NSFont.fontWithNameSize(argv[0], 12); '
              'return (!f.isNil() && ObjC.unwrap(f.fontName) == argv[0]) ? "yes" : "no"; }')
        try:
            r = subprocess.run(["osascript", "-l", "JavaScript", "-e", js, name], capture_output=True, text=True, timeout=30)
            _RESOLVED[name] = r.stdout.strip() == "yes"
        except Exception:
            _RESOLVED[name] = True  # cannot tell: do not cry wolf
    return _RESOLVED[name]


def font_problem(name: str) -> str | None:
    """None if `name` is an installed PostScript font name, else a hint (Keynote would silently draw another font)."""
    ps, fam = installed_fonts()
    if not ps or name in ps:
        return None
    if _font_resolves(name):  # system faces such as HiraKakuProN-W3 are usable but not in the font manager's list
        return None
    key = name.replace(" ", "").replace("-", "").lower()
    near = sorted(p for p in ps if p.replace("-", "").lower().startswith(key[:4]))[:6]
    if name in fam:
        faces = sorted(p for p in ps if p.replace("-", "").lower().startswith(name.replace(" ", "").lower()[:6]))[:6]
        return f"{name!r} is a family name, not a PostScript font name" + (f" - use one of: {', '.join(faces)}" if faces else "")
    return f"font {name!r} is not installed; Keynote will silently substitute another" + (f" (similar: {', '.join(near)})" if near else "")


def file_format_version(deck: str) -> str | None:
    """fileFormatVersion from the deck's Metadata/Properties.plist."""
    try:
        if os.path.isdir(deck):
            with open(os.path.join(deck, "Metadata", "Properties.plist"), "rb") as f:
                return str(plistlib.load(f).get("fileFormatVersion"))
        with zipfile.ZipFile(deck) as z:
            return str(plistlib.loads(z.read("Metadata/Properties.plist")).get("fileFormatVersion"))
    except Exception:
        return None


def _can_open(app_version: str, fmt: str | None) -> bool:
    """Keynote 15.x writes format 26.x; before that the format version tracked the app version."""
    if not fmt:
        return True
    f, a = _vtuple(fmt), _vtuple(app_version)
    if a >= (15,):
        return True
    return f < (15,) and f[:2] <= a[:2]


def choose(app: str | None = None, for_file: str | None = None, new_deck: bool = False) -> dict:
    """Pick the Keynote to drive. See the module docstring for the rules."""
    apps = installed()
    if not apps:
        raise KeynoteAppError("Keynote is not installed (looked for com.apple.Keynote and com.apple.iWork.Keynote).")
    want = app or os.environ.get("KEYNOTE_APP")
    if want:
        for a in apps:
            if want == a["id"] or a["version"].split(".")[0] == str(want):
                return a
        for a in apps:  # any pre-15 number means "the older Keynote"
            if str(want).isdigit() and int(want) < 15 and a["id"] == OLD_ID:
                return a
        raise KeynoteAppError(f"KEYNOTE_APP={want}: no such Keynote installed. Installed: " + ", ".join(f"{a['version']} ({a['id']})" for a in apps))
    if for_file and os.path.exists(for_file):
        fmt = file_format_version(for_file)
        able = [a for a in apps if _can_open(a["version"], fmt)]
        if able:
            return able[-1]  # the oldest that can open it: keeps the format the user's own Keynote reads
    if new_deck:
        return apps[-1]  # the oldest installed: every installed Keynote can open what it makes
    return apps[0]


def app_ref(app: dict) -> str:
    return f'application id "{app["id"]}"'


def ensure_running(app: dict, wait: int = 90) -> None:
    """Launch Keynote in the background and wait until it answers Apple events.

    `tell application id ...` alone is not enough: an app that macOS has
    auto-terminated (Keynote with no documents open) fails with -600
    "application isn't running" instead of launching.
    """
    import time

    probe = ["osascript", "-e", f'with timeout of 5 seconds\ntell {app_ref(app)} to return (count of documents)\nend timeout']
    if running(app):
        r = subprocess.run(probe, capture_output=True, text=True)
        if r.returncode == 0:
            return
    subprocess.run(["open", "-g", "-b", app["id"]], capture_output=True)
    deadline = time.time() + wait
    while time.time() < deadline:
        time.sleep(1)
        r = subprocess.run(probe, capture_output=True, text=True)
        if r.returncode == 0:
            return
        if "-1743" in r.stderr:
            raise KeynoteAppError(
                "macOS blocked control of Keynote. Allow it in System Settings > Privacy & Security > Automation."
            )
    raise KeynoteAppError(
        f"Keynote {app['version']} did not become ready within {wait}s. A first-launch or sign-in "
        "dialog is probably waiting in the app - dismiss it and retry." + dialog_hint(app)
    )


def run_applescript(script: str, args: list[str] | tuple = (), app: dict | None = None, timeout: int = 900) -> str:
    """Run AppleScript text (with {{APP}} placeholders); returns stdout, raises on failure."""
    app = app or choose()
    ensure_running(app, wait=min(90, max(10, timeout)))
    src = script
    has_run = any(line.strip().startswith("on run") for line in src.splitlines())
    if not has_run:
        # the script's own handlers (on name(...) ... end name) must stay outside the `on run` wrapper
        body, handlers, inside = [], [], None
        for line in src.splitlines():
            m = re.match(r"\s*on\s+([A-Za-z_]\w*)\s*\(", line) if inside is None else None
            if m:
                inside = m.group(1)
            (handlers if inside else body).append(line)
            if inside and re.match(rf"\s*end\s+{re.escape(inside)}\b", line):
                inside = None
        src = "on run argv\nwith timeout of 600 seconds\n" + "\n".join(body) + "\nend timeout\nend run\n" + "\n".join(handlers) + "\n"
    if "on openDeck(" not in src:
        src += HANDLERS
    # `tell application id "{{APP}}"` and `tell application "{{APP}}"` are common slips for `tell {{APP}}`.
    src = re.sub(r'application\s+(?:id\s+)?"\{\{APP\}\}"', "{{APP}}", src)
    src = src.replace("{{APP}}", app_ref(app))
    try:
        r = subprocess.run(["osascript", "-", *[str(a) for a in args]], input=src, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise KeynoteAppError(
            f"Keynote did not answer within {timeout}s. A dialog is probably open in Keynote "
            "(first-launch screen, a font/version warning, a \"can't be opened\" alert, or a macOS Automation "
            "permission prompt), or another job keeps it busy." + dialog_hint(app)
        )
    if r.returncode != 0:
        err = r.stderr.strip()
        hint = ""
        if "-1743" in err or "Not authorized" in err:
            hint = "\nGrant access in System Settings > Privacy & Security > Automation (your terminal -> Keynote)."
        elif "-1712" in err:
            hint = "\nKeynote timed out: a dialog is probably waiting in the app, or another job keeps it busy." + dialog_hint(app)
        elif "-1728" in err:
            hint = ("\n(-1728 = no such object: a wrong slide / item / layout / theme name or index (names are localized - list them first), "
                    "or a document that had not finished opening - if the names are right, just run it again.)")
        # osascript reports character offsets into the script: show the line they point at
        m = re.match(r"(\d+):(\d+):", err)
        if m:
            start = int(m.group(1))
            line_no = src.count("\n", 0, start) + 1
            lines = src.splitlines()
            if 0 < line_no <= len(lines):
                hint += f"\n  script line {line_no}: {lines[line_no - 1].strip()[:200]}"
                for prev in range(line_no - 1, 0, -1):  # key_build marks what it was making
                    if lines[prev - 1].startswith("set cur to "):
                        hint += f"\n  while building {lines[prev - 1][len('set cur to '):].strip(chr(34))}"
                        break
        raise KeynoteAppError(err + hint)
    return r.stdout.rstrip("\n")


# AppleScript handlers appended to every script run through run_applescript().
HANDLERS = r'''
on openDeck(p)
    -- returns {document, wasAlreadyOpen}; p is a POSIX path
    set f to POSIX file p
    try
        f as alias -- a missing path would make Keynote put up a modal "can't be opened" alert
    on error
        error "no such file: " & p number -43
    end try
    tell {{APP}}
        repeat with d in documents
            try
                set dp to POSIX path of (file of d as alias)
                if dp is p or dp is (p & "/") then return {contents of d, true}
            end try
        end repeat
        set d to open f
        repeat 100 times -- `open` can return before the document exists
            if d is not missing value then exit repeat
            delay 0.2
            try
                set d to (first document whose file is f)
            end try
        end repeat
        if d is missing value then error "Keynote could not open " & p
        return {d, false}
    end tell
end openDeck
'''


def running(app: dict) -> bool:
    # `is running` is answered locally - no System Events, which may be off-limits (-1743)
    try:
        r = subprocess.run(["osascript", "-e", f'application id "{app["id"]}" is running'],
                           capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        return False
    return r.stdout.strip() == "true"


_DIALOG_SCRIPT = r'''
on run argv
    set acc to ""
    tell application "System Events"
        repeat with p in (every process whose bundle identifier is (item 1 of argv))
            repeat with w in (every window of p)
                set boxes to {}
                set sr to ""
                try
                    set sr to subrole of w
                end try
                if sr is "AXDialog" or sr is "AXSystemDialog" then set end of boxes to w
                try
                    set boxes to boxes & (every sheet of w)
                end try
                repeat with bx in boxes
                    set t to ""
                    try
                        repeat with sx in (every static text of bx)
                            try
                                set v to (value of sx) as text
                                if v is not "" and v is not "missing value" then set t to t & v & " | "
                            end try
                        end repeat
                    end try
                    set bs to ""
                    try
                        repeat with bn in (every button of bx)
                            try
                                set bs to bs & "[" & ((name of bn) as text) & "] "
                            end try
                        end repeat
                    end try
                    set wn to ""
                    try
                        set wn to (name of w) as text
                    end try
                    set acc to acc & wn & tab & t & tab & bs & linefeed
                end repeat
            end repeat
        end repeat
    end tell
    return acc
end run
'''


def dialogs(app: dict, timeout: int = 8) -> tuple[list[str], str | None]:
    """Dialogs / sheets Keynote is showing, via System Events: ([description, ...], None),
    or ([], why it could not be checked). Needs Accessibility permission; never raises."""
    try:
        r = subprocess.run(["osascript", "-", app["id"]], input=_DIALOG_SCRIPT, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return [], f"System Events did not answer within {timeout}s"
    except Exception as e:
        return [], str(e)
    if r.returncode != 0:
        err = r.stderr.strip()
        if any(k in err for k in ("-1743", "-1719", "-25211", "assistive", "補助アクセス", "Not authorized", "権限がありません")):
            return [], "no permission (System Settings > Privacy & Security > Accessibility / Automation)"
        return [], err.splitlines()[-1][:200] if err else f"osascript exit {r.returncode}"
    out = []
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3:
            title, text, buttons = parts[0].strip(), parts[1].strip(" |"), parts[2].strip()
            out.append(" ".join(x for x in (f"window {title!r}:" if title else "", repr(text) if text else "(no text)", buttons) if x))
    return out, None


def dialog_hint(app: dict) -> str:
    """One line for an error message: the dialog Keynote shows, if it can be read."""
    if not running(app):
        return ""
    found, why = dialogs(app, timeout=5)
    if found:
        return f"\nKeynote {app['version']} is showing: " + " // ".join(found) + " - someone must dismiss it (if you did not cause it, it may belong to another job)."
    if why is None:
        return f"\n(No dialog or sheet is open in Keynote {app['version']}: it may just be busy with another job - `keynote_app.py info` lists its open documents.)"
    return f"\n(Could not check Keynote for dialogs: {why}.)"


_DECK_EXTS = (".key", ".pptx", ".ppt")


def missing_deck_args(src: str, args) -> list[str]:
    """Arguments that name a deck that does not exist - unless the script also saves or exports
    (then such an argument may be an output path). Opening a missing path makes Keynote put up a
    modal "can't be opened" dialog that stalls every job until someone dismisses it."""
    if re.search(r"\bexport\b|\bsave\b[^\n]*\bin\b", src):
        return []
    return [a for a in args if str(a).lower().rstrip("/").endswith(_DECK_EXTS) and not os.path.exists(str(a))]


def open_documents(app: dict, timeout: int = 60) -> list[dict]:
    if not running(app):
        return []
    out = run_applescript(
        '''
set acc to ""
tell {{APP}}
    repeat with d in documents
        set p to ""
        try
            set p to POSIX path of (file of d as alias)
        end try
        set acc to acc & (name of d) & tab & p & tab & (modified of d) & linefeed
    end repeat
end tell
return acc''',
        app=app, timeout=timeout,
    )
    docs = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            docs.append({"name": parts[0], "path": parts[1].rstrip("/"), "modified": parts[2] == "true"})
    return docs


def close_documents_in(folder: str, app: dict, timeout: int = 20) -> list[str] | None:
    """Close, without saving, every document open from inside `folder` (a temp dir this
    process made). Best effort for failure paths: never raises; returns the names closed,
    or None if Keynote did not answer."""
    root = os.path.realpath(folder) + os.sep
    closed = []
    try:
        for d in open_documents(app, timeout=timeout):
            if d["path"] and (os.path.realpath(d["path"]) + os.sep).startswith(root):
                run_applescript('tell {{APP}} to close (first document whose name is (item 1 of argv)) saving no',
                                [d["name"]], app, timeout=timeout)
                closed.append(d["name"])
    except Exception:
        return None
    return closed


def is_open(deck: str, app: dict | None = None) -> bool:
    """True if `deck` is open in any installed Keynote."""
    target = os.path.realpath(deck)
    for a in ([app] if app else installed()):
        try:
            for d in open_documents(a):
                if d["path"] and os.path.realpath(d["path"]) == target:
                    return True
        except KeynoteAppError:
            pass
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--app", help="a major version (15, 14, 13 ...) or a bundle id (default: $KEYNOTE_APP, else newest installed)")
    ap.add_argument("--for-file", help="pick the oldest installed Keynote that can open this deck")
    sub = ap.add_subparsers(dest="cmd", required=True)
    # --app / --for-file are accepted on either side of the subcommand
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--app", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    common.add_argument("--for-file", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    sub.add_parser("info", parents=[common])
    r = sub.add_parser("run", parents=[common])
    r.add_argument("script", help="AppleScript file, or - for stdin")
    r.add_argument("args", nargs="*")
    r.add_argument("--keep-open", action="store_true", help="do not close documents the script opened and left open")
    sub.add_parser("quit", parents=[common])
    f = sub.add_parser("fonts", parents=[common], help="list installed PostScript font names (optionally filtered)")
    f.add_argument("pattern", nargs="?", default="")
    a = ap.parse_args()
    try:
        if a.for_file and not os.path.exists(a.for_file):
            raise KeynoteAppError(f"--for-file {a.for_file}: no such file")
        if a.cmd == "info":
            apps = installed()
            if not apps:
                print("Keynote is not installed.")
                return 1
            chosen = choose(a.app, a.for_file)
            for x in apps:
                mark = "*" if x["id"] == chosen["id"] else " "
                up = running(x)
                print(f"{mark} Keynote {x['version']}  {x['id']}  {x['path']}  ({'running' if up else 'not running'})")
                if not up:
                    print("      no open documents")
                    continue
                found, why = dialogs(x)
                for t in found:
                    print(f"      DIALOG: {t}  - Keynote waits until someone dismisses it; every job driving it stalls meanwhile")
                if why:
                    print(f"      (dialogs not checked: {why})")
                elif not found:
                    print("      no dialog or sheet open")
                try:
                    docs = open_documents(x, timeout=15)
                except KeynoteAppError as e:
                    print(f"      open documents could not be listed: {str(e).splitlines()[0]}")
                    continue
                for d in docs:
                    print(f"      open: {d['path'] or d['name']}" + ("  [unsaved changes]" if d["modified"] else ""))
                if not docs:
                    print("      no open documents")
            if a.for_file:
                print(f"(* = the Keynote used for {os.path.basename(a.for_file)}, file format {file_format_version(a.for_file)})")
            else:
                new = choose(a.app, new_deck=True)
                print(f"New decks (key_build.py, key_import.py) are made by Keynote {new['version']}"
                      + (" - the oldest installed, so both Keynotes can open them; --app 15 to use the newest." if len(apps) > 1 and not a.app else "."))
                print("For an existing deck the oldest Keynote able to open it is used: info --for-file deck.key  (* = what a bare `run` drives)")
            return 0
        if a.cmd == "fonts":
            ps, _fam = installed_fonts()
            key = a.pattern.replace(" ", "").lower()
            for n in sorted(ps):
                if key in n.replace("-", "").lower() or key in n.lower():
                    print(n)
            return 0
        app = choose(a.app, a.for_file)
        if a.cmd == "run":
            src = sys.stdin.read() if a.script == "-" else open(a.script, encoding="utf-8").read()
            missing = missing_deck_args(src, a.args)
            if missing:
                raise KeynoteAppError("no such file: " + ", ".join(missing) + " - not handed to Keynote (opening a missing "
                                      "path puts up a modal alert in Keynote that stalls every job until someone dismisses it)")
            before = {(d["name"], d["path"]) for d in open_documents(app)}
            blob = src + "\n" + "\n".join(a.args)

            def mine(d):
                # Keynote may be shared with other jobs: only a document this script names (in its
                # text or arguments) is this run's to close - never one that merely appeared meanwhile
                return (d["name"], d["path"]) not in before and any(
                    k and k in blob for k in (d["path"], os.path.basename(d["path"] or ""), d["name"]))
            try:
                out = run_applescript(src, a.args, app)
            except KeynoteAppError:
                # a script that died half-way leaves its document open with unsaved changes; the next
                # run would then pick that stale document up. Close what this run opened, unsaved.
                left = [d for d in open_documents(app) if mine(d)]
                for d in left:
                    try:
                        run_applescript('tell {{APP}} to close (first document whose name is (item 1 of argv)) saving no', [d["name"]], app, timeout=60)
                        print(f"(closed {d['name']} without saving - the script failed before closing it; the file on disk is as it was before this run"
                              + (" unless the script had already saved" if d["path"] else "") + ")", file=sys.stderr)
                    except KeynoteAppError:
                        pass
                raise
            if out:
                print(out)
            if not a.keep_open:
                # a script that saves but forgets `close` leaves the deck open: key_edit.py then
                # refuses it and a later script may pick up the stale window
                for d in [d for d in open_documents(app) if mine(d)]:
                    if d["modified"]:
                        print(f"warning: {d['name']} is still open in Keynote with UNSAVED changes - the script did not save/close it. "
                              "Nothing was discarded; end scripts with `save d` and `close d saving no`.", file=sys.stderr)
                        continue
                    try:
                        run_applescript('tell {{APP}} to close (first document whose name is (item 1 of argv)) saving no', [d["name"]], app, timeout=60)
                        print(f"(closed {d['name']}, which the script left open - nothing was lost: it had no unsaved changes, so what the script saved is on disk. --keep-open to leave documents open)", file=sys.stderr)
                    except KeynoteAppError:
                        pass
            return 0
        if a.cmd == "quit":
            if not running(app):
                return 0
            dirty = [d for d in open_documents(app) if d["modified"]]
            if dirty:
                print("Not quitting: unsaved documents open: " + ", ".join(d["name"] for d in dirty), file=sys.stderr)
                return 1
            run_applescript("tell {{APP}} to quit saving no", app=app, timeout=60)
            return 0
    except KeynoteAppError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
