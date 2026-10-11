#!/usr/bin/env python3
"""
Offline test harness for labelcrop.

Generates representative inputs, runs the real detection and placement code
against them, and writes side-by-side PNG previews so the result can be eyeballed
without a printer.

    python3 test_crop.py [outdir]        # default: ./testout

Each case produces <name>.png: input pages on the left with the detected crop
boxed in red, output label pages on the right at true 103x164mm aspect.
"""
import importlib.machinery
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# labelcrop has no .py extension (it is a CUPS backend), so load it by path.
spec = importlib.util.spec_from_loader(
    "labelcrop",
    importlib.machinery.SourceFileLoader("labelcrop", os.path.join(HERE, "labelcrop")),
)
labelcrop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(labelcrop)

try:
    import pymupdf as fitz
except ImportError:
    import fitz

MM = labelcrop.MM
A4 = (210 * MM, 297 * MM)


# ------------------------------------------------------------ sample inputs ----


def _barcode(page, rect, seed=7):
    """A plausible Code128-ish barcode block drawn as filled bars."""
    x, n = rect.x0, 0
    widths = [1.0, 2.0, 1.0, 3.0, 1.5, 1.0, 2.5, 1.0, 1.5, 2.0, 1.0, 3.0]
    while x < rect.x1 - 1:
        w = widths[(n + seed) % len(widths)]
        if n % 2 == 0:
            page.draw_rect(fitz.Rect(x, rect.y0, min(x + w, rect.x1), rect.y1),
                           color=None, fill=(0, 0, 0))
        x += w
        n += 1


def _text(page, rect, s, **kw):
    """insert_textbox that shouts instead of silently dropping overflowing text."""
    left = page.insert_textbox(fitz.Rect(rect), s, **kw)
    if left < 0:
        raise RuntimeError(f"fixture text does not fit {tuple(rect)}: {s[:40]!r}")


def _label_content(page, box, carrier="EVRI", tracking="H00AA 1234 5678 9012"):
    """Draw the inside of a shipping label within box."""
    page.draw_rect(box, color=(0, 0, 0), width=1.2)
    x0, y0, x1, y1 = box
    w = x1 - x0
    _text(page, (x0 + 8, y0 + 6, x1 - 8, y0 + 44), carrier,
          fontsize=22, fontname="hebo")
    page.draw_line((x0, y0 + 46), (x1, y0 + 46), color=(0, 0, 0), width=0.8)
    _text(page, (x0 + 8, y0 + 52, x1 - 8, y0 + 142),
          "DELIVER TO\nJ Example\n1 Sample Street\nExampleton\nEX1 2AB", fontsize=11)
    page.draw_line((x0, y0 + 146), (x1, y0 + 146), color=(0, 0, 0), width=0.8)
    _barcode(page, fitz.Rect(x0 + 12, y0 + 154, x1 - 12, y0 + 196))
    _text(page, (x0 + 8, y0 + 200, x1 - 8, y0 + 222), tracking,
          fontsize=10, align=1)
    page.draw_rect(fitz.Rect(x0 + 8, y1 - 62, x0 + w * 0.45, y1 - 8),
                   color=(0, 0, 0), width=0.6)
    _text(page, (x0 + 12, y1 - 58, x0 + w * 0.45 - 4, y1 - 10),
          "Parcel 1 of 1\n0.5 kg\nSVC: Standard", fontsize=9)
    _barcode(page, fitz.Rect(x0 + w * 0.5, y1 - 54, x1 - 10, y1 - 14), seed=3)


def case_bordered_a4(path):
    """Classic carrier label: bordered box in the upper half of an A4 page."""
    doc = fitz.open()
    p = doc.new_page(width=A4[0], height=A4[1])
    _label_content(p, fitz.Rect(60, 80, 60 + 300, 80 + 300))
    doc.save(path)
    doc.close()


