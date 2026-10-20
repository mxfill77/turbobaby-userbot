# -*- coding: utf-8 -*-
"""Annex 4 builder: registration-book pages + photos of the 38 motorcycles of Annex 1.

Two steps (a cloud session has everything they need: poppler, Pillow, Chromium via playwright):
  prepare — <src_dir>/<drive_id>.pdf (the Drive PDFs named in annex_map.json) → <img_dir>/<NN>_cover.jpg
            and <NN>_reg.jpg: page rendered at 200 dpi, cut to the verified document box, white trimmed.
  html    — annex_map.json + <img_dir> → annex.html; then `node print_pdf.js annex.html annex.pdf`.
Photos are put as <img_dir>/<NN>_photo.jpg after the plate on them is checked against the book, and
the bike gets a "photo" entry in the map (source + plate seen). Only what the map names is attached;
an image without a map entry, or a map entry without an image, stops the build. Anything not in the
map becomes a "To be provided" placeholder, and the index marks it ○.
Fonts: Noto Sans / Noto Sans Thai / Noto Serif Thai woff2 from npm @fontsource (see README.md).
"""
import os
import sys
import json
import html
import argparse
import subprocess
import pathlib

NAVY = "#1F4E79"
ROW = "#F3F7FA"
LINE = "#B7C9D6"

PENDING_TH = "จะจัดส่งเพิ่มเติม"
PENDING_EN = "To be provided"


def esc(s):
    return html.escape(s or "")


def font_faces(font_dir):
    faces = [
        ("Noto Sans Thai", 400, "fontsource-noto-sans-thai-5.1.1/package/files/noto-sans-thai-thai-400-normal.woff2"),
        ("Noto Sans Thai", 700, "fontsource-noto-sans-thai-5.1.1/package/files/noto-sans-thai-thai-700-normal.woff2"),
        ("Noto Serif Thai", 400, "fontsource-noto-serif-thai-5.1.1/package/files/noto-serif-thai-thai-400-normal.woff2"),
        ("Noto Serif Thai", 600, "fontsource-noto-serif-thai-5.1.1/package/files/noto-serif-thai-thai-600-normal.woff2"),
        ("Noto Sans", 400, "fontsource-noto-sans-5.1.1/package/files/noto-sans-latin-400-normal.woff2"),
        ("Noto Sans", 700, "fontsource-noto-sans-5.1.1/package/files/noto-sans-latin-700-normal.woff2"),
    ]
    out = []
    for fam, w, rel in faces:
        p = os.path.join(font_dir, rel)
        if not os.path.exists(p):
            sys.exit("font missing: %s" % p)
        out.append("@font-face{font-family:'%s';font-weight:%d;src:url('%s') format('woff2');}"
                   % (fam, w, uri(p)))
    return "\n".join(out)


