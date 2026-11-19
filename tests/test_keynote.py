"""Tests for the keynote skill scripts.

    pytest tests                    # file-level tests
    KEYNOTE_TESTS=1 pytest tests    # also the tests that drive the installed Keynote app(s)

The sample decks are not part of the repository: the first run has Keynote build
them from tests/fixtures/spec.json (one per installed Keynote generation) and
keeps them in tests/fixtures/ for later runs, which then need no Keynote.
Without Keynote, the tests that need a deck are skipped.
"""

import json
import os
import subprocess
import sys
import zipfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "skills", "keynote", "scripts")
FIX = os.path.join(ROOT, "tests", "fixtures")
sys.path.insert(0, SCRIPTS)

import key_check  # noqa: E402
import keynote_lib  # noqa: E402
from keynote_lib import (  # noqa: E402
    KeynoteError,
    KeynoteFile,
    set_paragraph_levels,
    storage_paragraphs,
    storage_replace_range,
)

SPEC = os.path.join(FIX, "spec.json")
app = pytest.mark.skipif(not os.environ.get("KEYNOTE_TESTS"), reason="set KEYNOTE_TESTS=1 to drive Keynote")


def run(script, *args):
    return subprocess.run([sys.executable, os.path.join(SCRIPTS, script), *args], capture_output=True, text=True)


_built: dict = {}


def sample(kind):
    """Path of the sample deck made by the "new" (Keynote 15+) or "old" (14.x and earlier) app.

    Built from spec.json on first use and kept next to it; skips the test when it
    has to be built and that Keynote is not installed.
    """
    if kind in _built:
        if _built[kind] is None:
            pytest.skip(f"could not build the {kind} sample deck")
        return _built[kind]
    path = os.path.join(FIX, f"sample-{kind}.key")
    if not os.path.exists(path) or os.path.getmtime(path) < os.path.getmtime(SPEC):
        import keynote_app

        bundle = keynote_app.NEW_ID if kind == "new" else keynote_app.OLD_ID
        if not any(a["id"] == bundle for a in keynote_app.installed()):
            pytest.skip(f"no sample deck and no Keynote ({bundle}) to build it with")
        _built[kind] = None
        tmp = path + ".building.key"
        r = run("key_build.py", SPEC, tmp, "--app", bundle)
        assert r.returncode == 0, r.stderr or r.stdout
        os.replace(tmp, path)
    _built[kind] = path
    return path


@pytest.fixture(params=["new", "old"])
def deck(request):
    return sample(request.param)


# ---- reading ---------------------------------------------------------------


def test_slides_and_text(deck):
    doc = KeynoteFile(deck)
    slides = doc.slides()
    assert [s.index for s in slides] == [1, 2, 3, 4]
    assert slides[0].title == "Fixture deck 日本語"
    assert slides[0].notes == "note for slide 1"
    assert slides[1].body_item.text == "alpha\nbeta\ngamma 😀"
    assert [lv for lv, _ in storage_paragraphs(slides[1].body_item.storage.msg)] == [0, 1, 0]
    assert slides[3].skipped and not slides[0].skipped
    assert doc.size == (1920.0, 1080.0)


def test_table_and_chart(deck):
    doc = KeynoteFile(deck)
    table = [d for d in doc.slide(2).drawables if d.kind == "table"][0]
    assert doc.table_cells(table) == [["H1", "H2"], ["text", "42"], ["x", "3.5"]]
    chart = [d for d in doc.slide(3).drawables if d.kind == "chart"][0]
    data = doc.chart_series(chart)
    assert data["categories"] == ["A", "B", "C"]
    assert data["series"] == [{"name": "R1", "values": [1.0, 2.0, 3.0]}, {"name": "R2", "values": [4.0, 5.0, 6.0]}]