def case_header_footer(path):
    """Browser print: page header, URL footer, label in the middle, no outer frame."""
    doc = fitz.open()
    p = doc.new_page(width=A4[0], height=A4[1])
    _text(p, (40, 14, A4[0] - 40, 40), "Vinted - Your shipping label", fontsize=9)
    _text(p, (40, 14, A4[0] - 40, 40), "04/10/2026, 14:12", fontsize=9, align=2)
    _label_content(p, fitz.Rect(90, 120, 90 + 290, 120 + 300),
                   carrier="InPost", tracking="IP 9988 7766 5544")
    _text(p, (40, A4[1] - 40, A4[0] - 40, A4[1] - 14),
          "https://www.vinted.co.uk/items/12345678/shipping-label", fontsize=8)
    _text(p, (40, A4[1] - 40, A4[0] - 40, A4[1] - 14), "1/1", fontsize=8, align=2)
    doc.save(path)
    doc.close()


def case_full_text_doc(path):
    """A real document: full-page text, nothing to crop to."""
    doc = fitz.open()
    p = doc.new_page(width=A4[0], height=A4[1])
    body = ("Homelab change record\n\n" + (
        "The proxy queue accepts any document and reduces it to something the "
        "label roll can carry. Where a bordered region is found it is cropped "
        "out; where none exists the ink bounding box is used instead; and where "
        "the content already fills the page the page is simply fitted. "
    ) * 14)
    _text(p, (50, 50, A4[0] - 50, A4[1] - 50), body, fontsize=10)
    doc.save(path)
    doc.close()


def case_blank_second_page(path):
    """Two pages, the second one empty: the Vinted PDF shape."""
    doc = fitz.open()
    p = doc.new_page(width=A4[0], height=A4[1])
    _label_content(p, fitz.Rect(70, 90, 70 + 300, 90 + 300),
                   carrier="Yodel", tracking="YD 4455 6677 8899")
    doc.new_page(width=A4[0], height=A4[1])  # deliberately blank
    doc.save(path)
    doc.close()


def case_landscape_label(path):
    """A wide label on a landscape page: must come out rotated."""
    doc = fitz.open()
    p = doc.new_page(width=A4[1], height=A4[0])
    _label_content(p, fitz.Rect(70, 60, 70 + 430, 60 + 260),
                   carrier="ROYAL MAIL", tracking="RM 1122 3344 5566")
    doc.save(path)
    doc.close()


def case_png_screenshot(path):
    """A phone screenshot: image input, content inset from the edges."""
    w, h = 1170, 2532
    doc = fitz.open()
    p = doc.new_page(width=w, height=h)
    p.draw_rect(p.rect, color=None, fill=(1, 1, 1))
    p.draw_rect(fitz.Rect(0, 0, w, 120), color=None, fill=(0.93, 0.93, 0.95))
    _text(p, (30, 30, w - 30, 115), "9:41          Mail", fontsize=42)
    p.draw_rect(fitz.Rect(70, 300, w - 70, 1500), color=(0.2, 0.2, 0.2), width=3)
    _text(p, (110, 330, w - 110, 980),
          "Order confirmed\n\nOrder #AB-10294\n\n2 x Widget\n1 x Gadget\n\n"
          "Total  GBP 42.00", fontsize=52)
    _barcode(p, fitz.Rect(150, 1000, w - 150, 1200), seed=5)
    _text(p, (70, 2420, w - 70, 2510), "sent from my iPhone", fontsize=30, align=1)
    pix = p.get_pixmap(dpi=150)
    pix.save(path)
    doc.close()


CASES = [
    ("01-bordered-label-a4", "pdf", case_bordered_a4,
     "bordered carrier label on A4"),
    ("02-header-footer", "pdf", case_header_footer,
     "browser print with header + URL footer"),
    ("03-full-page-doc", "pdf", case_full_text_doc,
     "full-page text document"),
    ("04-blank-page-2", "pdf", case_blank_second_page,
     "2 pages, page 2 blank"),
    ("05-landscape-label", "pdf", case_landscape_label,
     "landscape label, needs rotating"),
    ("06-png-screenshot", "png", case_png_screenshot,
     "PNG phone screenshot"),
]


# ----------------------------------------------------------------- preview ----