CSS = """
@page { size: A4; margin: 14mm 14mm 16mm 14mm; }
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; background: #fff; }
body { font-family: 'Noto Sans', 'Noto Sans Thai', sans-serif; font-size: 9.5pt; color: #111; }
.th { font-family: 'Noto Sans Thai', 'Noto Sans', sans-serif; }
h1 { font-family: 'Noto Serif Thai', 'Noto Sans', serif; font-weight: 400; color: NAVY;
     font-size: 22pt; margin: 0 0 1mm 0; line-height: 1.25; }
h1 .en { font-family: 'Noto Sans', sans-serif; font-size: 12.5pt; font-weight: 700; letter-spacing: .2px; }
.sub { color: NAVY; font-weight: 700; font-size: 11pt; margin: 0 0 3mm 0; }
p { margin: 0 0 1.6mm 0; line-height: 1.45; }
p.en { font-size: 8.8pt; color: #333; }
ol.items { margin: 1mm 0 2.5mm 5mm; padding: 0; }
ol.items li { margin: 0 0 1mm 0; line-height: 1.4; }
table.idx { width: 100%; border-collapse: collapse; margin-top: 2mm; font-size: 7.6pt; }
table.idx th { background: NAVY; color: #fff; font-weight: 700; padding: 1.4mm 1.2mm; border: 0.3mm solid NAVY;
               text-align: center; line-height: 1.25; }
table.idx td { border: 0.3mm solid LINE; padding: 1mm 1.2mm; line-height: 1.25; vertical-align: middle; }
table.idx tr:nth-child(even) td { background: ROW; }
table.idx td.c { text-align: center; }
table.idx td.mono { font-family: 'Noto Sans', monospace; letter-spacing: .15px; }
.ok { color: #1a6b2f; font-weight: 700; }
.legend { font-size: 8pt; color: #444; margin-top: 1.5mm; }
.pend { color: #8a5a00; }
.sig { width: 100%; border-collapse: collapse; margin-top: 5mm; font-size: 8.5pt; page-break-inside: avoid; }
.sig td { border: 0.3mm solid LINE; padding: 2.5mm; width: 50%; vertical-align: top; height: 30mm; }
.sig .who { color: NAVY; font-weight: 700; }
.draft { display: inline-block; border: 0.4mm solid #b3261e; color: #b3261e; font-weight: 700;
         padding: 0.6mm 2mm; font-size: 8pt; margin-bottom: 2mm; }
.page { page-break-before: always; }
.bar { display: flex; justify-content: space-between; align-items: baseline; border-bottom: 0.6mm solid NAVY;
       padding-bottom: 1.2mm; margin-bottom: 2.5mm; }
.bar .t { color: NAVY; font-weight: 700; font-size: 13pt; }
.bar .n { color: NAVY; font-size: 9pt; white-space: nowrap; }
.meta { font-size: 8.6pt; margin-bottom: 2.5mm; line-height: 1.5; }
.meta b { color: NAVY; }
.sec { color: NAVY; font-weight: 700; font-size: 9.5pt; margin: 0 0 1.2mm 0; }
.sec .en { font-weight: 400; color: #333; }
.fig { border: 0.3mm solid LINE; display: flex; align-items: center; justify-content: center; overflow: hidden;
       background: #fff; margin-bottom: 3mm; }
.fig img { max-width: 100%; max-height: 100%; object-fit: contain; display: block; }
.fig.cover { height: 68mm; }
.fig.reg { height: 150mm; }
.fig.reg.full { height: 222mm; }
.fig.photo { height: 228mm; }
.ph { background: ROW; border: 0.5mm dashed LINE; color: #5b6b78; text-align: center; font-size: 11pt; }
.ph small { display: block; font-size: 8.5pt; margin-top: 1mm; }
"""


def uri(p):
    """file:// URL with the path percent-encoded: a '#', '?' or '%' in a folder name would otherwise
    turn every image into a broken-image icon while the index still shows ✓."""
    return pathlib.Path(os.path.abspath(p)).as_uri()


def fig(cls, path, label_th, label_en):
    if path:
        return '<div class="fig %s"><img src="%s"></div>' % (cls, esc(uri(path)))
    return ('<div class="fig %s ph"><div><span class="th">%s</span> / %s<small>%s — %s</small></div></div>'
            % (cls, PENDING_TH, PENDING_EN, esc(label_th), esc(label_en)))