def test_key_text_cli(deck):
    r = run("key_text.py", deck)
    assert r.returncode == 0, r.stderr
    assert "<!-- Slide number: 2" in r.stdout and "  - beta" in r.stdout and "| text | 42 |" in r.stdout
    assert "skipped" in r.stdout.split("<!-- Slide number: 4")[1].splitlines()[0]
    j = json.loads(run("key_text.py", deck, "--json", "--slides", "2").stdout)
    items = j["slides"][0]["items"]
    assert [i["item"] for i in items] == list(range(1, len(items) + 1))
    assert {"title", "body", "shape", "table"} <= {i["kind"] for i in items}
    assert run("key_text.py", deck, "--layouts").stdout.count("\n") >= 5


def test_not_a_keynote_file(tmp_path):
    p = tmp_path / "x.key"
    p.write_text("nope")
    with pytest.raises(KeynoteError):
        KeynoteFile(str(p))
    assert run("key_text.py", str(p)).returncode == 1


# ---- saving ----------------------------------------------------------------


def test_untouched_save_is_byte_identical(deck, tmp_path):
    out = str(tmp_path / "out.key")
    doc = KeynoteFile(deck)
    assert doc.changed_files() == []
    doc.save(out)
    a, b = zipfile.ZipFile(deck), zipfile.ZipFile(out)
    assert len(a.infolist()) == len(b.infolist())
    for ia, ib in zip(a.infolist(), b.infolist()):
        assert a.read(ia) == b.read(ib)


def test_refuses_to_overwrite_source(deck):
    with pytest.raises(KeynoteError):
        KeynoteFile(deck).save(deck)


def test_non_ascii_zip_names():
    # Keynote stores UTF-8 names without the zip UTF-8 flag; Python then decodes them as CP437
    from keynote_lib import _zip_info, _zip_name

    name = "Data/ペースト-1.png"
    raw = zipfile.ZipInfo(name.encode("utf-8").decode("cp437"))
    assert raw.flag_bits & 0x800 == 0
    assert _zip_name(raw) == name
    assert _zip_info(raw).filename == name
    flagged = zipfile.ZipInfo(name)
    flagged.flag_bits |= 0x800
    assert _zip_name(flagged) == name


@pytest.mark.parametrize("flag_in", ["local", "central"])
def test_zip_utf8_flag_mismatch(tmp_path, flag_in):
    # Keynote can write an entry whose UTF-8 flag is set in only one of the local
    # header / central directory; zipfile.read() then raises "File name ... differ".
    from keynote_lib import _Package

    name = "Data/ペーストされたムービー-1.png"
    src = tmp_path / "src.zip"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("Index/Document.iwa", b"doc")
        z.writestr(name, b"png")  # Python sets the flag in both places
    raw = bytearray(src.read_bytes())
    if flag_in == "local":  # clear it in the central directory
        for pos in [i for i in range(len(raw)) if raw[i:i + 4] == b"PK\x01\x02"]:
            raw[pos + 9] &= ~0x08
    else:  # clear it in the local header
        for pos in [i for i in range(len(raw)) if raw[i:i + 4] == b"PK\x03\x04"]:
            raw[pos + 7] &= ~0x08
    src.write_bytes(bytes(raw))
    with pytest.raises(zipfile.BadZipFile):
        with zipfile.ZipFile(src) as z:
            [z.read(i) for i in z.infolist()]

    pkg = _Package(str(src))
    assert name in pkg.names()
    assert pkg.read(name) == b"png"
    out = str(tmp_path / "out.zip")
    pkg.write_copy(out, {"Index/Document.iwa": b"new"}, {})
    with zipfile.ZipFile(out) as z:
        assert z.read(name) == b"png" and z.read("Index/Document.iwa") == b"new"


# ---- text edits ------------------------------------------------------------


