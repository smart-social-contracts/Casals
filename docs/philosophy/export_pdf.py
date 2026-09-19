#!/usr/bin/env python3
"""Export the Casals philosophy presentation to a multi-page PDF."""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import tempfile
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

DIR = Path(__file__).resolve().parent
DEFAULT_HTML = DIR / "index.html"
DEFAULT_PDF = DIR / "casals-philosophy.pdf"

EXPORT_CSS = """
.dots, footer { display: none !important; }
.page, .page-step {
  transition: none !important;
}
.deck {
  box-shadow: none !important;
}
"""


def scene_count(html_path: Path) -> int:
    text = html_path.read_text(encoding="utf-8")
    steps = [int(n) for n in re.findall(r'data-steps="(\d+)"', text)]
    if not steps:
        raise SystemExit(f"No slides found in {html_path}")
    return sum(steps)


def parse_scene_range(spec: str | None, total: int) -> range:
    if not spec:
        return range(1, total + 1)
    if "-" in spec:
        start_s, end_s = spec.split("-", 1)
        start, end = int(start_s), int(end_s)
    else:
        start = end = int(spec)
    if start < 1 or end > total or start > end:
        raise SystemExit(f"Invalid scene range {spec!r} (deck has {total} scenes)")
    return range(start, end + 1)


def export_pdf(
    html_path: Path,
    output_path: Path,
    *,
    width: int = 1600,
    height: int = 900,
    scenes: range,
    wait_ms: int = 200,
) -> None:
    if not html_path.is_file():
        raise SystemExit(f"Missing {html_path} — run: python3 build.py")

    file_url = html_path.resolve().as_uri()
    images: list[Image.Image] = []
    digests: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": height})
        page.goto(file_url, wait_until="networkidle")
        page.wait_for_function("typeof window.__goToScene === 'function'")
        page.add_style_tag(content=EXPORT_CSS)

        deck = page.locator("article.deck")
        deck.wait_for(state="visible")

        for scene in scenes:
            current = page.evaluate("(n) => window.__goToScene(n)", scene)
            if current != scene:
                raise SystemExit(f"Failed to navigate to scene {scene} (got {current!r})")
            page.wait_for_timeout(wait_ms)
            png_bytes = deck.screenshot(type="png")
            digest = hashlib.sha256(png_bytes).hexdigest()[:12]
            digests.append(digest)
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp.write(png_bytes)
                tmp_path = tmp.name
            try:
                images.append(Image.open(tmp_path).convert("RGB"))
            finally:
                Path(tmp_path).unlink(missing_ok=True)
            print(f"  scene {scene}/{scenes.stop - 1}")

        browser.close()

    unique = len(set(digests))
    if unique < min(3, len(digests)):
        raise SystemExit(
            f"Export looks broken: only {unique} unique frame(s) across "
            f"{len(digests)} scene(s). Check __goToScene in index.html."
        )

    if not images:
        raise SystemExit("No scenes captured")

    first, rest = images[0], images[1:]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    first.save(
        output_path,
        "PDF",
        resolution=150.0,
        save_all=True,
        append_images=rest,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--html", type=Path, default=DEFAULT_HTML)
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--scenes", metavar="RANGE", help="e.g. 1-12 or 5")
    parser.add_argument("--width", type=int, default=1600)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument("--wait-ms", type=int, default=200)
    args = parser.parse_args(argv)

    total = scene_count(args.html)
    scenes = parse_scene_range(args.scenes, total)
    print(f"Exporting {len(scenes)} scene(s) from {args.html.name} → {args.output}")
    export_pdf(
        args.html,
        args.output,
        width=args.width,
        height=args.height,
        scenes=scenes,
        wait_ms=args.wait_ms,
    )
    print(f"Wrote {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
