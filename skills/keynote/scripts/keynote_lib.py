"""Read and edit Keynote (.key) documents directly, without Keynote running.

A .key document is a ZIP (or a folder) holding `Index/*.iwa` files. Each .iwa
is a Snappy-compressed stream of Protobuf "archives"; every archive has a
document-unique integer identifier and objects point at each other by that
identifier (`TSP.Reference`).  This module loads all of them, lets you walk
slides -> drawables -> text, change things, and write a new .key.

Decoding uses the `keynote-parser` package (pip install keynote-parser).
Objects are edited as live Protobuf messages, so fields this schema does not
know about (newer Keynote versions add some) survive a save untouched, and
.iwa files you did not change are written back byte-for-byte.

    from keynote_lib import KeynoteFile
    doc = KeynoteFile("deck.key")
    for slide in doc.slides():
        print(slide.index, slide.title)
    doc.replace_text("2025", "2026")
    doc.save("out.key")

Positions and sizes are in points from the slide's top-left corner; character
indexes are UTF-16 code units. Editing gives the touched object its own style
"variation" (what Keynote itself creates for a local override), registered in
the stylesheet and the package metadata, so shared theme styles stay as they are.

Do not save over a deck that is open in Keynote.
"""

from __future__ import annotations

import copy
import hashlib
import os
import shutil
import re
import struct
import sys
import warnings
import zipfile
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterator

try:
    from keynote_parser import codec as _codec
    from keynote_parser.codec import IWAFile
except ImportError:  # pragma: no cover
    sys.exit(
        "keynote_lib needs the keynote-parser package: pip install keynote-parser"
    )

warnings.simplefilter("ignore", _codec.UnknownArchiveWarning)

PARA_BREAKS = "\n\r "
ATTACHMENT_CHAR = "￼"
# Attribute tables that hold one entry per paragraph start (when present).
_PER_PARAGRAPH_TABLES = ("table_para_style",)
# Attribute tables whose entries may only sit at paragraph starts.
_PARAGRAPH_ALIGNED_TABLES = ("table_para_style", "table_para_data", "table_list_style")


class KeynoteError(Exception):
    pass


def type_name(msg) -> str:
    d = getattr(msg, "DESCRIPTOR", None)
    return d.full_name if d is not None else getattr(msg, "PBTYPE", type(msg).__name__)


def u16len(s: str) -> int:
    """Length in UTF-16 code units - the unit Keynote's character indexes use."""
    return len(s.encode("utf-16-le")) // 2


def _u16(s: str) -> bytes:
    return s.encode("utf-16-le", "surrogatepass")


def _from_u16(b: bytes) -> str:
    return b.decode("utf-16-le", "surrogatepass")


# --------------------------------------------------------------------------
# Package (zip file or folder) access
# --------------------------------------------------------------------------


def _zip_name(info: zipfile.ZipInfo) -> str:
    """The entry's real name. Keynote writes UTF-8 names without setting the zip
    UTF-8 flag, so Python hands them back mis-decoded as CP437."""
    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return info.filename