def test_replace_text(deck, tmp_path):
    out = str(tmp_path / "out.key")
    doc = KeynoteFile(deck)
    assert doc.replace_text("beta", "a much longer BETA") == 1
    assert doc.replace_text("free text box", "two\nparagraphs 😀") == 1
    assert doc.replace_text("H1", "Header") == 1
    assert doc.replace_text("no such text", "x") == 0
    doc.save(out)
    assert key_check.check(out) == []
    doc2 = KeynoteFile(out)
    assert doc2.slide(2).body_item.text == "alpha\na much longer BETA\ngamma 😀"
    assert [lv for lv, _ in storage_paragraphs(doc2.slide(2).body_item.storage.msg)] == [0, 1, 0]
    table = [d for d in doc2.slide(2).drawables if d.kind == "table"][0]
    assert doc2.table_cells(table)[0][0] == "Header"
    assert any(d.text == "two\nparagraphs 😀" for d in doc2.slide(3).drawables)


def test_replace_limited_to_slides(deck):
    doc = KeynoteFile(deck)
    assert doc.replace_text("e", "@", slides=[1]) > 0
    assert doc.slide(2).body_item.text.startswith("alpha")


def test_replace_range_bookkeeping(deck):
    doc = KeynoteFile(deck)
    st = doc.slide(2).body_item.storage.msg
    storage_replace_range(st, 0, 5, "A\nB")  # "alpha" -> two paragraphs
    assert st.text[0] == "A\nB\nbeta\ngamma 😀"
    idx = [e.character_index for e in st.table_para_style.entries]
    assert idx == [0, 2, 4, 9]
    storage_replace_range(st, 1, 2, " ")  # remove a paragraph break
    assert [e.character_index for e in st.table_para_style.entries] == [0, 4, 9]
    storage_replace_range(st, 0, 9, "x")  # swallow the indented paragraph and its break
    assert st.text[0] == "xgamma 😀"
    for table in (st.table_para_style, st.table_para_data, st.table_list_style):
        assert [e.character_index for e in table.entries] == [0]
    with pytest.raises(KeynoteError):
        storage_replace_range(st, 5, 999, "x")


def test_set_text_levels_notes(deck, tmp_path):
    out = str(tmp_path / "out.key")
    doc = KeynoteFile(deck)
    s = doc.slide(2)
    doc.set_text(s.body_item, "one\ntwo\nthree\nfour")
    set_paragraph_levels(s.body_item.storage.msg, [0, 1, 2, 0])
    doc.set_notes(s, "new notes")
    with pytest.raises(KeynoteError):
        set_paragraph_levels(s.body_item.storage.msg, [0, 1])
    doc.save(out)
    assert key_check.check(out) == []
    s2 = KeynoteFile(out).slide(2)
    assert [lv for lv, _ in storage_paragraphs(s2.body_item.storage.msg)] == [0, 1, 2, 0]
    assert s2.notes == "new notes"


# ---- styling ---------------------------------------------------------------


def test_style_ops_keep_file_sound(deck, tmp_path):
    out = str(tmp_path / "out.key")
    doc = KeynoteFile(deck)
    s = doc.slide(2)
    shape = [d for d in s.drawables if d.kind == "shape"][0]
    old_style = shape.obj.msg.super.style.identifier
    doc.set_fill(shape, "#E4572E")
    doc.set_stroke(shape, "#222222", 4)
    doc.set_shape(shape, "rounded", 30)
    doc.set_shadow(shape, True)
    doc.set_text_layout(shape, valign="bottom", padding=10)
    doc.style_paragraphs(shape, align="right", bold=True, size=30, color="#FFFFFF")
    doc.style_paragraphs(s.body_item, [1], italic=True)
    assert doc.style_text(s.body_item, "gam", bold=True, color="#00AA00") == 1
    assert doc.style_text(s.body_item, "zzz", bold=True) == 0
    doc.set_background(s, "#F2F2F2")
    doc.reorder(shape, "back")
    doc.save(out)
    assert key_check.check(out) == []
    doc2 = KeynoteFile(out)
    s2 = doc2.slide(2)
    assert s2.drawables[0].kind == "shape"
    shape2 = s2.drawables[0]
    new_style = doc2.get(shape2.obj.msg.super.style.identifier)
    assert new_style.id != old_style and new_style.msg.super.super.is_variation
    assert round(new_style.msg.super.shape_properties.fill.color.r, 2) == 0.89
    # the theme style other shapes share is untouched
    assert round(doc2.get(old_style).msg.super.shape_properties.fill.color.r, 2) != 0.89
    assert shape2.obj.msg.super.pathsource.HasField("scalar_path_source")