def build(amap, img_dir, font_dir, draft):
    bikes = amap["bikes"]
    total = len(bikes)

    # An image is attached only when the map says so: a stray NN_<kind>.jpg left in img_dir by an
    # earlier run must not land under a bike number with a ✓, and a mapped page must not vanish.
    errors = []
    for b in bikes:
        for kind, key in (("cover", "cover_src"), ("reg", "reg_src"), ("photo", "photo")):
            p = os.path.join(img_dir, "%02d_%s.jpg" % (b["no"], kind))
            if b.get(key) and not os.path.exists(p):
                errors.append("#%d %s: in the map, but %s is missing" % (b["no"], kind, p))
            if not b.get(key) and os.path.exists(p):
                errors.append("#%d %s: %s exists, but the map has no %s" % (b["no"], kind, p, key))
    if errors:
        sys.exit("map and images disagree:\n  " + "\n  ".join(errors))

    def img(no, kind):
        return os.path.join(img_dir, "%02d_%s.jpg" % (no, kind)) if {
            "cover": "cover_src", "reg": "reg_src", "photo": "photo"}[kind] in by_no[no] else None

    by_no = {b["no"]: b for b in bikes}

    rows = []
    for b in bikes:
        no = b["no"]
        marks = []
        for kind in ("cover", "reg", "photo"):
            marks.append('<td class="c ok">✓</td>' if img(no, kind) else '<td class="c pend">○</td>')
        rows.append('<tr><td class="c">%d</td><td>%s</td><td class="th">%s</td><td class="mono">%s</td>%s</tr>'
                    % (no, esc(b["name"]), esc(b.get("reg_no_book") or "—"), esc(b.get("vin") or "—"),
                       "".join(marks)))

    a = amap["agreement"]
    out = ["<!doctype html><html><head><meta charset='utf-8'><title>Annex 4</title><style>",
           font_faces(font_dir), CSS.replace("NAVY", NAVY).replace("ROW", ROW).replace("LINE", LINE),
           "</style></head><body>"]
    if draft:
        out.append('<div class="draft">ร่าง / DRAFT — %s</div>' % esc(draft))
    out.append('<h1>ภาคผนวก 4 <span class="en">ANNEX 4 — REGISTRATION BOOKS AND PHOTOGRAPHS OF THE MOTORCYCLES</span></h1>')
    out.append('<div class="sub"><span class="th">คู่มือจดทะเบียนรถและภาพถ่ายรถจักรยานยนต์ตามภาคผนวก 1</span></div>')
    out.append('<p class="th">แนบท้ายสัญญาเช่ารถจักรยานยนต์ระยะยาว ลงวันที่ %s ระหว่าง %s (“ผู้ให้เช่า”) กับ %s (“ผู้เช่า”)</p>'
               % (esc(a["date_th"]), esc(a["lessor_th"]), esc(a["lessee_th"])))
    out.append('<p class="en">Attached to the Long-Term Motorcycle Rental Agreement dated %s between %s (Lessor) and %s (Lessee).</p>'
               % (esc(a["date_en"]), esc(a["lessor_en"]), esc(a["lessee_en"])))
    out.append('<p class="th">สำหรับรถจักรยานยนต์แต่ละคัน เรียงตามลำดับในภาคผนวก 1 ได้แนบเอกสารดังนี้</p>')
    out.append('<p class="en">For each motorcycle, in the order of Annex 1, the following are attached:</p>')
    out.append('<ol class="items">'
               '<li><span class="th">หน้าปกคู่มือจดทะเบียนรถ ซึ่งแสดงเลขทะเบียนทั้งหมดของรถ</span><br>'
               '<span class="en">the front cover of the registration book, listing all registration numbers of the vehicle;</span></li>'
               '<li><span class="th">หน้ารายการจดทะเบียนที่ระบุ MR. FILIPP VOROBEV เป็นผู้ถือกรรมสิทธิ์ พร้อมยี่ห้อ แบบ และเลขตัวรถ</span><br>'
               '<span class="en">the registration page recording MR. FILIPP VOROBEV as owner (ผู้ถือกรรมสิทธิ์), with make, model and VIN;</span></li>'
               '<li><span class="th">ภาพถ่ายรถจักรยานยนต์ซึ่งปรากฏแผ่นป้ายทะเบียนอย่างชัดเจน</span><br>'
               '<span class="en">a photograph of the motorcycle showing its number plate.</span></li></ol>')
    out.append('<p class="th">ให้ถือเลขตัวรถ (VIN) ตามคู่มือจดทะเบียนรถเป็นหลักในการระบุรถจักรยานยนต์แต่ละคัน '
               'กรณีเลขทะเบียนมีการเปลี่ยนแปลง เลขทะเบียนปัจจุบันเป็นไปตามคู่มือจดทะเบียนรถ '
               'ส่วนชื่อเรียกในภาคผนวก 1 ใช้เพื่อการอ้างอิง</p>')
    out.append('<p class="en">Each motorcycle is identified by the VIN shown in its registration book. Where a registration '
               'number has changed, the current number is as shown in the registration book; the identifier used in '
               'Annex 1 is kept for reference.</p>')
    out.append('<table class="idx"><thead><tr><th>No.</th><th>รถจักรยานยนต์ (ภาคผนวก 1)<br>Motorcycle (Annex 1)</th>'
               '<th>เลขทะเบียนตามคู่มือ<br>Reg. no. (book)</th><th>เลขตัวรถ<br>VIN</th><th>หน้าปก<br>Cover</th>'
               '<th>หน้ารายการจดทะเบียน<br>Reg. page</th><th>ภาพถ่าย<br>Photo</th></tr></thead><tbody>')
    out.extend(rows)
    out.append("</tbody></table>")
    out.append('<p class="legend">✓ <span class="th">แนบแล้ว</span> / attached &nbsp;&nbsp; ○ <span class="th">%s</span> / %s</p>'
               % (PENDING_TH, PENDING_EN.lower()))
    out.append('<table class="sig"><tr><td><div class="who"><span class="th">ผู้ให้เช่า</span> / LESSOR</div>%s<br><br>'
               'Signature / <span class="th">ลายมือชื่อ</span>: ______________________<br><br>'
               'Date / <span class="th">วันที่</span>: ______________</td>'
               '<td><div class="who"><span class="th">ผู้เช่า</span> / LESSEE</div>%s<br><br>'
               'Signature / <span class="th">ลายมือชื่อ</span>: ______________________<br><br>'
               'Date / <span class="th">วันที่</span>: ______________</td></tr></table>'
               % (esc(a["lessor_sig"]), esc(a["lessee_sig"])))

    for b in bikes:
        no = b["no"]
        meta = []
        if b.get("reg_no_book"):
            meta.append('<b class="th">เลขทะเบียน</b> / <b>Reg. no.:</b> <span class="th">%s</span>' % esc(b["reg_no_book"]))
        if b.get("make_model"):
            meta.append('<b class="th">ยี่ห้อ/แบบ</b> / <b>Make/model:</b> %s' % esc(b["make_model"]))
        if b.get("vin"):
            meta.append('<b class="th">เลขตัวรถ</b> / <b>VIN:</b> %s' % esc(b["vin"]))
        head = ('<div class="bar"><div class="t">%d. %s</div><div class="n">ANNEX 4 · %d / %d</div></div>'
                '<div class="meta">%s</div>' % (no, esc(b["name"]), no, total, " &nbsp;·&nbsp; ".join(meta)))
        cover = img(no, "cover")
        reg = img(no, "reg")
        photo = img(no, "photo")
        out.append('<section class="page">' + head)
        out.append('<div class="sec"><span class="th">1. หน้าปกคู่มือจดทะเบียนรถ</span> <span class="en">/ Registration book — front cover</span></div>')
        out.append(fig("cover", cover, "หน้าปกคู่มือจดทะเบียนรถ", "registration book front cover"))
        out.append('<div class="sec"><span class="th">2. หน้ารายการจดทะเบียน — ผู้ถือกรรมสิทธิ์ MR. FILIPP VOROBEV</span> '
                   '<span class="en">/ Registration page — owner MR. FILIPP VOROBEV</span></div>')
        out.append(fig("reg", reg, "หน้ารายการจดทะเบียน", "registration page"))
        out.append("</section>")
        out.append('<section class="page">' + head)
        out.append('<div class="sec"><span class="th">3. ภาพถ่ายรถจักรยานยนต์</span> <span class="en">/ Photograph of the motorcycle</span></div>')
        out.append(fig("photo", photo, "ภาพถ่ายรถจักรยานยนต์ซึ่งปรากฏแผ่นป้ายทะเบียน", "photo showing the number plate"))
        out.append("</section>")
    out.append("</body></html>")
    return "\n".join(out)


