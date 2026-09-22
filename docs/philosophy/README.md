# Casals — design rationale

Static slide deck. Visual style matches the DFINITY demo presentation
(arch-box slides, gray palette, hollow bullets, section dividers, morphing diagram).

The deck walks the *why* of Casals in **eight numbered steps**. Each step adds one
element to a single growing diagram, states its point in a side panel, and ends
with a *Therefore* that motivates the next step:

1. An application is several canisters
2. Someone must control them — a multisig
3. The application grows (more canisters)
4. Every user needs their own set (more users)
5. Some canisters serve everyone (shared infrastructure)
6. Name the grid, hand it to a conductor
7. The baton: the user decides
8. Two more pieces: a WASM registry and a treasury

The step texts live in `STEPS` in `build.py`; the progress rail, side panel
and authority slide badges are all generated from that list.

After the vocabulary, one more slide answers a question the eight steps raise
but do not settle: if the orchestra is declared in a sheet, why does Casals not
reconcile it? *The sheet is a genesis document, not a control loop* — because
authority is on-chain and shared, convergence depends on other signers, and the
orchestra mutates itself. Its bullets live in `steps.md` like any content slide.

## Build HTML

```bash
python3 build.py
```

Writes `index.html`. Edit `steps.md` (slide order, vocabulary) and/or `build.py`
(step texts, diagram, styles), then rebuild.

Open `index.html` in a browser. Scene URLs use `#N` (1-based). Navigate with
arrow keys, space, Home/End, click, or the dots.

## Export PDF

Requires [Playwright](https://playwright.dev/python/) and Pillow:

```bash
pip install playwright pillow
playwright install chromium   # once per machine
python3 export_pdf.py
```

Default output: `casals-philosophy.pdf`

Always run `python3 build.py` before exporting so the PDF matches the latest deck.