def test_chart_data(deck, tmp_path):
    out = str(tmp_path / "out.key")
    doc = KeynoteFile(deck)
    chart = [d for d in doc.slide(3).drawables if d.kind == "chart"][0]
    series = [{"name": n, "values": v} for n, v in (("x", [1, 2]), ("y", [3, 4]), ("z", [5, 6]))]
    doc.set_chart_series(chart, ["Q1", "Q2"], series)
    with pytest.raises(KeynoteError):
        doc.set_chart_series(chart, ["Q1"], series)
    doc.save(out)
    doc2 = KeynoteFile(out)
    chart2 = [d for d in doc2.slide(3).drawables if d.kind == "chart"][0]
    got = doc2.chart_series(chart2)
    assert got["categories"] == ["Q1", "Q2"] and [s["name"] for s in got["series"]] == ["x", "y", "z"]
    assert key_check.check(out) == []


def test_key_edit_cli(deck, tmp_path):
    out, ops = str(tmp_path / "out.key"), str(tmp_path / "ops.json")
    json.dump(
        [
            {"op": "set_text", "slide": 2, "kind": "title", "text": "Edited"},
            {"op": "style", "slide": 2, "match": "a shape", "fill": "#2C5F2D", "stroke": None, "shape": "oval"},
            {"op": "background", "slide": 1, "color": "#0B3C5D"},
            {"op": "replace", "find": "gamma", "replace": "GAMMA"},
        ],
        open(ops, "w"),
    )
    r = run("key_edit.py", deck, "-o", out, "--ops", ops, "--replace", "subtitle", "SUB")
    assert r.returncode == 0, r.stderr
    doc = KeynoteFile(out)
    assert doc.slide(2).title == "Edited" and "GAMMA" in doc.slide(2).body_item.text
    assert doc.slide(1).body_item.text == "SUB here"
    assert run("key_check.py", out, "--original", deck).returncode == 0
    # a failing op writes nothing
    json.dump([{"op": "set_text", "slide": 2, "match": "nowhere", "text": "x"}], open(ops, "w"))
    out2 = str(tmp_path / "out2.key")
    r = run("key_edit.py", deck, "-o", out2, "--ops", ops)
    assert r.returncode == 1 and not os.path.exists(out2)


def test_key_edit_in_place_keeps_backup(deck, tmp_path):
    import shutil

    p = str(tmp_path / "deck.key")
    shutil.copy(deck, p)
    r = run("key_edit.py", p, "--in-place", "--replace", "alpha", "ALPHA")
    assert r.returncode == 0, r.stderr
    assert os.path.exists(str(tmp_path / "deck.backup.key"))
    assert "ALPHA" in KeynoteFile(p).slide(2).body_item.text
    assert "alpha" in KeynoteFile(str(tmp_path / "deck.backup.key")).slide(2).body_item.text


def test_check_catches_damage(deck, tmp_path):
    out = str(tmp_path / "bad.key")
    doc = KeynoteFile(deck)
    st = doc.slide(2).body_item.storage.msg
    st.table_para_style.entries[-1].character_index = 999
    doc.first("KN.ShowArchive").msg.slideTree.slides.add().identifier = 123456789
    doc.save(out)
    problems = key_check.check(out)
    assert any("past the end" in p for p in problems)
    assert any("not a slide node" in p for p in problems)
    assert run("key_check.py", out).returncode == 1
    assert run("key_check.py", deck).returncode == 0


# ---- with the Keynote app ---------------------------------------------------


