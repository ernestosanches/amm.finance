#!/usr/bin/env python3
"""Render the generated out/*.html to PNG with open-source headless Chromium (Playwright).

This is verification tooling — it screenshots the REAL rendered Plotly page (what a browser
shows), so the figures can be eyeballed without a desktop browser. Set up via `bash tools.sh`.

  python3 render_figs.py                                  # key HTMLs -> out/png/<name>.png
  python3 render_figs.py orderbook.html                   # one file
  python3 render_figs.py orderbook.html --frames 0,13,23  # capture specific animation frames
  python3 render_figs.py --self-test                      # tiny render, assert PNG produced
"""
import argparse
import os

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
PNGDIR = os.path.join(OUT, "png")
KEY = ["orderbook.html", "depth_over_time.html"]

# Drive a Plotly slider to a named frame, then settle.
_ANIMATE_JS = """(name) => {
  const gd = document.querySelector('.plotly-graph-div');
  return Plotly.animate(gd, [name],
    {mode: 'immediate', frame: {duration: 0, redraw: true}, transition: {duration: 0}});
}"""


def _goto(page, html_path, wait_ms):
    page.goto("file://" + html_path, wait_until="load")
    page.wait_for_timeout(wait_ms)  # let Plotly draw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*", help="HTML files in out/ (default: key figures)")
    ap.add_argument("--frames", default=None, help="comma-separated Plotly frame names to capture")
    ap.add_argument("--wait", type=int, default=2500, help="ms to wait for Plotly to render")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    os.makedirs(PNGDIR, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])  # --no-sandbox: non-root container
        page = browser.new_page(viewport={"width": 1500, "height": 900})
        try:
            if args.self_test:
                t = os.path.join(PNGDIR, "_selftest.png")
                page.set_content("<h1>render-ok</h1>")
                page.screenshot(path=t)
                assert os.path.getsize(t) > 0
                print("self-test OK ->", t)
                return
            for f in (args.files or KEY):
                hp = os.path.join(OUT, f)
                if not os.path.exists(hp):
                    print("skip (missing):", f)
                    continue
                _goto(page, hp, args.wait)
                if args.frames:
                    for fr in (x.strip() for x in args.frames.split(",")):
                        page.evaluate(_ANIMATE_JS, fr)
                        page.wait_for_timeout(700)
                        pp = os.path.join(PNGDIR, f.replace(".html", f"_frame{fr}.png"))
                        page.screenshot(path=pp)
                        print("->", pp)
                else:
                    pp = os.path.join(PNGDIR, f.replace(".html", ".png"))
                    page.screenshot(path=pp)
                    print("->", pp)
        finally:
            browser.close()


if __name__ == "__main__":
    main()