def render_preview(src_path, out_path, previews, png_path, cfg):
    """Input pages with detected boxes, beside the output label pages."""
    src = labelcrop.open_input(src_path, cfg)
    out = fitz.open(out_path)

    thumb_h = 420
    gap = 14
    in_pix, out_pix = [], []

    boxes = {p["page"]: p for p in previews}
    for pno in range(src.page_count):
        page = src[pno]
        shot = fitz.open()
        q = shot.new_page(width=page.rect.width, height=page.rect.height)
        try:
            q.show_pdf_page(q.rect, src, pno)
        except ValueError:
            # Older PyMuPDF refuses to copy a page with no content stream at
            # all. That is exactly the blank page this case exists to test, so
            # leave the thumbnail white and carry on.
            pass
        info = boxes.get(pno)
        if info and info.get("clip"):
            q.draw_rect(fitz.Rect(*info["clip"]), color=(1, 0, 0),
                        width=max(1.5, page.rect.height / 300))
        elif info and info["reason"] == "blank":
            q.insert_textbox(fitz.Rect(10, 10, page.rect.width - 10, 60),
                             "BLANK - SKIPPED", fontsize=24, color=(1, 0, 0))
        in_pix.append(shot[0].get_pixmap(
            matrix=fitz.Matrix(thumb_h / page.rect.height,
                               thumb_h / page.rect.height), alpha=False))
        shot.close()

    for pno in range(out.page_count):
        page = out[pno]
        s = thumb_h / page.rect.height
        pm = page.get_pixmap(matrix=fitz.Matrix(s, s), alpha=False)
        out_pix.append(pm)

    total_w = sum(p.width + gap for p in in_pix) + 40 + \
        sum(p.width + gap for p in out_pix) + 40
    canvas = fitz.open()
    cp = canvas.new_page(width=total_w, height=thumb_h + 70)
    cp.draw_rect(cp.rect, color=None, fill=(1, 1, 1))

    x = 20
    cp.insert_text((x, 22), "INPUT  (red = detected crop)", fontsize=13,
                   fontname="hebo")
    for pm in in_pix:
        r = fitz.Rect(x, 34, x + pm.width, 34 + pm.height)
        cp.insert_image(r, pixmap=pm)
        cp.draw_rect(r, color=(0.75, 0.75, 0.75), width=0.6)
        x += pm.width + gap

    x += 26
    cp.draw_line((x - 20, 30), (x - 20, thumb_h + 44), color=(0.6, 0.6, 0.6), width=1)
    cp.insert_text((x, 22), "OUTPUT  (103 x 164 mm label pages)", fontsize=13,
                   fontname="hebo")
    for pm in out_pix:
        r = fitz.Rect(x, 34, x + pm.width, 34 + pm.height)
        cp.insert_image(r, pixmap=pm)
        cp.draw_rect(r, color=(0.1, 0.1, 0.1), width=0.9)
        x += pm.width + gap

    cp.get_pixmap(dpi=110).save(png_path)
    canvas.close()
    out.close()
    src.close()


# -------------------------------------------------------------------- main ----


def main():
    outdir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "testout")
    os.makedirs(outdir, exist_ok=True)
    cfg = dict(labelcrop.DEFAULTS)
    cfg["KEEP_DIR"] = ""  # no debug copies from the harness

    print(f"{'case':24} {'pages':>5}  {'result':<11} {'rot':>4} {'scale':>6}  note")
    print("-" * 96)
    failures = 0

    for name, ext, gen, note in CASES:
        src = os.path.join(outdir, f"{name}.{ext}")
        out = os.path.join(outdir, f"{name}-out.pdf")
        png = os.path.join(outdir, f"{name}.png")
        gen(src)
        previews = []
        try:
            n = labelcrop.build(src, out, cfg, previews=previews)
        except Exception as e:
            print(f"{name:24} {'-':>5}  FAILED      {e!r}")
            failures += 1
            continue

        printed = [p for p in previews if p["reason"] != "blank"]
        skipped = len(previews) - len(printed)
        first = printed[0] if printed else {}
        reasons = "+".join(sorted({p["reason"] for p in printed})) or "-"
        print(f"{name:24} {n:>5}  {reasons:<11} "
              f"{first.get('rotate', 0):>4} {first.get('scale', 0):>6.2f}  {note}"
              + (f"  [{skipped} blank skipped]" if skipped else ""))

        try:
            render_preview(src, out, previews, png, cfg)
        except Exception as e:
            print(f"{'':24} preview failed: {e!r}")
            failures += 1

    print("-" * 96)
    print(f"previews: {outdir}/*.png")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