@app
def test_build_edit_render_roundtrip(tmp_path):
    out = str(tmp_path / "built.key")
    r = run("key_build.py", SPEC, out)
    assert r.returncode == 0, r.stderr
    assert len(KeynoteFile(out).slides()) == 4
    edited = str(tmp_path / "edited.key")
    assert run("key_edit.py", out, "-o", edited, "--replace", "alpha", "ALPHA").returncode == 0
    r = run("key_check.py", edited, "--open")
    assert r.returncode == 0, r.stdout
    r = run("key_render.py", edited, str(tmp_path / "img"), "--jpeg")
    assert r.returncode == 0, r.stderr
    names = sorted(os.listdir(tmp_path / "img"))
    assert names == ["slide-001.jpg", "slide-002.jpg", "slide-003.jpg"]  # slide 4 is skipped


@app
def test_older_keynote_keeps_format(tmp_path):
    import keynote_app

    if len(keynote_app.installed()) < 2:
        pytest.skip("needs two Keynote versions")
    old = sample("old")
    spec = {"template": old, "slides": [{"layout": "title", "title": "T"}]}
    p, out = str(tmp_path / "s.json"), str(tmp_path / "o.key")
    json.dump(spec, open(p, "w"))
    assert run("key_build.py", p, out).returncode == 0
    assert KeynoteFile(out).format_version == KeynoteFile(old).format_version


# ---- frames, charts, merging, bullets ---------------------------------------


def test_frame_shape_arrow_and_style_info(deck, tmp_path):
    doc = KeynoteFile(deck)
    shape = doc.item(doc.slide(2), 3)
    doc.set_frame(shape, x=40, y=50, w=300, h=120)
    doc.set_shape(shape, "arrow")
    doc.set_fill(shape, "#F96167")
    doc.set_text_layout(shape, shrink=False)
    title = doc.slide(2).title_item
    doc.style_paragraphs(title, font="HiraginoSans-W6", size=44, color="#112233", line_spacing_pt=50)
    out = str(tmp_path / "out.key")
    doc.save(out)
    assert key_check.check(out) == []
    doc2 = KeynoteFile(out)
    shape = doc2.item(doc2.slide(2), 3)
    assert shape.frame == (40, 50, 300, 120)
    info = doc2.style_info(shape)
    assert info["fill"] == "#F96167" and info["shrink_to_fit"] is False
    t = doc2.style_info(doc2.slide(2).title_item)["text"]
    # a heavy face name switches bold on, or Keynote would draw the regular face
    assert t["font"] == "HiraginoSans-W6" and t["bold"] is True and t["size"] == 44 and t["color"] == "#112233"
    assert t["line_spacing_pt"] == 50
    with pytest.raises(KeynoteError):
        doc2.set_frame(doc2.item(doc2.slide(2), 4), w=10)  # tables cannot be resized in the file


def test_chart_axis_and_text_options(deck, tmp_path):
    doc = KeynoteFile(deck)
    chart = doc.item(doc.slide(3), 1)
    doc.set_chart_options(chart, y_min=0, y_max=100, thousands=True, decimals=0, font="Helvetica", text_color="#333333", text_scale=1.2)
    out = str(tmp_path / "out.key")
    doc.save(out)
    assert key_check.check(out) == []


def test_chart_type_and_bar_colors(deck, tmp_path):
    doc = KeynoteFile(deck)
    chart = doc.item(doc.slide(3), 1)
    doc.set_chart_options(chart, chart_type="line", y_steps=4)
    assert doc.chart_series(chart)["type"] == "lineChartType2D"
    with pytest.raises(KeynoteError):
        doc.set_chart_options(chart, chart_type="pie")  # another data layout: needs a new chart
    doc.set_chart_options(chart, chart_type="column")
    doc.set_chart_series(chart, ["A", "B", "C"], [{"name": "n", "values": [3, 1, 2]}])
    doc.set_chart_options(chart, bar_colors=["#111111", "#EE7700", "#111111"], value_labels="outside", thousands=True)
    data = doc.chart_series(chart)
    assert data["type"] == "stackedColumnChartType2D"
    assert [s["values"] for s in data["series"]] == [[3.0, None, 2.0], [None, 1.0, None]]
    doc.set_chart_options(chart, bar_colors=["#111111", "#111111", "#EE7700"])  # re-applying merges the split series first
    assert [s["values"] for s in doc.chart_series(chart)["series"]] == [[3.0, 1.0, None], [None, None, 2.0]]
    out = str(tmp_path / "out.key")
    doc.save(out)
    assert key_check.check(out) == []


