# Importing from PowerPoint

```bash
python3 scripts/key_import.py deck.pptx out.key --font-map "Helvetica=YuGo-Medium,Helvetica-Bold=YuGo-Bold"   # map only if fonts were substituted
python3 scripts/key_check.py out.key --open --layout
python3 scripts/key_render.py out.key qa/ --jpeg
```

Keynote's importer keeps text, shapes, tables, pictures and notes editable. `key_import.py` runs it without touching other open documents, converts line spacing to PowerPoint's (the main cause of post-import overlaps), and prints `CHECK:` lines for what needs your hands.

## Repair what the report lists

- **Charts can vanish** (the slide is left with a hole). Rebuild them natively: `set current slide of d to slide N`, `add chart …` ([applescript.md](applescript.md#charts)), then `key_edit.py` `chart_options` for colors, `y_min`/`y_max`, `value_labels`, fonts. The numbers are in the pptx (`unzip -p deck.pptx ppt/charts/chart1.xml`).
- **Fonts the Mac lacks are substituted** (usually by Helvetica), which shifts every line break. The report lists the missing fonts and the fonts the imported deck now uses. Re-run with `--font-map "Substitute=Wanted,Substitute-Bold=Wanted-Bold"`, or apply `key_edit.py`'s `replace_font` to the result — one global swap, which keeps mixed bold/regular runs and other fonts (Courier…) intact.
- Windows "Yu Gothic" is `YuGo-Medium` / `YuGo-Bold` on macOS; a font with no macOS counterpart ("Yu Gothic Light") needs the nearest light face.
- **An imported deck keeps the pptx slide size** (often 960 × 540 or 1280 × 720, not 1920 × 1080): read sizes from `--json` before placing anything, and scale the type sizes given in this skill accordingly.

## Compare against the original

- Compare every slide. For a reference picture, export the pptx to PDF with PowerPoint or LibreOffice if installed (`soffice --headless --convert-to pdf deck.pptx`); otherwise compare against the pptx's text (`python -m markitdown deck.pptx`) and judge the renders on their own (overlaps, clipped text, empty areas).
- A LibreOffice rendering is only a rough reference — it substitutes fonts too. Trust the pptx's own XML (`<a:rPr b="1" sz="1800">`) over it when they disagree.
- Small color shifts between the file's values and a render are color management, not an error.
- Report residual differences honestly (line breaks that fall one word differently, a substituted font) rather than claiming an exact match.
