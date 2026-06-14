#!/usr/bin/env python3
"""Build PRESENTATION.md into a PDF slide deck.

Canonical path (any machine with Node) is Marp — see the header of PRESENTATION.md.
This script is a zero-Node fallback that reuses the repo's already-installed
Playwright Chromium (the same engine render_figs.py uses): it converts the
Marp-flavoured markdown to a paginated HTML and prints it to PDF.

    python3 build_presentation.py            # -> out/PRESENTATION.pdf

It understands Marp's `![bg right:NN% fit](img)` directive as a right-column
image (two-column slide) and renders all other images inline.
"""
import os
import re
import sys

import markdown
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "PRESENTATION.md")
OUT = os.path.join(ROOT, "out", "PRESENTATION.pdf")

IMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def strip_frontmatter(text):
    """Drop the leading HTML comment(s) and the YAML frontmatter block."""
    text = text.lstrip()
    # remove a leading HTML comment block if present
    while text.startswith("<!--"):
        end = text.find("-->")
        text = text[end + 3:].lstrip()
    # remove YAML frontmatter delimited by --- ... ---
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            nl = text.find("\n", end + 1)
            text = text[nl + 1:]
    return text


def split_slides(body):
    parts, buf = [], []
    for line in body.splitlines():
        if line.strip() == "---":
            parts.append("\n".join(buf))
            buf = []
        else:
            buf.append(line)
    parts.append("\n".join(buf))
    return [p for p in (s.strip() for s in parts) if p]


def render_slide(md_text):
    md_text = COMMENT_RE.sub("", md_text)
    bg_img = None
    full_img = None

    def resolve(src):
        if src.startswith(("http://", "https://", "/")):
            return src
        return os.path.join(ROOT, src)  # repo-relative -> absolute for file:// page

    def take(m):
        nonlocal bg_img, full_img
        alt, src = m.group(1).strip(), resolve(m.group(2))
        if alt.startswith("bg"):
            bg_img = src
            return ""  # pull background image out of the markdown flow
        if alt.startswith("full"):
            full_img = src
            return ""  # pull big image out; rendered as a media slide
        return f"![{alt}]({src})"

    md_text = IMG_RE.sub(take, md_text).strip()
    html = markdown.markdown(md_text, extensions=["tables", "fenced_code", "sane_lists"])
    if bg_img:
        return (
            f'<section class="slide split">'
            f'<div class="col-text">{html}</div>'
            f'<div class="col-img"><img src="{bg_img}"></div>'
            f"</section>"
        )
    if full_img:
        return (
            f'<section class="slide media">'
            f'<div class="media-head">{html}</div>'
            f'<div class="media-img"><img src="{full_img}"></div>'
            f"</section>"
        )
    return f'<section class="slide">{html}</section>'


def build_html(slides):
    body = "\n".join(render_slide(s) for s in slides)
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
  @page {{ size: 1280px 720px; margin: 0; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; font-family: -apple-system, Segoe UI, Helvetica, Arial, sans-serif;
         color: #1f2933; }}
  .slide {{ width: 1280px; height: 720px; padding: 54px 64px; overflow: hidden;
            page-break-after: always; position: relative; }}
  .slide.split {{ display: flex; gap: 36px; align-items: center; }}
  .col-text {{ flex: 1 1 0; }}
  .col-img {{ flex: 0 0 46%; display: flex; justify-content: center; align-items: center; }}
  .col-img img {{ max-width: 100%; max-height: 600px; border: 1px solid #d2d6dc;
                  border-radius: 6px; }}
  .slide.media {{ display: flex; flex-direction: column; }}
  .media-head {{ flex: 0 0 auto; }}
  .media-head h2 {{ margin: 0 0 6px; }}
  .media-head p {{ font-size: 20px; color: #486581; margin: 0 0 12px; }}
  .media-img {{ flex: 1 1 auto; min-height: 0; display: flex;
                align-items: center; justify-content: center; }}
  .media-img img {{ max-width: 100%; max-height: 100%; object-fit: contain;
                    border: 1px solid #d2d6dc; border-radius: 6px; }}
  h1 {{ color: #1a73e8; font-size: 46px; margin: 0 0 18px; }}
  h2 {{ color: #1a73e8; font-size: 34px; margin: 0 0 18px; }}
  h3 {{ color: #334e68; font-size: 26px; margin: 0 0 14px; }}
  p, li {{ font-size: 23px; line-height: 1.45; }}
  .col-text p, .col-text li {{ font-size: 21px; }}
  ul {{ margin: 8px 0; padding-left: 26px; }}
  table {{ border-collapse: collapse; font-size: 21px; margin: 10px 0; }}
  th, td {{ border: 1px solid #cbd2d9; padding: 7px 12px; text-align: left; }}
  th {{ background: #f0f4f8; }}
  blockquote {{ border-left: 4px solid #1a73e8; margin: 14px 0; padding: 4px 18px;
                color: #486581; font-style: italic; }}
  code {{ background: #f0f4f8; padding: 1px 5px; border-radius: 3px; font-size: 0.92em; }}
  .small {{ font-size: 17px; color: #829ab1; }}
  .big {{ font-size: 40px; color: #243b53; line-height: 1.5; margin: 18px 0; }}
  .big code {{ font-size: 0.9em; }}
  img {{ max-width: 100%; }}
</style></head><body>
{body}
</body></html>"""


def main():
    with open(SRC, encoding="utf-8") as f:
        text = f.read()
    slides = split_slides(strip_frontmatter(text))
    html = build_html(slides)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    html_path = os.path.join(ROOT, "out", "_presentation.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto("file://" + html_path, wait_until="networkidle")
        page.pdf(path=OUT, width="1280px", height="720px", print_background=True)
        browser.close()
    print(f"Wrote {OUT} ({len(slides)} slides)")


if __name__ == "__main__":
    sys.exit(main())