@app
def test_merge_brings_slides_from_another_deck(tmp_path):
    src = sample("old")
    before = open(src, "rb").read()
    out = str(tmp_path / "merged.key")
    n = len(KeynoteFile(src).slides())
    r = run("key_merge.py", src, "-o", out, "--take", f"{src}:2-3", "--after", "1")
    assert r.returncode == 0, r.stderr
    doc = KeynoteFile(out)
    assert len(doc.slides()) == n + 2
    assert doc.slide(2).title == KeynoteFile(src).slide(2).title
    assert open(src, "rb").read() == before  # slides are moved out of a copy, never out of the source
    assert key_check.check(out) == []


def test_bullets_and_table_padding(deck, tmp_path):
    out = str(tmp_path / "out.key")
    ops = tmp_path / "ops.json"
    ops.write_text(json.dumps([
        {"op": "style", "slide": 2, "kind": "shape", "bullets": True, "size": 30},
        {"op": "style", "slide": 2, "kind": "table", "padding": [16, 6]},
    ]))
    r = run("key_edit.py", deck, "-o", out, "--ops", str(ops))
    assert r.returncode == 0, r.stderr
    assert key_check.check(out) == []
    doc = KeynoteFile(out)
    shape = next(d for d in doc.slide(2).walk() if d.kind == "shape")
    ls = doc.get(shape.storage.msg.table_list_style.entries[0].object.identifier).msg
    assert ls.super.is_variation and list(ls.indents)[:2] == [0.0, 33.0]  # hanging indent follows the new size
    assert list(doc.get(ls.super.parent.identifier).msg.label_types)[0] == 2  # a bulleted list style
    table = next(d for d in doc.slide(2).walk() if d.kind == "table")
    model = doc.get(table.obj.msg.tableModel.identifier).msg
    pad = doc.get(model.body_cell_style.identifier).msg.cell_properties.padding
    assert (pad.left, pad.right, pad.top, pad.bottom) == (16, 16, 6, 6)
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps([{"op": "style", "slide": 2, "kind": "table", "fill": "#FFFFFF"}]))
    assert run("key_edit.py", deck, "-o", str(tmp_path / "x.key"), "--ops", str(bad)).returncode != 0


def test_edit_ops_fan_out(deck, tmp_path):
    out = str(tmp_path / "out.key")
    ops = tmp_path / "ops.json"
    ops.write_text(json.dumps([
        {"op": "background", "all_slides": True, "color": "#FAFAFA"},
        {"op": "style", "all_slides": True, "kind": "title", "size": 40, "shrink": False},
        {"op": "frame", "slides": [2], "kind": "title", "x": 12, "y": 14},
    ]))
    r = run("key_edit.py", deck, "-o", out, "--ops", str(ops))
    assert r.returncode == 0, r.stderr
    doc = KeynoteFile(out)
    assert all(doc.background_info(s) == "#FAFAFA" for s in doc.slides())
    assert doc.slide(2).title_item.frame[:2] == (12, 14)
    r = run("key_text.py", out, "--json", "--styles", "--slides", "2")
    j = json.loads(r.stdout)["slides"][0]
    assert j["background"] == "#FAFAFA" and j["items"][0]["style"]["text"]["size"] == 40