def _zip_read(z: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    """z.read(info), tolerating a UTF-8 flag that differs between the entry's local
    header and its central-directory record. Keynote can write such entries (seen
    after re-saving a deck whose entries carried the flag); zipfile then decodes
    the two copies of the name differently and rejects the mismatch."""
    try:
        return z.read(info)
    except zipfile.BadZipFile as e:
        if "differ" not in str(e):
            raise
        err = e
    try:
        utf8_as_cp437 = info.filename.encode("utf-8").decode("cp437")
    except UnicodeDecodeError:
        utf8_as_cp437 = None
    for alt in (_zip_name(info), utf8_as_cp437):
        if alt and alt != info.orig_filename:
            zi = copy.copy(info)
            zi.orig_filename = alt
            try:
                return z.read(zi)
            except zipfile.BadZipFile:
                pass
    raise err


def _zip_info(info: zipfile.ZipInfo) -> zipfile.ZipInfo:
    """A ZipInfo for re-writing `info` under its real (UTF-8) name, stored uncompressed."""
    zi = zipfile.ZipInfo(_zip_name(info), date_time=info.date_time)
    zi.external_attr = info.external_attr
    zi.compress_type = zipfile.ZIP_STORED
    return zi


class _Package:
    """A .key on disk: a single zip file, or a folder (optionally with Index.zip)."""

    def __init__(self, path: str):
        self.path = path
        if not os.path.exists(path):
            raise KeynoteError(f"No such file: {path}")
        self.is_dir = os.path.isdir(path)
        self.index_zip = None
        if self.is_dir:
            iz = os.path.join(path, "Index.zip")
            if os.path.exists(iz):
                self.index_zip = iz
        elif not zipfile.is_zipfile(path):
            raise KeynoteError(
                f"{path} is not a Keynote document (not a zip or a package folder). "
                "Keynote '09 and earlier used a different format; open it in Keynote and re-save."
            )

    def names(self) -> list[str]:
        if not self.is_dir:
            with zipfile.ZipFile(self.path) as z:
                return [_zip_name(i) for i in z.infolist() if not i.filename.endswith("/")]
        out = []
        for root, _dirs, files in os.walk(self.path):
            for f in files:
                rel = os.path.relpath(os.path.join(root, f), self.path)
                if rel != "Index.zip":
                    out.append(rel)
        if self.index_zip:
            with zipfile.ZipFile(self.index_zip) as z:
                out += [_zip_name(i) for i in z.infolist() if not i.filename.endswith("/")]
        return out

    def _in_index_zip(self, name: str) -> bool:
        return bool(self.index_zip) and name.startswith("Index/")

    def read(self, name: str) -> bytes:
        if not self.is_dir or self._in_index_zip(name):
            with zipfile.ZipFile(self.index_zip if self.is_dir else self.path) as z:
                for info in z.infolist():
                    if _zip_name(info) == name:
                        return _zip_read(z, info)
            raise KeyError(name)
        with open(os.path.join(self.path, name), "rb") as f:
            return f.read()

    def write_copy(self, out_path: str, replace: dict[str, bytes], add: dict[str, bytes]):
        """Write the package to out_path with some entries replaced/added.

        Built next to the destination and moved into place, so out_path may be
        the source itself and a failure never leaves a half-written deck.
        """
        out_path = os.path.abspath(out_path)
        tmp = out_path + ".tmp-keynote-lib"
        if os.path.lexists(tmp):
            shutil.rmtree(tmp) if os.path.isdir(tmp) else os.remove(tmp)
        if not self.is_dir:
            with zipfile.ZipFile(self.path) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as zout:
                for info in zin.infolist():
                    name = _zip_name(info)
                    data = replace[name] if name in replace else _zip_read(zin, info)
                    zout.writestr(_zip_info(info), data)
                for name, data in add.items():
                    zout.writestr(zipfile.ZipInfo(name), data, zipfile.ZIP_STORED)
        else:
            shutil.copytree(self.path, tmp, symlinks=True)
            zip_replace = {n: d for n, d in replace.items() if self._in_index_zip(n)}
            zip_add = {n: d for n, d in add.items() if self._in_index_zip(n)}
            for name, data in {**replace, **add}.items():
                if name in zip_replace or name in zip_add:
                    continue
                target = os.path.join(tmp, name)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with open(target, "wb") as f:
                    f.write(data)
            if zip_replace or zip_add:
                dst = os.path.join(tmp, "Index.zip")
                with zipfile.ZipFile(self.index_zip) as zin, zipfile.ZipFile(dst + ".new", "w", zipfile.ZIP_STORED) as zout:
                    for info in zin.infolist():
                        data = zip_replace.get(_zip_name(info))
                        zout.writestr(_zip_info(info), _zip_read(zin, info) if data is None else data)
                    for name, data in zip_add.items():
                        zout.writestr(zipfile.ZipInfo(name), data, zipfile.ZIP_STORED)
                os.replace(dst + ".new", dst)
        if os.path.lexists(out_path):
            if os.path.isdir(out_path) and not os.path.islink(out_path):
                shutil.rmtree(out_path)
            else:
                os.remove(out_path)
        os.replace(tmp, out_path)


# --------------------------------------------------------------------------
# Object model
# --------------------------------------------------------------------------


@dataclass
class Obj:
    """One archive: `id`, its decoded Protobuf message `msg`, and where it lives."""

    id: int
    msg: Any
    archive: Any  # keynote_parser IWAArchiveSegment (header + objects)
    file: str  # e.g. "Index/Slide-123.iwa"

    @property
    def type(self) -> str:
        return type_name(self.msg)

    def __repr__(self):
        return f"<Obj {self.id} {self.type} in {self.file}>"


@dataclass
class Drawable:
    """Something placed on a slide: text box, shape, image, table, chart, group..."""

    doc: "KeynoteFile"
    obj: Obj
    kind: str  # title | body | slide_number | object_placeholder | text | shape | image | movie | table | chart | group | line | other
    parent: "Drawable | None" = None
    children: list["Drawable"] = field(default_factory=list)

    @property
    def id(self) -> int:
        return self.obj.id

    @property
    def base(self):
        """The TSD.DrawableArchive at the bottom of the `super` chain."""
        m = self.obj.msg
        while type_name(m) != "TSD.DrawableArchive":
            if not m.HasField("super"):
                return None
            m = m.super
        return m

    @property
    def geometry(self):
        b = self.base
        return b.geometry if b is not None and b.HasField("geometry") else None

    @property
    def frame(self) -> tuple[float, float, float, float] | None:
        """(x, y, width, height) in slide points; x/y are relative to the parent group, if any.

        A table's stored size goes stale (Keynote keeps the size it was created
        with): its width / height here are the sums of its column widths / row
        heights, which is what Keynote draws and reports."""
        g = self.geometry
        if g is None:
            return None
        w, h = g.size.width, g.size.height
        if self.kind == "table":
            try:
                cols, rows = self.doc.table_grid(self)
                w, h = sum(cols), sum(rows) + (self.doc.table_caption_height(self) or 0)
            except Exception:  # keep the stored size rather than fail a dump
                pass
        return (g.position.x, g.position.y, w, h)

    @property
    def storage(self) -> Obj | None:
        """The text storage (TSWP.StorageArchive) of a text box / shape / placeholder."""
        m = self.obj.msg
        while m is not None and type_name(m) != "TSWP.ShapeInfoArchive":
            m = m.super if _has(m, "super") else None
        if m is None:
            return None
        for f in ("owned_storage", "deprecated_storage"):
            if m.HasField(f):
                return self.doc.get(getattr(m, f).identifier)
        return None

    @property
    def text(self) -> str:
        st = self.storage
        return storage_text(st.msg) if st else ""

    @property
    def description(self) -> str:
        b = self.base
        return b.accessibility_description if b is not None else ""

    @property
    def data_name(self) -> str | None:
        """File name (inside the package's Data/ folder) of an image or movie."""
        m = self.obj.msg
        for f in ("data", "movieData", "originalData"):
            if _has(m, f):
                return self.doc.data_filename(getattr(m, f).identifier)
        return None

    def __repr__(self):
        return f"<Drawable {self.id} {self.kind} {self.text[:30]!r}>"


@dataclass
class Slide:
    doc: "KeynoteFile"
    index: int  # 1-based position in the deck (skipped slides included)
    node: Obj  # KN.SlideNodeArchive
    obj: Obj  # KN.SlideArchive

    @property
    def id(self) -> int:
        return self.obj.id

    @property
    def skipped(self) -> bool:
        return bool(self.node.msg.isSkipped)

    @property
    def depth(self) -> int:
        return int(self.node.msg.depth)

    @property
    def layout(self) -> Obj | None:
        m = self.obj.msg
        return self.doc.get(m.template_slide.identifier) if m.HasField("template_slide") else None

    @property
    def layout_name(self) -> str:
        lay = self.layout
        return lay.msg.name if lay is not None else ""

    @property
    def drawables(self) -> list[Drawable]:
        """Top-level drawables, back-to-front (z-order)."""
        return self.doc._slide_drawables(self.obj)

    def walk(self) -> Iterator[Drawable]:
        """Every drawable, descending into groups."""

        def rec(ds):
            for d in ds:
                yield d
                yield from rec(d.children)

        return rec(self.drawables)

    def _placeholder(self, fieldname: str) -> Drawable | None:
        m = self.obj.msg
        if not m.HasField(fieldname):
            return None
        ident = getattr(m, fieldname).identifier
        for d in self.drawables:
            if d.id == ident:
                return d
        o = self.doc.get(ident)
        return Drawable(self.doc, o, _classify(o.msg)) if o else None

    @property
    def title_item(self) -> Drawable | None:
        return self._placeholder("titlePlaceholder")

    @property
    def body_item(self) -> Drawable | None:
        return self._placeholder("bodyPlaceholder")

    @property
    def title(self) -> str:
        """The title placeholder's text - even when the title is switched off (see title_visible)."""
        t = self.title_item
        return t.text.strip() if t else ""

    @property
    def title_visible(self) -> bool:
        """False when the slide's Title checkbox is off: the placeholder keeps its
        (often stale) text in the file but is not drawn."""
        t = self.title_item
        return t is not None and any(d.id == t.id for d in self.drawables)

    @property
    def notes_storage(self) -> Obj | None:
        m = self.obj.msg
        if not m.HasField("note"):
            return None
        note = self.doc.get(m.note.identifier)
        if note is None or not note.msg.HasField("containedStorage"):
            return None
        return self.doc.get(note.msg.containedStorage.identifier)

    @property
    def notes(self) -> str:
        st = self.notes_storage
        return storage_text(st.msg) if st else ""

    def __repr__(self):
        return f"<Slide {self.index} id={self.id} {self.title!r}>"


def _has(msg, name: str) -> bool:
    try:
        return msg.HasField(name)
    except ValueError:
        return False


def _classify(msg) -> str:
    t = type_name(msg)
    if t == "KN.PlaceholderArchive":
        return {
            1: "slide_number",
            2: "title",
            3: "body",
            4: "object_placeholder",
        }.get(msg.kind, "text")
    if t == "TSWP.ShapeInfoArchive":
        return "text" if msg.is_text_box else "shape"
    return {
        "TSD.ShapeArchive": "shape",
        "TSD.ImageArchive": "image",
        "TSD.MovieArchive": "movie",
        "TSD.GroupArchive": "group",
        "TSD.ConnectionLineArchive": "line",
        "TST.TableInfoArchive": "table",
        "TSCH.ChartDrawableArchive": "chart",
        "TSA.GalleryInfoArchive": "gallery",
        "TSD.MediaArchive": "media",
    }.get(t, "other")


# --------------------------------------------------------------------------
# Text storage helpers
# --------------------------------------------------------------------------


def storage_text(storage_msg) -> str:
    """Plain text of a TSWP.StorageArchive; paragraphs separated by \\n."""
    t = storage_msg.text[0] if len(storage_msg.text) else ""
    for ch in "\r ":
        t = t.replace(ch, "\n")
    return t.replace(" ", "\n")  # soft line break


def _attr_tables(storage_msg):
    """Yield (name, table) for every attribute table present on the storage."""
    for fd, value in storage_msg.ListFields():
        if fd.name.startswith("table_") and fd.message_type is not None:
            yield fd.name, value


def storage_replace_range(storage_msg, start: int, end: int, new: str) -> None:
    """Replace UTF-16 range [start, end) of the storage text with `new`.

    Keeps every attribute table (paragraph/character/list styles, ...) in step:
    runs after the edit shift, runs that began inside the replaced range are
    dropped, and each new paragraph break gets the paragraph style of the
    paragraph it splits.
    """
    old = storage_msg.text[0] if len(storage_msg.text) else ""
    raw = _u16(old)
    n_old = len(raw) // 2
    if not (0 <= start <= end <= n_old):
        raise KeynoteError(f"range {start}:{end} outside text of length {n_old}")
    new = new.replace("\r\n", "\n").replace("\r", "\n")
    new_raw = _u16(new)
    n_new = len(new_raw) // 2
    delta = n_new - (end - start)
    result = _from_u16(raw[: start * 2] + new_raw + raw[end * 2 :])
    if len(storage_msg.text):
        storage_msg.text[0] = result
    else:
        storage_msg.text.append(result)
    total = n_old + delta

    for name, table in _attr_tables(storage_msg):
        entries = list(table.entries)
        if not entries or not hasattr(entries[0], "character_index"):
            continue
        kept = []
        for e in entries:
            i = e.character_index
            if i <= start:
                kept.append((i, e))
            elif i >= end:
                kept.append((i + delta, e))
            # else: the run began inside the replaced text -> drop it
        if name in _PER_PARAGRAPH_TABLES:
            # one entry per paragraph: add entries for breaks introduced by `new`
            have = {i for i, _ in kept}
            units = _u16(new)
            for k in range(n_new):
                if _from_u16(units[k * 2 : k * 2 + 2]) in PARA_BREAKS:
                    pos = start + k + 1
                    if pos not in have and pos <= total:
                        src = max((x for x in kept if x[0] <= start), key=lambda x: x[0], default=None)
                        if src is not None:
                            clone = type(src[1])()
                            clone.CopyFrom(src[1])
                            kept.append((pos, clone))
                            have.add(pos)
        if name in _PARAGRAPH_ALIGNED_TABLES:
            # a deleted paragraph break leaves a stale entry mid-paragraph: drop those
            res_units = _u16(result)
            starts = {0} | {
                k + 1
                for k in range(len(res_units) // 2)
                if _from_u16(res_units[k * 2 : k * 2 + 2]) in PARA_BREAKS
            }
            kept = [x for x in kept if x[0] in starts]
        kept.sort(key=lambda x: x[0])
        dedup: dict[int, Any] = {}
        for i, e in kept:
            dedup[i] = e  # the later run wins when two land on one index
        new_entries = []
        for i, e in sorted(dedup.items()):
            c = type(e)()
            c.CopyFrom(e)
            c.character_index = i
            new_entries.append(c)
        del table.entries[:]
        table.entries.extend(new_entries)


def storage_set_text(storage_msg, new: str) -> None:
    """Replace the whole text, keeping the first paragraph's styles for all of it."""
    old = storage_msg.text[0] if len(storage_msg.text) else ""
    storage_replace_range(storage_msg, 0, u16len(old), new)


def storage_paragraphs(storage_msg) -> list[tuple[int, str]]:
    """[(list_level, text)] per paragraph."""
    raw = storage_msg.text[0] if len(storage_msg.text) else ""
    levels = []
    if storage_msg.HasField("table_para_data"):
        levels = sorted((e.character_index, e.first) for e in storage_msg.table_para_data.entries)
    out = []
    pos = 0
    cur = ""
    start = 0

    def level_at(i):
        lv = 0
        for ci, first in levels:
            if ci <= i:
                lv = first
            else:
                break
        return lv

    for ch in raw:
        if ch in PARA_BREAKS:
            out.append((level_at(start), cur))
            cur = ""
            pos += 1
            start = pos
        else:
            cur += "\n" if ch == " " else ch
            pos += u16len(ch)
    out.append((level_at(start), cur))
    return out


# --------------------------------------------------------------------------
# Table cell decoding
# --------------------------------------------------------------------------


def _decimal128(buf: bytes) -> float:
    exp = (((buf[15] & 0x7F) << 7) | (buf[14] >> 1)) - 0x1820
    mantissa = buf[14] & 1
    for i in range(13, -1, -1):
        mantissa = mantissa * 256 + buf[i]
    if buf[15] & 0x80:
        mantissa = -mantissa
    return float(Decimal(mantissa).scaleb(exp))


def _fmt_number(v: float) -> str:
    if v == int(v) and abs(v) < 1e15:
        return str(int(v))
    return repr(round(v, 10)).rstrip("0").rstrip(".") if "e" not in repr(v) else repr(v)


def _fmt_places(v: float, places: int | None, thousands: bool, max_auto: int = 10) -> str:
    """Fixed `places` decimals, or (None) as many as the value needs."""
    if places is None:
        s = f"{v:.{max_auto}f}".rstrip("0").rstrip(".") if abs(v) < 1e15 else repr(v)
        if thousands and "e" not in s:
            head, _, tail = s.partition(".")
            s = f"{int(head):,}" + ("." + tail if tail else "")
            if v < 0 and not s.startswith("-"):
                s = "-" + s
        return s
    return f"{v:,.{places}f}" if thousands else f"{v:.{places}f}"


def _fmt_cell_number(v: float, fmt, custom: dict) -> str:
    """A number the way the cell's format displays it (decimal, percent, scientific, custom)."""
    if fmt is None:
        return _fmt_number(v)
    ft = fmt.format_type
    places = fmt.decimal_places if fmt.HasField("decimal_places") and fmt.decimal_places < 250 else None
    thousands = fmt.show_thousands_separator
    if ft == 256 or ft == 257:  # decimal, currency (symbol not rendered)
        return _fmt_places(v, places, thousands)
    if ft == 258:  # percent: stored as a fraction
        return _fmt_places(v * 100, places, thousands) + "%"
    if ft == 259:
        return f"{v:.{places if places is not None else 2}E}"
    if ft in (270, 271, 272) and fmt.HasField("custom_uid"):
        cf = custom.get((fmt.custom_uid.lower, fmt.custom_uid.upper))
        pattern = cf.custom_format_string if cf is not None else ""
        if pattern:
            # literals are quoted: #,###.##'%'  ->  prefix "", digits "#,###.##", suffix "%"
            parts = re.split(r"'([^']*)'", pattern)
            digits = "".join(parts[0::2])
            lits = parts[1::2]
            m = re.search(r"[#0][#0,]*(?:\.[#0]+)?", digits)
            if m:
                body = m.group(0)
                dec = body.partition(".")[2]
                lo, hi = dec.count("0"), len(dec)
                val = v * (cf.scale_factor or 1)
                out = f"{val:.{hi}f}"
                if hi > lo:
                    head, _, tail = out.partition(".")
                    tail = tail[:lo] + tail[lo:].rstrip("0")
                    out = head + ("." + tail if tail else "")
                if "," in body:
                    head, _, tail = out.partition(".")
                    out = f"{int(head):,}" + ("." + tail if tail else "")
                    if val < 0 and not out.startswith("-"):
                        out = "-" + out
                before = digits[: m.start()].strip()
                after = digits[m.end():].strip()
                pre = "".join(lits) if (lits and pattern.lstrip().startswith("'")) else ""
                suf = "".join(lits) if not pre else ""
                return pre + before + out + after + suf
    return _fmt_number(v)


# --------------------------------------------------------------------------
# The document
# --------------------------------------------------------------------------


class KeynoteFile:
    def __init__(self, path: str):
        self.path = path
        self._pkg = _Package(path)
        self.names = self._pkg.names()
        self.files: dict[str, IWAFile] = {}
        self._orig_bytes: dict[str, bytes] = {}
        self._orig_hash: dict[str, str] = {}
        self.objects: dict[int, Obj] = {}
        self._added_files: dict[str, bytes] = {}
        self._replaced_files: dict[str, bytes] = {}
        iwa_names = [n for n in self.names if n.startswith("Index/") and n.endswith(".iwa")]
        if not iwa_names:
            raise KeynoteError(f"{path}: no Index/*.iwa found - not a Keynote 6+ document")
        for name in iwa_names:
            data = self._pkg.read(name)
            self._orig_bytes[name] = data
            try:
                f = IWAFile.from_buffer(data, name)
            except Exception as e:
                raise KeynoteError(
                    f"{path}: cannot decode {name} ({type(e).__name__}). If the deck is password-protected, "
                    "open it in Keynote and work through AppleScript instead."
                ) from e
            self.files[name] = f
            self._orig_hash[name] = self._content_hash(f)
            for chunk in f.chunks:
                for arch in chunk.archives:
                    if arch.objects:
                        ident = int(arch.header.identifier)
                        self.objects[ident] = Obj(ident, arch.objects[0], arch, name)
        self._data_names: dict[int, str] | None = None
        self._drawable_cache: dict[int, list[Drawable]] = {}

    # ---- low level -------------------------------------------------------

    @staticmethod
    def _content_hash(f: IWAFile) -> str:
        h = hashlib.sha1()
        for chunk in f.chunks:
            for arch in chunk.archives:
                h.update(arch.header.SerializeToString())
                for o in arch.objects:
                    h.update(o.SerializeToString())
        return h.hexdigest()

    def get(self, ident: int) -> Obj | None:
        return self.objects.get(int(ident))

    def find(self, type_: str) -> list[Obj]:
        """All objects whose Protobuf type is `type_`, e.g. 'TSWP.StorageArchive'."""
        return [o for o in self.objects.values() if o.type == type_]

    def first(self, type_: str) -> Obj:
        for o in self.objects.values():
            if o.type == type_:
                return o
        raise KeynoteError(f"No {type_} in document")

    @property
    def properties(self) -> dict:
        import plistlib

        try:
            return plistlib.loads(self._pkg.read("Metadata/Properties.plist"))
        except Exception:
            return {}

    @property
    def format_version(self) -> str:
        return str(self.properties.get("fileFormatVersion", "?"))

    @property
    def size(self) -> tuple[float, float]:
        """Slide size in points (width, height)."""
        s = self.first("KN.ShowArchive").msg.size
        return (s.width, s.height)

    def data_filename(self, data_id: int) -> str | None:
        """Map a TSP.DataReference identifier to its file name under Data/."""
        if self._data_names is None:
            self._data_names = {}
            for o in self.find("TSP.PackageMetadata"):
                for d in o.msg.datas:
                    self._data_names[int(d.identifier)] = d.file_name or d.preferred_file_name
        return self._data_names.get(int(data_id))

    # ---- slides ----------------------------------------------------------

    def _slide_node_ids(self) -> list[int]:
        show = self.first("KN.ShowArchive").msg
        tree = show.slideTree
        if len(tree.slides):
            return [r.identifier for r in tree.slides]
        out = []

        def rec(ident):
            node = self.get(ident)
            if node is None:
                return
            if node.msg.HasField("slide"):
                out.append(ident)
            for c in node.msg.children:
                rec(c.identifier)

        if tree.HasField("rootSlideNode"):
            root = self.get(tree.rootSlideNode.identifier)
            for c in root.msg.children if root else []:
                rec(c.identifier)
        return out

    def slides(self) -> list[Slide]:
        out = []
        for i, nid in enumerate(self._slide_node_ids(), 1):
            node = self.get(nid)
            if node is None:
                continue
            so = self.get(node.msg.slide.identifier)
            if so is None:
                continue
            out.append(Slide(self, i, node, so))
        return out

    def slide(self, index: int) -> Slide:
        for s in self.slides():
            if s.index == index:
                return s
        raise KeynoteError(f"No slide {index}")

    def layouts(self) -> list[Obj]:
        """Slide layouts (template slides) of the document's theme."""
        theme = self.first("KN.ThemeArchive").msg
        out = []
        for r in theme.templates:
            o = self.get(r.identifier)
            if o is not None and o.type == "KN.SlideNodeArchive":
                o = self.get(o.msg.slide.identifier)
            if o is not None:
                out.append(o)
        return out

    def layout_index(self, slide: "Slide") -> int | None:
        """1-based position of the slide's layout among doc.layouts() (= AppleScript `slide layout N`)."""
        lay = slide.layout
        if lay is None:
            return None
        for i, o in enumerate(self.layouts(), 1):
            if o.id == lay.id:
                return i
        return None

    def _slide_drawables(self, slide_obj: Obj) -> list[Drawable]:
        if slide_obj.id in self._drawable_cache:
            return self._drawable_cache[slide_obj.id]
        m = slide_obj.msg
        refs = list(m.drawables_z_order) or list(m.owned_drawables)
        seen = set()
        out = []
        for r in refs + list(m.owned_drawables):
            if r.identifier in seen:
                continue
            seen.add(r.identifier)
            d = self._make_drawable(r.identifier, None)
            if d:
                out.append(d)
        self._drawable_cache[slide_obj.id] = out
        return out

    def _make_drawable(self, ident: int, parent: Drawable | None) -> Drawable | None:
        o = self.get(ident)
        if o is None:
            return None
        d = Drawable(self, o, _classify(o.msg), parent)
        if d.kind == "group":
            for c in o.msg.children:
                cd = self._make_drawable(c.identifier, d)
                if cd:
                    d.children.append(cd)
        return d

    # ---- tables & charts -------------------------------------------------

    def table_cells(self, table: Drawable | Obj, bold: list | None = None) -> list[list[str]]:
        """Cell values of a table as rows of strings ('' for empty cells).

        Numbers come out the way the cell displays them (thousands separators,
        percent, decimal places). Pass a list as `bold` to collect the (row, col)
        pairs (0-based) of cells whose own text style is bold - i.e. emphasis
        added by hand, not the header rows' default weight.
        """
        tobj = table.obj if isinstance(table, Drawable) else table
        model = self.get(tobj.msg.tableModel.identifier).msg
        nrows, ncols = model.number_of_rows, model.number_of_columns
        ds = model.base_data_store
        strings = self._datalist(ds.stringTable.identifier) if ds.HasField("stringTable") else {}
        rich = self._datalist(ds.rich_text_table.identifier) if ds.HasField("rich_text_table") else {}
        formats = self._datalist(ds.format_table.identifier) if ds.HasField("format_table") else {}
        styles = self._datalist(ds.styleTable.identifier) if ds.HasField("styleTable") else {}
        custom = {}
        for cl in self.find("TSK.CustomFormatListArchive"):
            for u, cf in zip(cl.msg.uuids, cl.msg.custom_formats):
                custom[(u.lower, u.upper)] = cf.default_format
        ctx = (formats, styles, custom)
        grid = [["" for _ in range(ncols)] for _ in range(nrows)]
        tile_size = ds.tiles.tile_size or 256
        for tref in ds.tiles.tiles:
            tile = self.get(tref.tile.identifier)
            if tile is None:
                continue
            for ri in tile.msg.rowInfos:
                r = tref.tileid * tile_size + ri.tile_row_index
                buf, offs = ri.cell_storage_buffer, ri.cell_offsets
                if not buf:
                    if ri.cell_storage_buffer_pre_bnc:
                        raise KeynoteError(
                            "table uses the pre-2019 cell storage; open the deck in Keynote "
                            "and re-save it, or read the table with keynote_app.py"
                        )
                    continue
                n = len(offs) // 2
                offsets = struct.unpack(f"<{n}h", offs[: n * 2])
                for c, off in enumerate(offsets):
                    if off < 0 or c >= ncols or r >= nrows:
                        continue
                    if ri.has_wide_offsets:
                        off *= 4
                    try:
                        grid[r][c], is_bold = self._decode_cell(buf, off, strings, rich, ctx)
                        if is_bold and bold is not None:
                            bold.append((r, c))
                    except Exception:
                        grid[r][c] = "?"
        return grid

    def _table_headers(self, table: Drawable | Obj):
        """(model, {row index: header}, {column index: header}) - the TST.HeaderStorageBucket entries."""
        tobj = table.obj if isinstance(table, Drawable) else table
        model = self.get(tobj.msg.tableModel.identifier).msg
        ds = model.base_data_store
        rows, cols = {}, {}
        for b in ds.rowHeaders.buckets:
            o = self.get(b.identifier)
            for hd in (o.msg.headers if o is not None else []):
                rows[hd.index] = hd
        o = self.get(ds.columnHeaders.identifier) if ds.HasField("columnHeaders") else None
        for hd in (o.msg.headers if o is not None else []):
            cols[hd.index] = hd
        return model, rows, cols

    def table_grid(self, table: Drawable | Obj) -> tuple[list[float], list[float]]:
        """(column widths, row heights) in points, hidden rows / columns left out.

        Their sums are the table's real width and height (the table name caption,
        if shown, sits outside). The size in the table's geometry is stale."""
        model, rows, cols = self._table_headers(table)

        def sizes(n, headers, default):
            out = []
            for i in range(n):
                hd = headers.get(i)
                if hd is not None and hd.hidingState:
                    continue
                out.append(float(hd.size) if hd is not None and hd.size > 0 else float(default))
            return out

        return (sizes(model.number_of_columns, cols, model.default_column_width),
                sizes(model.number_of_rows, rows, model.default_row_height))

    def table_caption_height(self, table: Drawable | Obj) -> float | None:
        """Height of the name caption Keynote draws above a table: 0 when the name
        is not shown, None when it is shown but the file does not say how tall.
        The table's stored position is the caption's top-left; the grid starts
        this much lower."""
        tobj = table.obj if isinstance(table, Drawable) else table
        model = self.get(tobj.msg.tableModel.identifier).msg
        if not model.table_name_enabled:
            return 0.0
        if model.table_name_height > 0:
            return float(model.table_name_height)
        b = Drawable(self, tobj, "table").base if not isinstance(table, Drawable) else table.base
        if b is not None and b.HasField("geometry"):
            extra = b.geometry.size.height - sum(self.table_grid(table)[1])
            if 0 < extra < 120:  # the stored size is current only when Keynote last laid the caption out
                return float(extra)
        return None

    def table_row_fits(self, table: Drawable | Obj) -> list[tuple[int, float, float]]:
        """[(row index, row height, height its text needs)] as Keynote measured
        when it last saved the file (TST.WidthHeightCache) - stale if cells,
        fonts or padding were changed in the file since. Empty if not recorded."""
        tobj = table.obj if isinstance(table, Drawable) else table
        m = tobj.msg
        if not _has(m, "layout_engine") or not m.layout_engine.HasField("width_height_cache"):
            return []
        model, rows, _cols = self._table_headers(table)
        out = []
        for i, e in enumerate(m.layout_engine.width_height_cache.rows_fitting_entries):
            if i >= model.number_of_rows:
                break
            hd = rows.get(i)
            if hd is not None and hd.hidingState:
                continue
            size = float(hd.size) if hd is not None and hd.size > 0 else float(model.default_row_height)
            out.append((i, size, float(e.fitting_size)))
        return out

    def set_table_row_height(self, table: Drawable | Obj, row: int, height: float) -> None:
        """Set one row's height (0-based row) in the file."""
        _model, rows, _cols = self._table_headers(table)
        if row not in rows:
            raise KeynoteError(f"table row {row + 1} has no header entry to resize")
        rows[row].size = float(height)

    def table_name(self, table: Drawable | Obj) -> str:
        """The table's name if it is shown above the table as a caption, else ''."""
        tobj = table.obj if isinstance(table, Drawable) else table
        model = self.get(tobj.msg.tableModel.identifier).msg
        return model.table_name if model.table_name_enabled else ""

    def _datalist(self, ident: int) -> dict[int, Any]:
        o = self.get(ident)
        out: dict[int, Any] = {}
        if o is None:
            return out

        def take(msg):
            for e in msg.entries:
                out[e.key] = e
            for seg in msg.segments:
                s = self.get(seg.identifier)
                if s is not None:
                    take(s.msg)

        take(o.msg)
        return out

    def _decode_cell(self, buf: bytes, off: int, strings, rich, ctx=({}, {}, {})) -> tuple[str, bool]:
        if buf[off] != 5:
            return "?", False
        formats, styles, custom = ctx
        ctype = buf[off + 1]
        flags = struct.unpack_from("<I", buf, off + 8)[0]
        p = off + 12
        d128 = dbl = secs = None
        if flags & 0x1:
            d128 = _decimal128(buf[p : p + 16])
            p += 16
        if flags & 0x2:
            dbl = struct.unpack_from("<d", buf, p)[0]
            p += 8
        if flags & 0x4:
            secs = struct.unpack_from("<d", buf, p)[0]
            p += 8
        # then one int32 per set bit: 0x8 string, 0x10 rich text, 0x20 cell style, 0x40 text style,
        # 0x80 conditional style, 0x100 its rule, 0x200 formula, 0x400 control, 0x800 formula error,
        # 0x1000 suggest, 0x2000 number format, 0x4000 currency, 0x8000 date, 0x10000 duration,
        # 0x20000 text, 0x40000 boolean format
        ids = {}
        for bit in range(3, 19):
            if flags & (1 << bit):
                ids[1 << bit] = struct.unpack_from("<i", buf, p)[0]
                p += 4
        sid, rid = ids.get(0x8), ids.get(0x10)
        is_bold = False
        if 0x40 in ids:
            e = styles.get(ids[0x40])
            st = self.get(e.reference.identifier) if e is not None else None
            if st is not None and hasattr(st.msg, "char_properties"):
                is_bold = bool(st.msg.char_properties.bold)
        if ctype in (2, 10):  # number, currency
            if d128 is None:
                return "", is_bold
            fe = formats.get(ids.get(0x4000 if ctype == 10 and 0x4000 in ids else 0x2000))
            try:
                return _fmt_cell_number(d128, fe.format if fe is not None else None, custom), is_bold
            except Exception:
                return _fmt_number(d128), is_bold
        if ctype == 3:  # text
            e = strings.get(sid)
            return (e.string if e is not None else ""), is_bold
        if ctype == 5:  # date: seconds since 2001-01-01
            import datetime

            return (datetime.datetime(2001, 1, 1) + datetime.timedelta(seconds=secs or 0)).isoformat(sep=" "), is_bold
        if ctype == 6:  # bool
            return ("TRUE" if dbl else "FALSE"), is_bold
        if ctype == 7:  # duration (seconds)
            return f"{_fmt_number(dbl or 0)}s", is_bold
        if ctype == 8:
            return "#ERROR", is_bold
        if ctype == 9:  # rich text
            e = rich.get(rid)
            if e is not None and e.HasField("rich_text_payload"):
                payload = self.get(e.rich_text_payload.identifier)
                if payload is not None:
                    st = self.get(payload.msg.storage.identifier)
                    if st is not None:
                        return storage_text(st.msg), is_bold
            return "", is_bold
        return "", is_bold

    def chart_data(self, chart: Drawable | Obj) -> dict:
        """{'type', 'row_names', 'column_names', 'rows': [[...]]} for a chart."""
        cobj = chart.obj if isinstance(chart, Drawable) else chart
        ext = None
        for fd, value in cobj.msg.ListFields():
            if fd.is_extension and fd.name == "unity":
                ext = value
        if ext is None:
            return {}
        rows = []
        for gr in ext.grid.grid_row:
            row = []
            for v in gr.value:
                got = None
                for f in ("numeric_value", "date_value", "duration_value", "date_value_1_0"):
                    if v.HasField(f):
                        got = getattr(v, f)
                        break
                row.append(got)
            rows.append(row)
        try:
            enum = cobj.msg.DESCRIPTOR.file.pool.FindEnumTypeByName("TSCH.ChartType")
            tname = enum.values_by_number[ext.chart_type].name
        except Exception:
            tname = str(ext.chart_type)
        return {
            "type": tname,
            "row_names": list(ext.grid.row_name),
            "column_names": list(ext.grid.column_name),
            "rows": rows,
        }

    # ---- text edits ------------------------------------------------------

    def storages(self) -> list[Obj]:
        return self.find("TSWP.StorageArchive")

    def replace_text(self, find: str, replace: str, slides: list[int] | None = None, count: int = 0) -> int:
        """Replace literal `find` with `replace` in slide text, notes and table cells.

        `slides` limits the edit to those 1-based slide numbers (text boxes,
        placeholders, shapes and notes on them). Returns the number of
        replacements made.
        """
        if not find:
            return 0
        targets: list[Any] = []
        if slides is None:
            targets = [o.msg for o in self.storages()]
        else:
            for s in self.slides():
                if s.index not in slides:
                    continue
                for d in s.walk():
                    st = d.storage
                    if st is not None:
                        targets.append(st.msg)
                ns = s.notes_storage
                if ns is not None:
                    targets.append(ns.msg)
        done = 0
        for msg in targets:
            text = msg.text[0] if len(msg.text) else ""
            pos = text.find(find)
            while pos != -1:
                start = u16len(text[:pos])
                storage_replace_range(msg, start, start + u16len(find), replace)
                done += 1
                if count and done >= count:
                    return done
                text = msg.text[0]
                pos = text.find(find, pos + len(replace))
        if slides is None:
            # plain-string table cells live in string tables, not storages
            for o in self.find("TST.TableDataList"):
                for e in o.msg.entries:
                    if e.HasField("string") and find in e.string:
                        done += e.string.count(find)
                        e.string = e.string.replace(find, replace)
        return done

    # ---- save ------------------------------------------------------------

    def changed_files(self) -> list[str]:
        return [n for n, f in self.files.items() if self._content_hash(f) != self._orig_hash[n]]

    def replace_data(self, name: str, data: bytes) -> None:
        """Replace a media file (e.g. 'Data/photo-12.png') byte-for-byte."""
        if name not in self.names:
            raise KeynoteError(f"{name} is not in the package")
        self._replaced_files[name] = data

    def save(self, out_path: str, overwrite: bool = False) -> list[str]:
        """Write the document to `out_path`; returns the .iwa files that changed.

        Saving over the source deck needs overwrite=True. Never save over a
        deck that is open in Keynote - Keynote will not notice, and its next
        save silently discards your edit.
        """
        same = os.path.exists(out_path) and os.path.realpath(out_path) == os.path.realpath(self.path)
        if same and not overwrite:
            raise KeynoteError("Refusing to overwrite the source deck: save to a new path or pass overwrite=True.")
        changed = self.changed_files()
        replace = {n: self.files[n].to_buffer() for n in changed}
        replace.update(self._replaced_files)
        self._pkg.write_copy(out_path, replace, self._added_files)
        return changed

# --------------------------------------------------------------------------
# Creating objects and style variations (methods attached to KeynoteFile)
# --------------------------------------------------------------------------


def _metadata(self):
    return self.first("TSP.PackageMetadata").msg


def _component_of(self, obj: Obj):
    """The TSP.ComponentInfo describing the .iwa file `obj` lives in."""
    f = self.files[obj.file]
    root_id = int(f.chunks[0].archives[0].header.identifier)
    for c in self._metadata().components:
        if int(c.identifier) == root_id:
            return c
    locator = os.path.splitext(os.path.basename(obj.file))[0]
    for c in self._metadata().components:
        if (c.locator or c.preferred_locator) == locator:
            return c
    return None


def new_object(self, msg, near: Obj, references: list[int] = ()) -> Obj:
    """Add `msg` as a new archive in the same .iwa file as `near`; returns its Obj.

    `references` are the identifiers of objects the new message points at
    (strong references only - leave out the stylesheet back-pointer).
    """
    meta = self._metadata()
    ident = int(meta.last_object_identifier) + 1
    meta.last_object_identifier = ident
    from keynote_parser.codec import IWAArchiveSegment

    header = type(near.archive.header)()
    header.identifier = ident
    mi = header.message_infos.add()
    src = None
    for o in self.objects.values():
        if type(o.msg) is type(msg):
            src = o.archive.header.message_infos[0]
            break
    if src is None:
        # nothing of this type in the deck yet: take the type number from keynote-parser's registry
        from keynote_parser import codec as _c

        id_name = _c.import_version()[0]
        want = type_name(msg)
        tid = next((int(k) for k, v in id_name.items() if getattr(v, "DESCRIPTOR", None) is not None and v.DESCRIPTOR.full_name == msg.DESCRIPTOR.full_name), None)
        if tid is None:
            raise KeynoteError(f"no existing {want} to model the new object on")
        mi.type = tid
        mi.version.extend(near.archive.header.message_infos[0].version)
    else:
        mi.type = src.type
        mi.version.extend(src.version)
    mi.length = len(msg.SerializeToString())
    mi.object_references.extend(int(r) for r in references)
    seg = IWAArchiveSegment(header, [msg])
    self.files[near.file].chunks[-1].archives.append(seg)
    obj = Obj(ident, msg, seg, near.file)
    self.objects[ident] = obj
    return obj


def _retarget(self, holder: Obj, old_id: int | None, new: Obj) -> None:
    """Bookkeeping after `holder` was changed to reference `new` instead of `old_id`."""
    for mi in holder.archive.header.message_infos:
        refs = [int(r) for r in mi.object_references]
        if old_id is not None and old_id in refs and not _still_references(holder.msg, old_id):
            refs.remove(old_id)
        if new.id not in refs:
            refs.append(new.id)
        del mi.object_references[:]
        mi.object_references.extend(refs)
        break
    hc, nc = self._component_of(holder), self._component_of(new)
    if hc is not None and nc is not None and hc is not nc:
        if not any(int(e.object_identifier) == new.id for e in hc.external_references):
            e = hc.external_references.add()
            e.component_identifier = nc.identifier
            e.object_identifier = new.id


def _still_references(msg, ident: int) -> bool:
    for fd, value in msg.ListFields():
        if fd.message_type is None:
            continue
        items = value if fd.is_repeated else [value]
        if fd.message_type.full_name == "TSP.Reference":
            if any(int(v.identifier) == ident for v in items):
                return True
        elif not fd.message_type.GetOptions().map_entry:
            if any(_still_references(v, ident) for v in items):
                return True
    return False


def _style_base(style_msg):
    """The TSS.StyleArchive at the bottom of a style's `super` chain."""
    m = style_msg
    while type_name(m) != "TSS.StyleArchive":
        m = m.super
    return m


def make_style_variation(self, parent: Obj) -> Obj:
    """A new, empty style whose parent is `parent` (what Keynote makes for a local override)."""
    msg = type(parent.msg)()
    base_p = _style_base(parent.msg)
    # mirror the super-chain so every level exists
    src, dst = parent.msg, msg
    while type_name(src) != "TSS.StyleArchive":
        dst.super.SetInParent()
        src, dst = src.super, dst.super
    # a variation of a variation hangs off the same named parent
    root = parent
    if base_p.is_variation and base_p.HasField("parent"):
        root = self.get(base_p.parent.identifier) or parent
        msg.CopyFrom(parent.msg)  # carry the existing overrides along
    b = _style_base(msg)
    b.ClearField("name")
    b.ClearField("style_identifier")
    b.parent.identifier = root.id
    b.is_variation = True
    if base_p.HasField("stylesheet"):
        b.stylesheet.identifier = base_p.stylesheet.identifier
    new = self.new_object(msg, parent, [root.id])
    sheet = self.get(base_p.stylesheet.identifier) if base_p.HasField("stylesheet") else None
    if sheet is None:
        sheet = self.first("TSS.StylesheetArchive")
    sheet.msg.styles.add().identifier = new.id
    entry = None
    for e in sheet.msg.parent_to_children_style_map:
        if int(e.parent.identifier) == root.id:
            entry = e
            break
    if entry is None:
        entry = sheet.msg.parent_to_children_style_map.add()
        entry.parent.identifier = root.id
    entry.children.add().identifier = new.id
    self._retarget(sheet, None, new)
    return new


def parse_color(spec) -> tuple[float, float, float, float]:
    """'#RRGGBB', '#RRGGBBAA', 'RRGGBB' or an (r, g, b[, a]) tuple of 0-1 floats."""
    if isinstance(spec, (tuple, list)):
        r, g, b, *rest = spec
        return (float(r), float(g), float(b), float(rest[0]) if rest else 1.0)
    s = str(spec).lstrip("#")
    if len(s) not in (6, 8):
        raise KeynoteError(f"bad color {spec!r}: use #RRGGBB or #RRGGBBAA")
    v = [int(s[i : i + 2], 16) / 255 for i in range(0, len(s), 2)]
    return (v[0], v[1], v[2], v[3] if len(v) == 4 else 1.0)


def _set_color(color_msg, spec) -> None:
    r, g, b, a = parse_color(spec)
    color_msg.Clear()
    color_msg.model = 1  # rgb
    color_msg.r, color_msg.g, color_msg.b, color_msg.a = r, g, b, a
    color_msg.rgbspace = 1  # srgb


def _set_text_color(char_props, spec) -> None:
    """Text color lives in two places: font_color (older Keynotes) and tsd_fill (current)."""
    _set_color(char_props.font_color, spec)
    char_props.tsd_fill.Clear()
    _set_color(char_props.tsd_fill.color, spec)


def _own_style(self, holder: Obj, ref_field_owner, field_name: str = "style") -> Obj:
    """Give `holder` its own style variation (so edits don't leak to other objects) and return it."""
    ref = getattr(ref_field_owner, field_name)
    old = self.get(ref.identifier)
    if old is None:
        raise KeynoteError(f"{holder} has no resolvable {field_name}")
    new = self.make_style_variation(old)
    ref.identifier = new.id
    self._retarget(holder, old.id, new)
    return new


def _shape_archive(msg):
    m = msg
    while m is not None and type_name(m) != "TSD.ShapeArchive":
        m = m.super if _has(m, "super") else None
    return m


def _drawable_style_props(self, d: Drawable):
    """(style Obj, TSD.ShapeStylePropertiesArchive) of a fresh variation for a shape/text box."""
    sh = _shape_archive(d.obj.msg)
    if sh is None or not sh.HasField("style"):
        raise KeynoteError(f"{d.kind} {d.id} is not a shape or text box")
    style = self._own_style(d.obj, sh)
    m = style.msg
    while type_name(m) != "TSD.ShapeStyleArchive":
        m = m.super
    return style, m


def set_fill(self, d: Drawable, color) -> None:
    """Solid fill color for a shape / text box ('#RRGGBB'), or None for no fill."""
    _style, m = self._drawable_style_props(d)
    fill = m.shape_properties.fill
    fill.Clear()
    if color is None:
        m.shape_properties.fill.SetInParent()
    else:
        _set_color(fill.color, color)
    m.override_count = max(m.override_count, 1)


def set_stroke(self, d: Drawable, color=None, width: float | None = None) -> None:
    """Outline of a shape / text box / line. width=0 removes it."""
    _style, m = self._drawable_style_props(d)
    parent = self.get(_style_base(_style.msg).parent.identifier)
    st = m.shape_properties.stroke
    pm = parent.msg
    while type_name(pm) != "TSD.ShapeStyleArchive":
        pm = pm.super
    if not m.shape_properties.HasField("stroke") and pm.shape_properties.HasField("stroke"):
        st.CopyFrom(pm.shape_properties.stroke)
    if width is not None and width <= 0:
        if not st.HasField("color"):
            _set_color(st.color, "#000000")
        st.width = st.width or 1
        st.pattern.Clear()
        st.pattern.type = 2  # TSDEmptyPattern = no line
    else:
        if color is not None:
            _set_color(st.color, color)
        elif not st.HasField("color"):
            _set_color(st.color, "#000000")
        if width is not None:
            st.width = width
        elif not st.width:
            st.width = 1
        st.pattern.Clear()
        st.pattern.type = 1  # TSDSolidPattern
        if not st.HasField("cap"):
            st.cap = 0
        if not st.HasField("join"):
            st.join = 0
        if not st.HasField("miter_limit"):
            st.miter_limit = 4
    m.override_count = max(m.override_count, 1)


def set_shadow(self, d: Drawable, enabled: bool = True, offset: float = 8, blur: int = 20, opacity: float = 0.25, angle: float = 90, color="#000000") -> None:
    """Soft drop shadow on a shape / text box (enabled=False removes it).
    angle is clockwise from the right: 90 = straight down, 45 = down-right."""
    _style, m = self._drawable_style_props(d)
    sh = m.shape_properties.shadow
    sh.Clear()
    _set_color(sh.color, color)
    sh.angle, sh.offset, sh.radius, sh.opacity = float(angle), float(offset), int(blur), float(opacity)
    sh.is_enabled = bool(enabled)
    sh.type = 0  # drop shadow
    m.override_count = max(m.override_count, 1)


_VALIGN = {"top": 0, "middle": 1, "center": 1, "bottom": 2}
_ALIGN = {"left": 0, "right": 1, "center": 2, "justify": 3, "natural": 4}


def set_text_layout(self, d: Drawable, valign: str | None = None, padding: float | None = None, shrink: bool | None = None) -> None:
    """Vertical alignment ('top'/'middle'/'bottom'), inner padding (pt) and
    shrink-text-to-fit of a shape / text box / placeholder."""
    style, _m = self._drawable_style_props(d)
    props = style.msg.shape_properties  # TSWP.ShapeStylePropertiesArchive
    if valign is not None:
        if valign not in _VALIGN:
            raise KeynoteError(f"valign must be one of {sorted(_VALIGN)}")
        props.vertical_alignment = _VALIGN[valign]
    if padding is not None:
        props.padding.left = props.padding.top = props.padding.right = props.padding.bottom = float(padding)
    if shrink is not None:
        props.shrink_to_fit = bool(shrink)
    style.msg.override_count = max(style.msg.override_count, 1)


_POLYGONS = ("arrow", "arrow_left", "arrow_up", "arrow_down", "chevron", "triangle", "diamond")


def _polygon(shape: str, w: float, h: float) -> list[tuple[float, float]]:
    """Outline points of a polygon shape filling a w x h frame."""
    if shape == "triangle":
        return [(w / 2, 0), (w, h), (0, h)]
    if shape == "diamond":
        return [(w / 2, 0), (w, h / 2), (w / 2, h), (0, h / 2)]
    vertical = shape in ("arrow_up", "arrow_down")
    L, T = (h, w) if vertical else (w, h)  # length along the pointing direction, thickness across
    if shape == "chevron":
        n = min(0.5 * L, 0.5 * T)
        pts = [(0, 0), (L - n, 0), (L, T / 2), (L - n, T), (0, T), (n, T / 2)]
    else:
        hl = min(0.5 * L, 0.6 * T)  # head length
        pts = [(0, 0.25 * T), (L - hl, 0.25 * T), (L - hl, 0), (L, T / 2), (L - hl, T), (L - hl, 0.75 * T), (0, 0.75 * T)]
    if shape == "arrow_left":
        pts = [(L - x, y) for x, y in pts]
    elif shape == "arrow_down":
        pts = [(y, x) for x, y in pts]
    elif shape == "arrow_up":
        pts = [(y, L - x) for x, y in pts]
    return pts


def set_shape(self, d: Drawable, shape: str, radius: float = 20.0) -> None:
    """Change the outline of a shape: 'rect', 'rounded' (corner `radius` in pt), 'oval',
    'arrow' (block arrow pointing right; 'arrow_left' / 'arrow_up' / 'arrow_down'),
    'chevron', 'triangle' (pointing up) or 'diamond'. The outline fills the frame."""
    sh = _shape_archive(d.obj.msg)
    if sh is None:
        raise KeynoteError(f"{d.kind} {d.id} is not a shape")
    g = d.geometry
    w, h = g.size.width, g.size.height
    ps = sh.pathsource
    flips = (ps.horizontalFlip, ps.verticalFlip)
    ps.Clear()
    ps.horizontalFlip, ps.verticalFlip = flips
    if shape == "rounded":
        sp = ps.scalar_path_source
        sp.type = 0  # kTSDRoundedRectangle
        sp.scalar = float(min(radius, w / 2, h / 2))
        sp.naturalSize.width, sp.naturalSize.height = w, h
        sp.is_curve_continuous = False
        return
    bp = ps.bezier_path_source
    bp.naturalSize.width, bp.naturalSize.height = w, h

    def el(kind, *pts):
        e = bp.path.elements.add()
        e.type = kind
        for x, y in pts:
            p = e.points.add()
            p.x, p.y = x, y

    if shape == "rect":
        el(1, (0, 0))
        el(2, (w, 0))
        el(2, (w, h))
        el(2, (0, h))
        el(5)
    elif shape == "oval":
        k = 0.5522847498
        rx, ry = w / 2, h / 2
        el(1, (w, ry))
        el(4, (w, ry + k * ry), (rx + k * rx, h), (rx, h))
        el(4, (rx - k * rx, h), (0, ry + k * ry), (0, ry))
        el(4, (0, ry - k * ry), (rx - k * rx, 0), (rx, 0))
        el(4, (rx + k * rx, 0), (w, ry - k * ry), (w, ry))
        el(5)
    elif shape in _POLYGONS:
        for i, pt in enumerate(_polygon(shape, w, h)):
            el(1 if i == 0 else 2, pt)
        el(5)
    else:
        raise KeynoteError(f"shape must be one of: rect, rounded, oval, {', '.join(sorted(_POLYGONS))}")


def set_frame(self, d: Drawable, x: float | None = None, y: float | None = None, w: float | None = None, h: float | None = None, rotation: float | None = None) -> None:
    """Move / resize an object (slide points from the top-left; any subset).

    Fine for shapes, text boxes, placeholders, images and lines. A shape's
    outline is rescaled with it. Not for tables and charts (their size is
    derived from rows/columns and the plot - resize those through the app).
    An auto-sized text box (w or h of 0) keeps growing with its text; moving it
    is fine, giving it a size is ignored by Keynote.
    """
    if d.kind in ("table", "chart"):
        if w is not None or h is not None:
            raise KeynoteError(f"a {d.kind} cannot be resized in the file - use the Keynote app (set width/height)")
    g = d.geometry
    if g is None:
        raise KeynoteError(f"{d.kind} {d.id} has no geometry")
    ow, oh = g.size.width, g.size.height
    if x is not None:
        g.position.x = float(x)
    if y is not None:
        g.position.y = float(y)
    if w is not None:
        g.size.width = float(w)
    if h is not None:
        g.size.height = float(h)
    if rotation is not None:
        g.angle = float(rotation)
    sh = _shape_archive(d.obj.msg)
    if sh is not None and (w is not None or h is not None) and sh.HasField("pathsource"):
        ps = sh.pathsource
        nw, nh = g.size.width, g.size.height
        for fd, sub in ps.ListFields():
            if fd.message_type is not None and fd.name != "bezier_path_source" and "naturalSize" in fd.message_type.fields_by_name:
                if sub.HasField("naturalSize"):
                    sub.naturalSize.width, sub.naturalSize.height = nw, nh
        if ps.HasField("bezier_path_source"):
            bp = ps.bezier_path_source
            sx = nw / ow if ow else 1.0
            sy = nh / oh if oh else 1.0
            nat = bp.naturalSize
            # the path is drawn in naturalSize units and stretched to the frame: keep both in step
            if abs(nat.width - ow) < 0.01 and abs(nat.height - oh) < 0.01:
                for e in bp.path.elements:
                    for pt in e.points:
                        pt.x *= sx
                        pt.y *= sy
                nat.width, nat.height = nw, nh


_ARROW_HEAD = [(1, (2.15, 0.0)), (2, (0.0, 4.3)), (2, (-2.15, 0.0)), (5, None), (1, (2.15, 0.0))]


def set_line_ends(self, d: Drawable, start: bool | None = None, end: bool | None = None) -> None:
    """Put an arrowhead on (True) or take it off (False) the start / end of a line."""
    _style, m = self._drawable_style_props(d)
    sp = m.shape_properties
    for flag, field in ((start, "tail_line_end"), (end, "head_line_end")):
        if flag is None:
            continue
        le = getattr(sp, field)
        le.Clear()
        if flag:
            for kind, pt in _ARROW_HEAD:
                e = le.path.elements.add()
                e.type = kind
                if pt is not None:
                    q = e.points.add()
                    q.x, q.y = pt
            le.line_join = 0  # miter
            le.end_point.x, le.end_point.y = 0.0, -0.8333333
            le.is_filled = True
            le.identifier = "simple arrow"
        else:
            le.SetInParent()  # an empty line end = none (overrides an inherited arrowhead)
    m.override_count = max(m.override_count, 1)


def _style_chain(self, style: Obj | None):
    """A style and its ancestors, nearest first."""
    seen = set()
    while style is not None and style.id not in seen:
        seen.add(style.id)
        yield style
        base = _style_base(style.msg)
        style = self.get(base.parent.identifier) if base.HasField("parent") else None


def _hex(color) -> str:
    v = [max(0, min(255, round(c * 255))) for c in (color.r, color.g, color.b)]
    out = "#%02X%02X%02X" % tuple(v)
    return out + "%02X" % round(color.a * 255) if color.HasField("a") and color.a < 0.999 else out


def _resolve(self, style: Obj | None, path: tuple[str, ...]):
    """First value of a nested field along a style's parent chain (None if never set)."""
    for st in self._style_chain(style):
        m = st.msg
        ok = True
        # the field may sit on the style message itself or on one of its `super`s
        while m is not None:
            cur = m
            ok = True
            for name in path:
                if not _has(cur, name) or not cur.HasField(name):
                    ok = False
                    break
                cur = getattr(cur, name)
            if ok:
                return cur
            m = m.super if _has(m, "super") and type_name(m) != "TSS.StyleArchive" else None
    return None


def _para_style_info(self, style: Obj | None) -> dict:
    out: dict = {}
    font = self._resolve(style, ("char_properties", "font_name"))
    if font is not None:
        out["font"] = font
    size = self._resolve(style, ("char_properties", "font_size"))
    if size is not None:
        out["size"] = round(size, 1)
    for key in ("bold", "italic"):
        v = self._resolve(style, ("char_properties", key))
        if v:
            out[key] = True
    col = self._resolve(style, ("char_properties", "tsd_fill", "color"))
    if col is None:
        col = self._resolve(style, ("char_properties", "font_color"))
    if col is not None:
        out["color"] = _hex(col)
    al = self._resolve(style, ("para_properties", "alignment"))
    if al is not None:
        out["align"] = {v: k for k, v in _ALIGN.items()}.get(int(al), str(al))
    ls = self._resolve(style, ("para_properties", "line_spacing"))
    if ls is not None and ls.HasField("amount"):
        out["line_spacing" if int(ls.mode) == 0 else "line_spacing_pt"] = round(ls.amount, 2)
    return out


def style_info(self, d: Drawable) -> dict:
    """The look of an object as currently resolved through its style chain.

    {'fill', 'stroke': {'color', 'width'} | None, 'shrink_to_fit', 'valign',
     'text': {'font', 'size', 'bold', 'italic', 'color', 'align', 'line_spacing'},
     'paragraphs': [...]}  - 'text' is the first paragraph's style; 'paragraphs'
    (one entry per paragraph) appears only when they differ. Character-level
    runs inside a paragraph (a bold word, a coloured phrase) are reported as
    'runs': [{'text', ...overrides}]. Keys that nothing sets are left out.
    """
    out: dict = {}
    sh = _shape_archive(d.obj.msg)
    if sh is not None and sh.HasField("style"):
        st = self.get(sh.style.identifier)
        fill = self._resolve(st, ("shape_properties", "fill"))
        if fill is not None:
            if fill.HasField("color"):
                out["fill"] = _hex(fill.color)
            elif fill.HasField("gradient"):
                out["fill"] = "gradient"
            elif fill.HasField("image"):
                out["fill"] = "image"
        stroke = self._resolve(st, ("shape_properties", "stroke"))
        if stroke is not None:
            if stroke.HasField("pattern") and int(stroke.pattern.type) == 2 or not stroke.width:
                out["stroke"] = None
            else:
                out["stroke"] = {"color": _hex(stroke.color), "width": round(stroke.width, 2)}
        for key, field in (("arrow_start", "tail_line_end"), ("arrow_end", "head_line_end")):
            le = self._resolve(st, ("shape_properties", field))
            if le is not None and len(le.path.elements):
                out[key] = True
        # text layout lives on the TSWP level of the same style
        for st2 in self._style_chain(st):
            m = st2.msg
            if _has(m, "shape_properties") and type_name(m) == "TSWP.ShapeStyleArchive":
                sp = m.shape_properties
                if "shrink_to_fit" not in out and sp.HasField("shrink_to_fit"):
                    out["shrink_to_fit"] = bool(sp.shrink_to_fit)
                if "valign" not in out and sp.HasField("vertical_alignment"):
                    out["valign"] = {0: "top", 1: "middle", 2: "bottom"}.get(int(sp.vertical_alignment), "top")
    storage = d.storage
    if storage is not None and storage_text(storage.msg).strip():
        msg = storage.msg
        ranges = paragraph_ranges(msg)
        entries = sorted(msg.table_para_style.entries, key=lambda e: e.character_index)
        infos = []
        for start, _end in ranges:
            ident = 0
            for e in entries:
                if e.character_index <= start and e.object.identifier:
                    ident = int(e.object.identifier)
            infos.append(self._para_style_info(self.get(ident)) if ident else {})
        if infos:
            out["text"] = infos[0]
            if any(i != infos[0] for i in infos[1:]):
                out["paragraphs"] = infos
        runs = []
        text16 = _u16(storage_text(msg))
        cs = sorted(msg.table_char_style.entries, key=lambda e: e.character_index)
        for i, e in enumerate(cs):
            if not e.object.identifier:
                continue
            a = e.character_index
            b = cs[i + 1].character_index if i + 1 < len(cs) else len(text16) // 2
            piece = _from_u16(text16[a * 2 : b * 2])
            if not piece.strip():
                continue
            st = self.get(int(e.object.identifier))
            info = {}
            if st is not None and _has(st.msg, "char_properties"):
                cp = st.msg.char_properties
                for key, field in (("font", "font_name"), ("size", "font_size"), ("bold", "bold"), ("italic", "italic")):
                    if cp.HasField(field):
                        info[key] = getattr(cp, field)
                if cp.HasField("tsd_fill") and cp.tsd_fill.HasField("color"):
                    info["color"] = _hex(cp.tsd_fill.color)
                elif cp.HasField("font_color"):
                    info["color"] = _hex(cp.font_color)
            if info:
                runs.append({"text": piece[:60], **info})
        if runs:
            out["runs"] = runs
    return out


_PLACEHOLDER_FIELDS = {"title": "titlePlaceholder", "body": "bodyPlaceholder", "slide_number": "slideNumberPlaceholder"}


def show_placeholder(self, slide: "Slide", which: str, visible: bool = True) -> None:
    """Switch a slide's title / body / slide_number placeholder on or off
    (the Title / Body / Slide Number checkboxes of Keynote's Format > Slide pane).

    A switched-off placeholder stays in the file but is not among the slide's
    drawables. Raises if the slide has no such placeholder at all (its layout
    lacks one - give the slide a layout that has it, through the app).
    """
    if which not in _PLACEHOLDER_FIELDS:
        raise KeynoteError(f"which must be one of {sorted(_PLACEHOLDER_FIELDS)}")
    m = slide.obj.msg
    field = _PLACEHOLDER_FIELDS[which]
    ident = int(getattr(m, field).identifier) if m.HasField(field) else 0
    if not ident or self.get(ident) is None:
        raise KeynoteError(f"slide {slide.index} has no {which} placeholder (its layout has none)")
    for lst in (m.owned_drawables, m.drawables_z_order):
        ids = [int(r.identifier) for r in lst]
        if visible and ident not in ids:
            if lst is m.drawables_z_order and not ids:
                continue  # no explicit z-order kept: owned_drawables is the order
            lst.add().identifier = ident
        elif not visible and ident in ids:
            keep = [i for i in ids if i != ident]
            del lst[:]
            for i in keep:
                lst.add().identifier = i
    self._drawable_cache.pop(slide.obj.id, None)


def placeholder_visible(self, slide: "Slide", which: str) -> bool | None:
    """True / False, or None when the slide has no such placeholder."""
    m = slide.obj.msg
    field = _PLACEHOLDER_FIELDS[which]
    if not m.HasField(field):
        return None
    ident = int(getattr(m, field).identifier)
    return any(d.id == ident for d in slide.drawables)


def background_info(self, slide: "Slide") -> str | None:
    """'#RRGGBB' of a slide's solid background, 'gradient' / 'image', or None when it shows the layout's."""
    m = slide.obj.msg
    st = self.get(m.style.identifier) if m.HasField("style") else None
    fill = self._resolve(st, ("slide_properties", "fill"))
    if fill is None:
        return None
    if fill.HasField("color"):
        return _hex(fill.color)
    return "gradient" if fill.HasField("gradient") else "image" if fill.HasField("image") else None


def set_background(self, slide: "Slide | Obj", color) -> None:
    """Solid background color for one slide (or a layout Obj from doc.layouts())."""
    holder = slide.obj if isinstance(slide, Slide) else slide
    style = self._own_style(holder, holder.msg)
    fill = style.msg.slide_properties.fill
    fill.Clear()
    _set_color(fill.color, color)
    style.msg.override_count = max(style.msg.override_count, 1)
    if isinstance(slide, Slide):
        slide.node.msg.background_is_no_fill_or_color_fill_with_alpha = parse_color(color)[3] < 1


def _slide_of(self, d: Drawable) -> Obj:
    top = d
    while top.parent is not None:
        top = top.parent
    for o in self.find("KN.SlideArchive"):
        if any(int(r.identifier) == top.id for r in o.msg.owned_drawables):
            return o
    raise KeynoteError(f"drawable {d.id} is not on a slide")


def reorder(self, d: Drawable, where: str) -> None:
    """Change stacking order of a top-level drawable: 'front' or 'back'."""
    so = self._slide_of(d)
    z = so.msg.drawables_z_order
    ids = [int(r.identifier) for r in z] or [int(r.identifier) for r in so.msg.owned_drawables]
    if d.id not in ids:
        raise KeynoteError("only top-level drawables can be reordered")
    ids.remove(d.id)
    ids = ids + [d.id] if where == "front" else [d.id] + ids
    del z[:]
    for i in ids:
        z.add().identifier = i
    self._drawable_cache.pop(so.id, None)


# ---- paragraphs ------------------------------------------------------------


def paragraph_ranges(storage_msg) -> list[tuple[int, int]]:
    """UTF-16 [start, end) of each paragraph (end excludes the break)."""
    raw = _u16(storage_msg.text[0] if len(storage_msg.text) else "")
    out, start = [], 0
    n = len(raw) // 2
    for k in range(n):
        if _from_u16(raw[k * 2 : k * 2 + 2]) in PARA_BREAKS:
            out.append((start, k))
            start = k + 1
    out.append((start, n))
    return out


def set_paragraph_levels(storage_msg, levels: list[int]) -> None:
    """Set the list (indent) level of each paragraph: 0 = top level."""
    ranges = paragraph_ranges(storage_msg)
    if len(levels) != len(ranges):
        raise KeynoteError(f"{len(levels)} levels given for {len(ranges)} paragraphs")
    table = storage_msg.table_para_data
    seconds = {}
    cur = 0
    for e in sorted(table.entries, key=lambda e: e.character_index):
        seconds[e.character_index] = e.second
    del table.entries[:]
    prev = None
    for (start, _end), lv in zip(ranges, levels):
        cur = seconds.get(start, cur)
        if prev is not None and prev == (lv, cur):
            continue
        e = table.entries.add()
        e.character_index, e.first, e.second = start, int(lv), cur
        prev = (lv, cur)


def _explicit_para_styles(self, storage: Obj) -> list:
    """Make table_para_style hold one entry, with an explicit style, per paragraph."""
    msg = storage.msg
    ranges = paragraph_ranges(msg)
    old = sorted(msg.table_para_style.entries, key=lambda e: e.character_index)
    resolved = []
    for start, _end in ranges:
        ident = 0
        for e in old:
            if e.character_index <= start and e.object.identifier:
                ident = int(e.object.identifier)
            if e.character_index > start:
                break
        resolved.append(ident)
    if not any(resolved):
        raise KeynoteError(f"storage {storage.id} has no paragraph style to build on")
    first = next(r for r in resolved if r)
    del msg.table_para_style.entries[:]
    out = []
    for (start, _end), ident in zip(ranges, resolved):
        e = msg.table_para_style.entries.add()
        e.character_index = start
        e.object.identifier = ident or first
        out.append(e)
    return out


_HEAVY_FACE = re.compile(r"(-W[6-9]\b|Bold|Semibold|SemiBold|DemiBold|Heavy|Black)")


def style_paragraphs(
    self,
    target: "Drawable | Obj",
    paragraphs: list[int] | None = None,
    align: str | None = None,
    bold: bool | None = None,
    italic: bool | None = None,
    size: float | None = None,
    font: str | None = None,
    color=None,
    line_spacing: float | None = None,
    line_spacing_pt: float | None = None,
    space_before: float | None = None,
    space_after: float | None = None,
) -> None:
    """Style whole paragraphs of a text box / shape / placeholder (or a storage Obj).

    `paragraphs` are 0-based paragraph numbers (default: all). `align` is
    'left' / 'center' / 'right' / 'justify'; `font` a PostScript font name
    ('HelveticaNeue-Bold'); `line_spacing` a multiple (1.2) or `line_spacing_pt`
    an exact line height in points (steadier than a multiple when Japanese and
    Latin text share a paragraph); spaces in points.
    """
    storage = target.storage if isinstance(target, Drawable) else target
    if storage is None:
        raise KeynoteError("target has no text")
    if bold is None and font and _HEAVY_FACE.search(font):
        bold = True  # Keynote falls back to the family's regular face unless the bold flag agrees with the name
    if align is not None and align not in _ALIGN:
        raise KeynoteError(f"align must be one of {sorted(_ALIGN)}")
    entries = self._explicit_para_styles(storage)
    made: dict[int, Obj] = {}
    for i, e in enumerate(entries):
        if paragraphs is not None and i not in paragraphs:
            continue
        base_id = int(e.object.identifier)
        if base_id not in made:
            var = self.make_style_variation(self.get(base_id))
            m = var.msg
            cp, pp = m.char_properties, m.para_properties
            if align is not None:
                pp.alignment = _ALIGN[align]
            if line_spacing is not None:
                pp.line_spacing.mode = 0  # relative
                pp.line_spacing.amount = float(line_spacing)
            if line_spacing_pt is not None:
                pp.line_spacing.mode = 2  # exact
                pp.line_spacing.amount = float(line_spacing_pt)
            if space_before is not None:
                pp.space_before = float(space_before)
            if space_after is not None:
                pp.space_after = float(space_after)
            if bold is not None:
                cp.bold = bool(bold)
            if italic is not None:
                cp.italic = bool(italic)
            if size is not None:
                cp.font_size = float(size)
            if font is not None:
                cp.font_name = font
            if color is not None:
                _set_text_color(cp, color)
            m.override_count = max(m.override_count, 1)
            made[base_id] = var
            self._retarget(storage, None, var)
        e.object.identifier = made[base_id].id


def style_text(self, target: "Drawable | Obj", find: str, bold=None, italic=None, color=None, size=None, font=None, underline=None, occurrence: int = 0) -> int:
    """Style the characters matching literal `find` inside one text (a run, not whole paragraphs).

    occurrence=0 styles every match, 1 the first, 2 the second... Returns matches styled.
    """
    storage = target.storage if isinstance(target, Drawable) else target
    if storage is None:
        raise KeynoteError("target has no text")
    msg = storage.msg
    text = msg.text[0] if len(msg.text) else ""
    spans = []
    pos = text.find(find)
    while pos != -1 and find:
        a = u16len(text[:pos])
        spans.append((a, a + u16len(find)))
        pos = text.find(find, pos + len(find))
    if occurrence:
        spans = spans[occurrence - 1 : occurrence]
    if not spans:
        return 0
    # Keynote hangs local character formatting off the theme's empty "None" style
    styles = self.find("TSWP.CharacterStyleArchive")
    model = next((o for o in styles if o.msg.super.style_identifier == "character-style-null"), None)
    if model is None:
        model = next((o for o in styles if not o.msg.super.is_variation and not o.msg.char_properties.ListFields()), None)
    if model is None:
        raise KeynoteError("document has no empty character style to build on; style the text through the app instead")
    total = u16len(text)
    table = msg.table_char_style
    for a, b in spans:
        # style in effect at each index before the edit
        old = sorted(((e.character_index, int(e.object.identifier)) for e in table.entries))

        def at(i):
            cur = 0
            for ci, ident in old:
                if ci <= i:
                    cur = ident
                else:
                    break
            return cur

        base = self.get(at(a)) or model
        var = self.make_style_variation(base)
        cp = var.msg.char_properties
        if bold is not None:
            cp.bold = bool(bold)
        if italic is not None:
            cp.italic = bool(italic)
        if underline is not None:
            cp.underline = 1 if underline else 0
        if size is not None:
            cp.font_size = float(size)
        if font is not None:
            cp.font_name = font
        if color is not None:
            _set_text_color(cp, color)
        var.msg.override_count = max(var.msg.override_count, 1)
        self._retarget(storage, None, var)
        after = at(b)
        new = [(ci, ident) for ci, ident in old if ci < a or ci > b]
        new.append((a, var.id))
        if b < total:
            new.append((b, after))
        if not any(ci == 0 for ci, _ in new):
            new.append((0, 0))
        del table.entries[:]
        for ci, ident in sorted(new):
            e = table.entries.add()
            e.character_index = ci
            if ident:
                e.object.identifier = ident
    return len(spans)


# ---- charts ----------------------------------------------------------------


def set_chart_data(self, chart: "Drawable | Obj", row_names: list[str], column_names: list[str], rows: list[list[float]]) -> None:
    """Replace a chart's data grid. Each row is one series when the chart plots
    rows as series (what `add chart ... group by chart row` makes); names and
    values may grow or shrink."""
    import uuid

    cobj = chart.obj if isinstance(chart, Drawable) else chart
    ext = None
    for fd, value in cobj.msg.ListFields():
        if fd.is_extension and fd.name == "unity":
            ext = value
    if ext is None:
        raise KeynoteError("not a chart")
    if len(rows) != len(row_names) or any(len(r) != len(column_names) for r in rows):
        raise KeynoteError("rows must be len(row_names) x len(column_names)")
    grid = ext.grid
    del grid.row_name[:]
    grid.row_name.extend(row_names)
    del grid.column_name[:]
    grid.column_name.extend(column_names)
    del grid.grid_row[:]
    for r in rows:
        gr = grid.grid_row.add()
        for v in r:
            cell = gr.value.add()
            if v is not None:
                cell.numeric_value = float(v)
    for id_map, n in ((grid.idMap.row_id_map, len(row_names)), (grid.idMap.column_id_map, len(column_names))):
        keep = sorted(id_map, key=lambda e: e.index)[:n]
        entries = [(e.uniqueId, e.index) for e in keep]
        for i in range(len(entries), n):
            entries.append((str(uuid.uuid4()).upper(), i))
        del id_map[:]
        for uid, idx in entries:
            e = id_map.add()
            e.uniqueId, e.index = uid, idx
    ext.contains_default_data = False


def _ext(msg):
    """The message's set Protobuf extension value (TSCH objects keep everything in one).
    Message-valued extensions win over stray scalar ones."""
    scalar = None
    for fd, value in msg.ListFields():
        if fd.is_extension:
            if fd.message_type is not None:
                return value
            if scalar is None:
                scalar = value
    return scalar


def _ext_or_new(msg):
    """The message's (message-valued) extension, creating it if unset."""
    for fd, value in msg.ListFields():
        if fd.is_extension and fd.message_type is not None:
            return value
    exts = [fd for fd in msg.DESCRIPTOR.file.pool.FindAllExtensions(msg.DESCRIPTOR) if fd.message_type is not None]
    if not exts:
        raise KeynoteError(f"{type_name(msg)} has no settings extension")
    ext_fd = next((fd for fd in exts if fd.name == "current"), exts[0])
    v = msg.Extensions[ext_fd]
    v.SetInParent()
    return v


def set_chart_options(
    self,
    chart: "Drawable | Obj",
    legend: bool | None = None,
    title: str | None = None,
    colors: list | None = None,
    y_min: float | None = None,
    y_max: float | None = None,
    thousands: bool | None = None,
    decimals: int | None = None,
    font: str | None = None,
    text_color=None,
    text_scale: float | None = None,
    value_labels: bool | str | None = None,
    value_label_scale: float | None = None,
    value_label_color=None,
    value_label_decimals: int | None = None,
    chart_type: str | None = None,
    bar_colors: list | None = None,
    y_steps: int | None = None,
    value_axis: bool | None = None,
    gridlines: bool | None = None,
) -> None:
    """Show/hide a chart's legend, set (or with '' hide) its title, give series
    their own colors ('#RRGGBB' per series, in order; None keeps one), pin the
    value axis (`y_min` / `y_max`; Keynote otherwise picks a range that may not
    start at 0), format the value-axis numbers (`thousands` separators,
    `decimals`), and restyle all of the chart's text (`font` PostScript name,
    `text_color`, `text_scale` multiplying every label size). `value_labels`
    prints each value on its bar / point / wedge: True, or for bar and column
    charts 'outside' (above the bar, dark text), 'inside' (top of the bar) or
    'middle'; `value_label_scale` / `value_label_color` size and colour just
    those labels. `value_axis=False` hides the value axis numbers and
    `gridlines=False` its gridlines (usual when values are printed on bars)."""
    cobj = chart.obj if isinstance(chart, Drawable) else chart
    unity = _ext(cobj.msg)
    if unity is None:
        raise KeynoteError("not a chart")
    if chart_type is not None:
        want = _CHART_TYPES.get(chart_type, chart_type)
        enum = unity.DESCRIPTOR.fields_by_name["chart_type"].enum_type
        if want not in enum.values_by_name:
            raise KeynoteError(f"unknown chart_type {chart_type!r}: use one of {', '.join(sorted(_CHART_TYPES))}")
        # same data, fonts, frame and legend - only the drawing changes. Kept within the families
        # that share a data layout (category charts); pie / scatter / bubble need a new chart.
        now = enum.values_by_number[unity.chart_type].name
        if any(k in n for n in (now, want) for k in ("pie", "donut", "scatter", "bubble", "mixed", "twoAxis", "multiData", "radar", "3D")):
            raise KeynoteError(f"cannot turn {now} into {want} in place: make a new chart with `add chart` and delete the old one")
        unity.chart_type = enum.values_by_name[want].number
    if bar_colors is not None:
        # Keynote colours a series, not a bar. A one-series bar chart with differently coloured bars
        # is drawn as a stacked chart with one series per colour, each bar living in one of them.
        data = self.chart_series(chart)
        enum = unity.DESCRIPTOR.fields_by_name["chart_type"].enum_type
        now = enum.values_by_number[unity.chart_type].name
        stacked = {"columnChartType2D": "stackedColumnChartType2D", "barChartType2D": "stackedBarChartType2D"}
        if now not in stacked and now not in stacked.values():
            raise KeynoteError(f"bar_colors needs a column or bar chart, this is {now}")
        cats = data["categories"]
        totals = [None] * len(cats)
        for se in data["series"]:  # re-applying on an already split chart: merge the series back first
            for i, v in enumerate(se["values"]):
                if v is not None:
                    totals[i] = (totals[i] or 0) + v
        if len(bar_colors) != len(cats):
            raise KeynoteError(f"bar_colors needs one colour per category ({len(cats)}), got {len(bar_colors)}")
        if now in stacked and len(data["series"]) != 1:
            raise KeynoteError("bar_colors works on a chart with a single series")
        order = []
        for c in bar_colors:
            if c not in order:
                order.append(c)
        name = data["series"][0]["name"]
        self.set_chart_series(chart, cats, [
            {"name": name if k == 0 else f"{name} ({k + 1})", "values": [v if c == col else None for v, c in zip(totals, bar_colors)]}
            for k, col in enumerate(order)])
        unity.chart_type = enum.values_by_name[stacked.get(now, now)].number
        colors, legend = order, False
        ns = self.get(unity.chart_non_style.identifier)
        info = _ext_or_new(ns.msg)
        if value_labels is not None:
            # the number above a stacked bar is its "summary label"; per-series labels would sit inside
            info.tschchartinfodefaultsummarylabelshowlabels = bool(value_labels)
            f = info.tschchartinfodefaultsummarylabelnumberformat
            f.format_type = 256
            places = value_label_decimals if value_label_decimals is not None else decimals
            f.decimal_places = int(places) if places is not None else 253
            f.negative_style = 0
            f.show_thousands_separator = bool(thousands)
            info.tschchartinfodefaultsummarylabelnumberformattype = 1
            value_labels = None
            cs = _ext(self.get(unity.chart_style.identifier).msg)
            k = int(cs.tschchartinfodefaultsummarylabelparagraphstyleindex) if cs is not None and cs.HasField("tschchartinfodefaultsummarylabelparagraphstyleindex") else None
            if k is not None and k < len(unity.paragraph_styles):
                # that style is the chart title's too (40 pt): give the labels their own, at axis-label size
                ref = unity.paragraph_styles[k]
                base = self.get(ref.identifier)
                axis_sizes = []
                for aref in list(unity.value_axis_styles):
                    ap = _ext(self.get(aref.identifier).msg)
                    for fd in ap.DESCRIPTOR.fields:
                        if fd.name.endswith("labelsparagraphstyleindex") and ap.HasField(fd.name) and int(getattr(ap, fd.name)) < len(unity.paragraph_styles):
                            sz = self._resolve(self.get(unity.paragraph_styles[int(getattr(ap, fd.name))].identifier), ("char_properties", "font_size"))
                            if sz:
                                axis_sizes.append(float(sz))
                var = self.make_style_variation(base)
                size = (axis_sizes[0] if axis_sizes else float(self._resolve(base, ("char_properties", "font_size")) or 24) * 0.6)
                var.msg.char_properties.font_size = round(size * float(value_label_scale or 1), 1)
                if value_label_color is not None:
                    _set_text_color(var.msg.char_properties, value_label_color)
                var.msg.override_count = max(var.msg.override_count, 1)
                ref.identifier = var.id
                self._retarget(cobj, base.id, var)
                value_label_scale = value_label_color = None
    if y_steps is not None:
        for ref in unity.value_axis_nonstyles:
            _ext_or_new(self.get(ref.identifier).msg).tschchartaxisvaluenumberofmajorgridlines = int(y_steps)
    if y_min is not None or y_max is not None or thousands is not None or decimals is not None:
        if not unity.value_axis_nonstyles:
            raise KeynoteError("chart has no value axis")
        for ref in unity.value_axis_nonstyles:
            ax = self.get(ref.identifier)
            props = _ext_or_new(ax.msg)
            if y_min is not None:
                props.tschchartaxisdefaultusermin.number_archive = float(y_min)
            if y_max is not None:
                props.tschchartaxisdefaultusermax.number_archive = float(y_max)
            if thousands is not None or decimals is not None:
                f = props.tschchartaxisdefaultnumberformat
                f.format_type = 256
                if decimals is not None:
                    f.decimal_places = int(decimals)
                elif not f.HasField("decimal_places"):
                    f.decimal_places = 253  # automatic
                f.negative_style = 0
                f.show_thousands_separator = bool(thousands) if thousands is not None else f.show_thousands_separator
                props.tschchartaxisdefaultnumberformattype = 1
    if value_labels is not None:
        data = self.chart_series(chart)
        existing = {int(e.index): e for e in unity.series_non_styles.entries}
        for i in range(len(data["series"]) if data else 0):
            if i in existing:
                ns = self.get(existing[i].reference.identifier)
            else:
                from google.protobuf import message_factory

                cls = message_factory.GetMessageClass(unity.DESCRIPTOR.file.pool.FindMessageTypeByName("TSCH.ChartSeriesNonStyleArchive"))
                m = cls()
                m.super.SetInParent()
                ns = self.new_object(m, cobj)
                e = unity.series_non_styles.entries.add()
                e.index = i
                e.reference.identifier = ns.id
                self._retarget(cobj, None, ns)
            props = _ext_or_new(ns.msg)
            for fd in props.DESCRIPTOR.fields:
                if fd.name.endswith("showvaluelabels"):
                    setattr(props, fd.name, bool(value_labels))
        if unity.series_non_styles.entries:  # Keynote ignores entries past `count`
            unity.series_non_styles.count = max(int(unity.series_non_styles.count), max(int(e.index) for e in unity.series_non_styles.entries) + 1)
        if isinstance(value_labels, str):
            where = {"middle": 1, "inside": 5, "outside": 6}.get(value_labels)
            if where is None:
                raise KeynoteError("value_labels must be true, false, 'outside', 'inside' or 'middle'")
            for st in self._own_series_styles(cobj, unity, len(data["series"]) if data else 0):
                props = _ext_or_new(st.msg)
                props.tschchartseriesbarvaluelabelposition = where
                props.tschchartseriesstackedbarvaluelabelposition = where
                props.tschchartseriesmixedcolumnvaluelabelposition = where
    if value_label_decimals is not None or (value_labels and (decimals is not None or thousands is not None)):
        places = value_label_decimals if value_label_decimals is not None else decimals
        for e in unity.series_non_styles.entries:
            props = _ext_or_new(self.get(e.reference.identifier).msg)
            f = props.tschchartseriesdefaultnumberformat
            f.format_type = 256
            if places is not None:
                f.decimal_places = int(places)
            elif not f.HasField("decimal_places"):
                f.decimal_places = 253
            f.negative_style = 0
            if thousands is not None:
                f.show_thousands_separator = bool(thousands)
            props.tschchartseriesdefaultnumberformattype = 1
    if value_label_scale is not None or value_label_color is not None:
        idx = set()
        for ref in list(unity.series_theme_styles) + [e.reference for e in unity.series_private_styles.entries]:
            st = self.get(ref.identifier)
            props = _ext(st.msg) if st is not None else None
            if props is None:
                continue
            for fd in props.DESCRIPTOR.fields:
                if fd.name.endswith("labelparagraphstyleindex") and "trendline" not in fd.name and props.HasField(fd.name):
                    idx.add(int(getattr(props, fd.name)))
        axis_idx = set()
        for ref in list(unity.value_axis_styles) + list(unity.category_axis_styles):
            props = _ext(self.get(ref.identifier).msg)
            if props is not None and hasattr(props, "DESCRIPTOR"):
                for fd in props.DESCRIPTOR.fields:
                    if fd.name.endswith("paragraphstyleindex") and props.HasField(fd.name):
                        axis_idx.add(int(getattr(props, fd.name)))
        for i in sorted(idx - axis_idx):  # never touch a style the axes share
            if i >= len(unity.paragraph_styles):
                continue
            ref = unity.paragraph_styles[i]
            base = self.get(ref.identifier)
            var = self.make_style_variation(base)
            cp = var.msg.char_properties
            if value_label_color is not None:
                _set_text_color(cp, value_label_color)
            if value_label_scale is not None:
                size = self._resolve(base, ("char_properties", "font_size"))
                if size:
                    cp.font_size = round(float(size) * float(value_label_scale), 1)
            var.msg.override_count = max(var.msg.override_count, 1)
            ref.identifier = var.id
            self._retarget(cobj, base.id, var)
    if value_axis is not None:
        for ref in unity.value_axis_nonstyles:
            _ext_or_new(self.get(ref.identifier).msg).tschchartaxisvalueshowlabels = bool(value_axis)
    if gridlines is not None:
        for i, ref in enumerate(unity.value_axis_styles):
            base = self.get(ref.identifier)
            var = self.make_style_variation(base)
            _ext_or_new(var.msg).tschchartaxisvalueshowmajorgridlines = bool(gridlines)
            ref.identifier = var.id
            self._retarget(cobj, base.id, var)
    if font is not None or text_color is not None or text_scale is not None:
        # paragraph styles are shared by every chart of the theme: give this chart its own
        for ref in unity.paragraph_styles:
            base = self.get(ref.identifier)
            if base is None:
                continue
            var = self.make_style_variation(base)
            cp = var.msg.char_properties
            if font is not None:
                cp.font_name = font
            if text_color is not None:
                _set_text_color(cp, text_color)
            if text_scale is not None:
                size = self._resolve(base, ("char_properties", "font_size"))
                if size:
                    cp.font_size = round(float(size) * float(text_scale), 1)
            var.msg.override_count = max(var.msg.override_count, 1)
            ref.identifier = var.id
            self._retarget(cobj, base.id, var)
    if legend is not None or title is not None:
        ns = self.get(unity.chart_non_style.identifier)
        if ns is None:
            raise KeynoteError("chart has no settings object")
        props = _ext(ns.msg)
        if props is None:
            ext_fd = next(iter(ns.msg.DESCRIPTOR.file.pool.FindAllExtensions(ns.msg.DESCRIPTOR)))
            props = ns.msg.Extensions[ext_fd]
        if legend is not None:
            props.tschchartinfodefaultshowlegend = bool(legend)
            # Keynote parks a new legend flush on the plot's top edge, where it touches the top
            # gridline and tall bars: lift it clear (legend_frame is relative to the chart's centre)
            g = chart.geometry if isinstance(chart, Drawable) else None
            if legend and g is not None and unity.HasField("legend_frame"):
                lf = unity.legend_frame
                plot_top = -g.size.height / 2
                if abs((lf.origin.y + lf.size.height) - plot_top) < 6:
                    lf.origin.y -= max(16.0, 0.45 * lf.size.height)
        if title is not None:
            props.tschchartinfodefaultshowtitle = bool(title)
            if title:
                props.tschchartinfodefaulttitle = title
    if colors:
        themes = [r.identifier for r in unity.series_theme_styles]
        if not themes:
            raise KeynoteError("chart has no series styles to recolor")
        existing = {int(e.index): e for e in unity.series_private_styles.entries}
        for i, spec in enumerate(colors):
            if spec is None:
                continue
            if i in existing:
                base = self.get(existing[i].reference.identifier)
            else:
                base = self.get(themes[i % len(themes)])
            var = self.make_style_variation(base)
            src = _ext(base.msg)
            ext_fd = next(fd for fd, _ in base.msg.ListFields() if fd.is_extension)
            props = var.msg.Extensions[ext_fd]
            for fd in props.DESCRIPTOR.fields:
                n = fd.name
                if fd.message_type is None or "errorbar" in n or "trendline" in n or "calloutline" in n:
                    continue
                if fd.message_type.full_name == "TSD.FillArchive" and n.endswith("fill"):
                    f = getattr(props, n)
                    f.Clear()
                    _set_color(f.color, spec)
                elif fd.message_type.full_name == "TSD.StrokeArchive" and n.endswith("stroke"):
                    # line / scatter strokes and their point markers' outlines (a marker left in the
                    # theme colour on a recoloured line looks like a bug)
                    if any(k in n for k in ("line", "scatter", "radar", "bubble")):
                        st = getattr(props, n)
                        if src is not None and src.HasField(n):
                            st.CopyFrom(getattr(src, n))
                        elif "symbol" in n:
                            continue
                        _set_color(st.color, spec)
            if i in existing:
                existing[i].reference.identifier = var.id
            else:
                e = unity.series_private_styles.entries.add()
                e.index = i
                e.reference.identifier = var.id
            self._retarget(cobj, None, var)
        entries = sorted(unity.series_private_styles.entries, key=lambda e: e.index)
        kept = [(int(e.index), int(e.reference.identifier)) for e in entries]
        del unity.series_private_styles.entries[:]
        for idx, ref in kept:
            e = unity.series_private_styles.entries.add()
            e.index = idx
            e.reference.identifier = ref
        unity.series_private_styles.count = max(int(unity.series_private_styles.count), max(k for k, _ in kept) + 1)


_CHART_TYPES = {
    "column": "columnChartType2D", "bar": "barChartType2D", "line": "lineChartType2D", "area": "areaChartType2D",
    "stacked_column": "stackedColumnChartType2D", "stacked_bar": "stackedBarChartType2D", "stacked_area": "stackedAreaChartType2D",
}


def _own_series_styles(self, cobj: Obj, unity, n: int) -> list:
    """A private style (variation of the theme's) for each of the chart's first n series."""
    themes = [r.identifier for r in unity.series_theme_styles]
    if not themes:
        raise KeynoteError("chart has no series styles")
    existing = {int(e.index): e for e in unity.series_private_styles.entries}
    out = []
    for i in range(n):
        if i in existing:
            out.append(self.get(existing[i].reference.identifier))
            continue
        var = self.make_style_variation(self.get(themes[i % len(themes)]))
        e = unity.series_private_styles.entries.add()
        e.index = i
        e.reference.identifier = var.id
        self._retarget(cobj, None, var)
        out.append(var)
    kept = sorted((int(e.index), int(e.reference.identifier)) for e in unity.series_private_styles.entries)
    del unity.series_private_styles.entries[:]
    for idx, ref in kept:
        e = unity.series_private_styles.entries.add()
        e.index = idx
        e.reference.identifier = ref
    unity.series_private_styles.count = max(int(unity.series_private_styles.count), kept[-1][0] + 1 if kept else 0)
    return out


def chart_series(self, chart: "Drawable | Obj") -> dict:
    """{'type', 'categories': [...], 'series': [{'name', 'values'}]} whichever way the grid is oriented."""
    data = self.chart_data(chart)
    if not data:
        return {}
    cobj = chart.obj if isinstance(chart, Drawable) else chart
    by_column = any(fd.is_extension and v.series_direction == 2 for fd, v in cobj.msg.ListFields())
    if by_column:
        series = [{"name": n, "values": [r[i] for r in data["rows"]]} for i, n in enumerate(data["column_names"])]
        return {"type": data["type"], "categories": data["row_names"], "series": series}
    series = [{"name": n, "values": list(r)} for n, r in zip(data["row_names"], data["rows"])]
    return {"type": data["type"], "categories": data["column_names"], "series": series}


def set_chart_series(self, chart: "Drawable | Obj", categories: list[str], series: list[dict]) -> None:
    """Replace a chart's data: `categories` are the x-axis labels (pie: the wedges come
    from the first series), `series` is [{'name': str, 'values': [numbers]}]."""
    cobj = chart.obj if isinstance(chart, Drawable) else chart
    for sr in series:
        if len(sr["values"]) != len(categories):
            raise KeynoteError(f"series {sr.get('name')!r}: {len(sr['values'])} values for {len(categories)} categories")
    names = [str(sr.get("name", f"Series {i}")) for i, sr in enumerate(series, 1)]
    by_column = any(fd.is_extension and v.series_direction == 2 for fd, v in cobj.msg.ListFields())
    if by_column:
        rows = [[sr["values"][i] for sr in series] for i in range(len(categories))]
        self.set_chart_data(chart, [str(c) for c in categories], names, rows)
    else:
        self.set_chart_data(chart, names, [str(c) for c in categories], [list(sr["values"]) for sr in series])


def item(self, slide: "Slide | int", n: int) -> Drawable:
    """The n-th (1-based) top-level object on a slide, back to front - the same
    numbering as `key_text.py --json` ("item") and AppleScript's `iWork item n`."""
    s = self.slide(slide) if isinstance(slide, int) else slide
    ds = s.drawables
    if not 1 <= n <= len(ds):
        raise KeynoteError(f"slide {s.index} has {len(ds)} items; no item {n}")
    return ds[n - 1]


def find_drawables(self, text: str | None = None, kind: str | None = None, slides: list[int] | None = None) -> list[tuple["Slide", Drawable]]:
    """(slide, drawable) pairs whose text contains `text` and/or whose kind matches."""
    out = []
    for s in self.slides():
        if slides is not None and s.index not in slides:
            continue
        for d in s.walk():
            if kind is not None and d.kind != kind:
                continue
            if text is not None and text not in d.text:
                continue
            out.append((s, d))
    return out


def fonts_used(self) -> list[tuple[str, int]]:
    """Font names set in the deck's text styles, most used first: [(name, number of styles)]."""
    count: dict[str, int] = {}
    for o in self.objects.values():
        m = o.msg
        if _has(m, "char_properties") and m.char_properties.HasField("font_name"):
            count[m.char_properties.font_name] = count.get(m.char_properties.font_name, 0) + 1
    return sorted(count.items(), key=lambda kv: -kv[1])


def replace_fonts(self, mapping: dict[str, str]) -> int:
    """Re-point every text style that names font A to font B ({'Helvetica': 'YuGo-Medium', ...}),
    across the whole deck, layouts included. Bold / italic flags are left alone, so map the
    bold face too ('Helvetica-Bold': 'YuGo-Bold'). Returns the number of styles changed."""
    n = 0
    for o in self.objects.values():
        m = o.msg
        if _has(m, "char_properties") and m.char_properties.HasField("font_name"):
            new = mapping.get(m.char_properties.font_name)
            if new:
                m.char_properties.font_name = new
                n += 1
    return n


def override_runs(self, target: "Drawable | Obj", font=None, size=None, color=None, bold=None, italic=None) -> int:
    """Make character-level runs (a coloured word, a bigger phrase) follow the given values too.

    style_paragraphs() sets the paragraph style; a run that carries its own
    font / size / colour / weight still wins over it. This rewrites exactly
    those properties on such runs - only the ones a run actually overrides.
    Returns the number of runs changed.
    """
    storage = target.storage if isinstance(target, Drawable) else target
    if storage is None:
        return 0
    if bold is None and font and _HEAVY_FACE.search(font):
        bold = True
    n = 0
    made: dict[int, Obj] = {}
    for e in storage.msg.table_char_style.entries:
        ident = int(e.object.identifier)
        st = self.get(ident) if ident else None
        if st is None or not _has(st.msg, "char_properties"):
            continue
        cp = st.msg.char_properties
        todo = []
        if font is not None and cp.HasField("font_name") and cp.font_name != font:
            todo.append("font")
        if size is not None and cp.HasField("font_size") and cp.font_size != size:
            todo.append("size")
        if color is not None and (cp.HasField("font_color") or cp.HasField("tsd_fill")):
            todo.append("color")
        if bold is not None and cp.HasField("bold") and cp.bold != bool(bold):
            todo.append("bold")
        if italic is not None and cp.HasField("italic") and cp.italic != bool(italic):
            todo.append("italic")
        if not todo:
            continue
        if ident not in made:
            var = self.make_style_variation(st)
            vp = var.msg.char_properties
            if "font" in todo:
                vp.font_name = font
            if "size" in todo:
                vp.font_size = float(size)
            if "color" in todo:
                _set_text_color(vp, color)
            if "bold" in todo:
                vp.bold = bool(bold)
            if "italic" in todo:
                vp.italic = bool(italic)
            var.msg.override_count = max(var.msg.override_count, 1)
            made[ident] = var
            self._retarget(storage, None, var)
        e.object.identifier = made[ident].id
        n += 1
    return n


def exact_line_spacing(self, factor: float = 1.2, slides: list[int] | None = None) -> int:
    """Turn every relative line spacing into an exact one of
    (largest font size in the paragraph) x `factor` x (the multiple).

    PowerPoint sets a "1.0" line at about 1.2 x the font size whatever the
    font; Keynote uses the font's own metrics, which for Japanese fonts are
    much taller - so a deck imported from PowerPoint grows ~20% in every text
    block and overflows. Returns the number of paragraphs changed.
    """
    count = 0
    for s in self.slides():
        if slides and s.index not in slides:
            continue
        for d in s.walk():
            st = d.storage
            if st is None or not d.text.strip():
                continue
            msg = st.msg
            ranges = paragraph_ranges(msg)
            pentries = sorted(msg.table_para_style.entries, key=lambda e: e.character_index)
            centries = sorted(msg.table_char_style.entries, key=lambda e: e.character_index)
            plan = []
            for i, (a, b) in enumerate(ranges):
                pid = 0
                for e in pentries:
                    if e.character_index <= a and e.object.identifier:
                        pid = int(e.object.identifier)
                pst = self.get(pid) if pid else None
                ls = self._resolve(pst, ("para_properties", "line_spacing"))
                if ls is not None and ls.HasField("mode") and int(ls.mode) != 0:
                    continue  # already exact / at-least / between
                amount = ls.amount if ls is not None and ls.HasField("amount") else 1.0
                base = self._resolve(pst, ("char_properties", "font_size")) or 0
                sizes = []
                for j, e in enumerate(centries):
                    start = e.character_index
                    end = centries[j + 1].character_index if j + 1 < len(centries) else 10**9
                    if end <= a or start >= max(b, a + 1):
                        continue
                    cs = self._resolve(self.get(int(e.object.identifier)), ("char_properties", "font_size")) if e.object.identifier else None
                    sizes.append(cs or base)
                size = max(sizes) if sizes and all(sizes) else max([base] + sizes)
                if size:
                    plan.append((i, round(size * factor * amount, 2)))
            for i, pt in plan:
                self.style_paragraphs(st, paragraphs=[i], line_spacing_pt=pt)
                count += 1
    return count


def set_text(self, target: "Drawable | Obj", text: str) -> None:
    """Replace all the text of a text box / shape / placeholder (or storage Obj).
    The first paragraph's style is kept for the whole text; '\\n' starts a new paragraph."""
    storage = target.storage if isinstance(target, Drawable) else target
    if storage is None:
        raise KeynoteError("target has no text")
    storage_set_text(storage.msg, text)


def set_notes(self, slide: "Slide", text: str) -> None:
    st = slide.notes_storage
    if st is None:
        raise KeynoteError(f"slide {slide.index} has no notes storage; set notes through the app instead")
    storage_set_text(st.msg, text)
    slide.node.msg.hasNote = bool(text)


def set_table_padding(self, table: "Drawable | Obj", padding) -> int:
    """Inner margin of every cell of a table, in points: one number, or (horizontal, vertical).
    Keynote's default is 4 pt, which on a 1920-wide slide leaves text touching the cell borders.
    Returns the number of cell styles given the padding."""
    h, v = (padding if isinstance(padding, (list, tuple)) else (padding, padding))
    tobj = table.obj if isinstance(table, Drawable) else table
    if tobj.type != "TST.TableInfoArchive":
        raise KeynoteError("not a table")
    mobj = self.get(tobj.msg.tableModel.identifier)
    model = mobj.msg
    done: dict[int, Obj] = {}

    def padded(ref, holder) -> None:
        old = int(ref.identifier)
        base = self.get(old)
        if base is None or base.type != "TST.CellStyleArchive":
            return
        if old not in done:
            var = self.make_style_variation(base)
            pd = var.msg.cell_properties.padding
            pd.left = pd.right = float(h)
            pd.top = pd.bottom = float(v)
            var.msg.override_count = max(var.msg.override_count, 1)
            done[old] = var
        ref.identifier = done[old].id
        self._retarget(holder, old, done[old])

    for name in ("body_cell_style", "header_row_style", "header_column_style", "footer_row_style"):
        if model.HasField(name):
            padded(getattr(model, name), mobj)
    ds = model.base_data_store
    if ds.HasField("styleTable"):  # cells with a fill / border of their own carry their own style
        def walk(o):
            for e in o.msg.entries:
                if e.HasField("reference"):
                    padded(e.reference, o)
            for seg in o.msg.segments:
                so = self.get(seg.identifier)
                if so is not None:
                    walk(so)
        root = self.get(ds.styleTable.identifier)
        if root is not None:
            walk(root)
    return len(done)


def set_bullets(self, d: "Drawable", on: bool = True, char: str | None = None, indent: float | None = None) -> None:
    """Turn the paragraphs of a text box / shape into a real bulleted list (hanging indent,
    levels from `set_paragraph_levels`) - or back to plain text. `char` is the bullet glyph
    (default: the theme's); `indent` the step per level in points (default 1.1 x the text size)."""
    st = d.storage
    if st is None:
        raise KeynoteError("object has no text")
    styles = self.find("TSWP.ListStyleArchive")

    def named(tag, label):
        for o in styles:
            if not o.msg.super.is_variation and tag in o.msg.super.style_identifier:
                return o
        for o in styles:
            if not o.msg.super.is_variation and list(o.msg.label_types)[:1] == [label] and not o.msg.super.style_identifier.startswith("note"):
                return o
        return None

    base = named("liststyle-Bullet", 2) if on else named("liststyle-None", 0)
    if base is None:
        raise KeynoteError("this deck's theme has no " + ("bulleted" if on else "plain") + " list style to build on")
    target = base
    if on:
        size = float(((self.style_info(d).get("text") or {}).get("size")) or 0) or None
        step = float(indent) if indent is not None else (round(size * 1.1, 1) if size else None)
        if step is not None or char is not None:
            target = self.make_style_variation(base)
            m = target.msg
            if step is not None:
                del m.indents[:]
                m.indents.extend(step * k for k in range(max(9, len(base.msg.indents))))
            if char is not None:
                del m.strings[:]
                m.strings.extend([char] * max(9, len(base.msg.strings)))
            m.override_count = max(m.override_count, 1)
    tbl = st.msg.table_list_style
    old = [int(e.object.identifier) for e in tbl.entries]
    del tbl.entries[:]
    e = tbl.entries.add()
    e.character_index = 0
    e.object.identifier = target.id
    for o in old:
        self._retarget(st, o, target)
    if not old:
        self._retarget(st, None, target)


for _f in (
    set_table_padding, set_bullets,
    _metadata, _component_of, new_object, _retarget, make_style_variation, _own_style, _drawable_style_props,
    set_fill, set_stroke, set_shadow, set_text_layout, set_shape, set_background, _slide_of, reorder,
    _explicit_para_styles, style_paragraphs, style_text, set_chart_data, set_chart_options, _own_series_styles, chart_series, set_chart_series, item,
    set_frame, set_line_ends, fonts_used, replace_fonts, override_runs, exact_line_spacing, background_info, show_placeholder, placeholder_visible, _style_chain, _resolve, _para_style_info, style_info,
    find_drawables, set_text, set_notes,
):
    setattr(KeynoteFile, _f.__name__, _f)