def prepare(amap, src_dir, img_dir, work_dir, pad=0.015, fuzz=18, margin=12, long_side=(1500, 1900)):
    """Book pages from the Drive PDFs → cropped JPEGs named <NN>_cover.jpg / <NN>_reg.jpg.

    The pad is a fraction of the verified document box (not of the page), so on a two-up scan the
    crop reaches only a few pixels past the box and does not pull in text of the facing page."""
    from PIL import Image, ImageChops
    os.makedirs(img_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    done = []
    for b in amap["bikes"]:
        for kind, key in (("cover", "cover_src"), ("reg", "reg_src")):
            s = b.get(key)
            if not s:
                continue
            pdf = os.path.join(src_dir, s["drive_id"] + ".pdf")
            if not os.path.exists(pdf):
                print("no source for #%d %s: %s" % (b["no"], kind, pdf))
                continue
            stem = os.path.join(work_dir, "%s_p%d" % (s["drive_id"], s["page"]))
            if not os.path.exists(stem + ".png"):
                subprocess.run(["pdftoppm", "-r", "200", "-f", str(s["page"]), "-l", str(s["page"]),
                                "-png", "-singlefile", pdf, stem], check=True)
            im = Image.open(stem + ".png").convert("RGB")
            w, h = im.size
            bb = s.get("bbox") or {"x0": 0, "y0": 0, "x1": 1, "y1": 1}
            px, py = pad * (bb["x1"] - bb["x0"]), pad * (bb["y1"] - bb["y0"])
            im = im.crop((max(0, int((bb["x0"] - px) * w)), max(0, int((bb["y0"] - py) * h)),
                          min(w, int((bb["x1"] + px) * w)), min(h, int((bb["y1"] + py) * h))))
            ink = ImageChops.difference(im.convert("L"), Image.new("L", im.size, 255))
            box = ink.point(lambda v: 255 if v > fuzz else 0).getbbox()
            if box:
                im = im.crop((max(0, box[0] - margin), max(0, box[1] - margin),
                              min(im.size[0], box[2] + margin), min(im.size[1], box[3] + margin)))
            lim = long_side[0] if kind == "cover" else long_side[1]
            im.thumbnail((lim, lim))
            out = os.path.join(img_dir, "%02d_%s.jpg" % (b["no"], kind))
            im.save(out, quality=84, optimize=True)
            done.append(out)
    return done


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare", help="Drive PDFs → cropped book pages")
    p.add_argument("--map", required=True)
    p.add_argument("--src", required=True, help="dir with <drive_id>.pdf")
    p.add_argument("--img", required=True)
    p.add_argument("--work", required=True, help="scratch dir for page renders")
    h = sub.add_parser("html", help="map + images → annex.html")
    h.add_argument("--map", required=True)
    h.add_argument("--img", required=True)
    h.add_argument("--fonts", required=True)
    h.add_argument("--out", required=True)
    h.add_argument("--draft", default="", help="text of the DRAFT stamp; empty = final, no stamp")
    a = ap.parse_args()
    amap = json.load(open(a.map, encoding="utf-8"))
    if a.cmd == "prepare":
        print("pages:", len(prepare(amap, a.src, a.img, a.work)))
        return
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(build(amap, os.path.abspath(a.img), os.path.abspath(a.fonts), a.draft))
    print("html:", a.out)


if __name__ == "__main__":
    main()