def test_app_placeholder_slips():
    import re

    import keynote_app

    src = 'tell application id "{{APP}}"\nend tell\ntell {{APP}}\nend tell'
    fixed = re.sub(r'application\s+(?:id\s+)?"\{\{APP\}\}"', "{{APP}}", src)
    assert fixed.count("{{APP}}") == 2 and 'id "{{APP}}"' not in fixed
    assert hasattr(keynote_app, "run_applescript")


def test_placeholders_value_labels_and_spacing(deck, tmp_path):
    doc = KeynoteFile(deck)
    s = doc.slide(2)
    assert doc.placeholder_visible(s, "title") is True
    doc.show_placeholder(s, "title", False)
    assert doc.placeholder_visible(s, "title") is False and s.title_visible is False
    doc.show_placeholder(s, "title", True)
    assert s.title_visible is True
    chart = doc.item(doc.slide(3), 1)
    doc.set_chart_options(chart, value_labels=True, value_label_decimals=1)  # creates the per-series settings objects
    unity = keynote_lib._ext(chart.obj.msg)
    n = len(doc.chart_series(chart)["series"])
    # Keynote ignores series settings past `count`: labels stayed hidden when it was left at 0
    assert len(unity.series_non_styles.entries) == n and unity.series_non_styles.count == n
    assert doc.exact_line_spacing() > 0
    out = str(tmp_path / "out.key")
    doc.save(out)
    assert key_check.check(out) == []
    info = KeynoteFile(out).style_info(KeynoteFile(out).slide(2).title_item)
    assert "line_spacing_pt" in info["text"]


def test_table_cells_read_as_displayed(deck):
    doc = KeynoteFile(deck)
    table = doc.item(doc.slide(2), 4)
    bold: list = []
    rows = doc.table_cells(table, bold)
    assert rows and all(isinstance(c, str) for r in rows for c in r)


def test_table_frame_is_drawn_size(deck, tmp_path):
    # the size stored with a table goes stale; the frame is the sum of its columns / rows
    doc = KeynoteFile(deck)
    table = doc.item(doc.slide(2), 4)
    cols, rows = doc.table_grid(table)
    assert table.frame == (1100.0, 260.0, 700.0, 300.0) == (1100.0, 260.0, sum(cols), sum(rows))
    assert (table.geometry.size.width, table.geometry.size.height) != (700.0, 300.0)  # the stale stored size
    doc.set_table_row_height(table, 0, 160)
    out = str(tmp_path / "taller.key")
    doc.save(out)
    j = json.loads(run("key_text.py", out, "--json", "--slides", "2").stdout)
    t = next(i for i in j["slides"][0]["items"] if i["kind"] == "table")
    assert t["frame"] == {"x": 1100.0, "y": 260.0, "w": 700.0, "h": 300.0 - rows[0] + 160}
    assert "caption_h" not in t  # no name shown


def test_missing_deck_never_reaches_keynote(tmp_path):
    import keynote_app

    gone = str(tmp_path / "gone.key")
    r = run("keynote_app.py", "--for-file", gone, "info")
    assert r.returncode == 1 and "no such file" in r.stderr
    script = tmp_path / "s.applescript"
    script.write_text("set {d, wasOpen} to my openDeck(item 1 of argv)\n")
    r = run("keynote_app.py", "run", str(script), gone)
    assert r.returncode == 1 and "no such file" in r.stderr and "not handed to Keynote" in r.stderr
    # a script that saves / exports may name its output, which does not exist yet
    assert keynote_app.missing_deck_args("save d in POSIX file (item 2 of argv)", [gone]) == []
    assert keynote_app.missing_deck_args("export d to POSIX file (item 2 of argv) as PDF", [gone]) == []
    assert keynote_app.missing_deck_args("count slides", [gone, "x"]) == [gone]


def test_keyx_picks_keynote_16_or_refuses(tmp_path, monkeypatch):
    import keynote_app

    keyx = tmp_path / "deck.keyx"
    with zipfile.ZipFile(keyx, "w") as z:
        z.writestr("main.json", "{}")
    new16 = {"id": keynote_app.NEW_ID, "version": "16.0", "path": "/x"}
    new15 = {"id": keynote_app.NEW_ID, "version": "15.4", "path": "/x"}
    old13 = {"id": keynote_app.OLD_ID, "version": "13.2", "path": "/y"}
    monkeypatch.delenv("KEYNOTE_APP", raising=False)

    monkeypatch.setattr(keynote_app, "installed", lambda: [new16, old13])
    assert keynote_app.choose(for_file=str(keyx)) is new16  # not the oldest: 13.2 cannot open a .keyx
    assert keynote_app.choose("16", str(keyx)) is new16
    assert keynote_app.choose("15", str(keyx)) is new16  # "15" still means Keynote Creator Studio
    assert keynote_app.choose("15") is new16 and keynote_app.choose("14") is old13
    with pytest.raises(keynote_app.KeynoteAppError, match="13.2 cannot open"):
        keynote_app.choose("13", str(keyx))

    for apps in ([new15, old13], [old13]):
        monkeypatch.setattr(keynote_app, "installed", lambda apps=apps: apps)
        with pytest.raises(keynote_app.KeynoteAppError, match="Keynote 16 or later"):
            keynote_app.choose(for_file=str(keyx))

    # the file-level scripts say what the file is and where to take it
    with pytest.raises(KeynoteError, match="creator-studio-doc-format"):
        KeynoteFile(str(keyx))
    r = run("key_text.py", str(keyx))
    assert r.returncode == 1 and ".keyx" in r.stderr
    # a missing .keyx is kept away from Keynote like any other deck
    assert keynote_app.missing_deck_args("count slides", [str(tmp_path / "gone.keyx")]) == [str(tmp_path / "gone.keyx")]


@app
def test_build_grows_rows_whose_text_would_be_cut_off(tmp_path):
    rows = [["ID", "サンプル名\n(ID)", "Long English header text here"], ["A", "B", "x"], ["C", "D", "y"]]
    spec = {"slides": [
        {"layout": "blank", "items": [{"type": "table", "x": 100, "y": 100, "rows": rows, "font_size": 28, "col_widths": [200, 200, 250]}]},
        {"layout": "blank", "items": [{"type": "table", "x": 100, "y": 100, "rows": rows, "font_size": 28, "col_widths": [200, 200, 250], "padding": 16}]},
        {"layout": "blank", "items": [{"type": "table", "x": 100, "y": 100, "rows": [["a", "b"], ["1", "2"]], "font_size": 28}]},
    ]}
    p, out = tmp_path / "s.json", str(tmp_path / "t.key")
    p.write_text(json.dumps(spec))
    r = run("key_build.py", str(p), out)
    assert r.returncode == 0, r.stderr
    assert "slide 1: table (item 1): rows grown" in r.stderr and "slide 2: table (item 1): rows grown" in r.stderr
    assert "slide 3" not in r.stderr  # an ordinary table is left alone
    doc = KeynoteFile(out)
    one, padded, plain = (doc.item(doc.slide(i), 1) for i in (1, 2, 3))
    for t in (one, padded, plain):
        assert all(need <= size + 0.5 for _r, size, need in doc.table_row_fits(t))
    h1, h2 = doc.table_grid(one)[1][0], doc.table_grid(padded)[1][0]
    assert h1 > 100 and h2 >= h1 + 20  # three lines; the padding adds 2 x 12 pt
    assert len(set(doc.table_grid(plain)[1])) == 1
    r = run("key_check.py", out, "--layout")
    assert "no cut-off table rows" in r.stdout, r.stdout
    # a row made too short again is reported by key_check --layout
    doc.set_table_row_height(one, 0, 60)
    short = str(tmp_path / "short.key")
    doc.save(short)
    r = run("key_check.py", short, "--layout")
    assert "slide 1: item 1 table: row 1 is 60 pt tall" in r.stdout and "cut off" in r.stdout, r.stdout
