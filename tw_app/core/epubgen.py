#!/usr/bin/env python3
"""
epubgen.py — chapter HTML + metadata se valid EPUB 3 banata hai.

Burp→EPUB process (EPUB-banane-ka-process.md) ka code roop:
  - chapter HTML fragment → well-formed XHTML (void tags self-close)
  - ser-watermark div (per-user mark) hata do — privacy
  - ser-inline scene-break logos → text divider (✦ ✦ ✦), 100% offline
  - mimetype first + ZIP_STORED
  - content.opf + nav.xhtml + toc.ncx + style.css + cover + chapters
"""
from __future__ import annotations

import html as html_lib
import re
import unicodedata
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from xml.dom import minidom

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr"}

# body ke andar allowed inline/block tags (baaki drop)
KEEP = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "strong", "b", "em", "i",
        "u", "br", "blockquote", "div", "span", "hr", "ul", "ol", "li"}

DIVIDER = '<p class="ser-div">✦ ✦ ✦</p>'

STYLE_CSS = """\
body{font-family:serif;line-height:1.7;margin:5%;}
p{margin:0 0 .8em;text-indent:1.2em;}
h1,h2,h3{text-indent:0;text-align:center;margin:1.2em 0 .6em;}
.ser-center{text-align:center;text-indent:0;}
.ser-div{display:block;text-align:center;letter-spacing:.3em;color:#888;margin:1.1em 0;text-indent:0;}
.ser-watermark{display:none !important;}
img{max-width:100%;height:auto;display:block;margin:.6em auto;}
"""


class _Norm(HTMLParser):
    """Chapter HTML fragment → clean XHTML. Watermark drop, logo→divider."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._skip_depth = 0        # ser-watermark jaise block skip karne ke liye

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if self._skip_depth:
            if tag not in VOID:
                self._skip_depth += 1
            return
        cls = a.get("class", "")
        if tag == "div" and "ser-watermark" in cls:
            self._skip_depth = 1
            return
        if tag == "img":
            # saari inline images branding logos hain → divider
            self.out.append(DIVIDER)
            return
        if tag not in KEEP:
            return
        kept = ""
        if "class" in a and a["class"]:
            safe = html_lib.escape(a["class"], quote=True)
            kept = f' class="{safe}"'
        if tag in VOID:
            self.out.append(f"<{tag}{kept}/>")
        else:
            self.out.append(f"<{tag}{kept}>")

    def handle_endtag(self, tag):
        if self._skip_depth:
            if tag not in VOID:
                self._skip_depth -= 1
            return
        if tag in VOID or tag not in KEEP:
            return
        self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if self._skip_depth:
            return
        self.out.append(html_lib.escape(data))


def normalize_html(fragment: str) -> str:
    p = _Norm()
    p.feed(fragment or "")
    p.close()
    body = "".join(p.out)
    # consecutive dividers collapse
    body = re.sub(r"(?:\s*" + re.escape(DIVIDER) + r"\s*){2,}", DIVIDER, body)
    # empty paragraphs hatao
    body = re.sub(r"<p[^>]*>\s*</p>", "", body)
    return body.strip()


def _xhtml_doc(title: str, body: str) -> str:
    safe_title = html_lib.escape(title or "")
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="hi" lang="hi">\n'
        f'<head><meta charset="utf-8"/><title>{safe_title}</title>'
        '<link rel="stylesheet" type="text/css" href="style.css"/></head>\n'
        f'<body>\n{body}\n</body>\n</html>\n'
    )


def _slugify(text: str, limit: int = 80) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"\s+", "_", text.strip())
    return (text or "book")[:limit]


def _validate_xml(name: str, data: str) -> None:
    minidom.parseString(data.encode("utf-8"))   # raise on malformed


class EpubBuilder:
    def __init__(self, meta: dict):
        self.meta = meta
        self.chapters: list[tuple[str, str]] = []   # (title, xhtml)
        self.cover_bytes: bytes = b""
        self.cover_ext: str = "webp"

    def add_cover(self, data: bytes, url: str = "") -> None:
        self.cover_bytes = data or b""
        if url:
            ext = Path(urlparse_path(url)).suffix.lstrip(".").lower()
            if ext in ("webp", "jpg", "jpeg", "png"):
                self.cover_ext = "jpg" if ext == "jpeg" else ext

    def add_chapter(self, title: str, html_fragment: str) -> None:
        body = normalize_html(html_fragment)
        self.chapters.append((title or f"Section {len(self.chapters)+1}", body))

    # ---- opf / nav / ncx ----

    def _manifest_items(self) -> list[str]:
        items = [
            '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
            '<item id="style" href="style.css" media-type="text/css"/>',
        ]
        if self.cover_bytes:
            mt = {"webp": "image/webp", "jpg": "image/jpeg",
                  "png": "image/png"}.get(self.cover_ext, "image/webp")
            items.append(f'<item id="cover-image" href="images/cover.{self.cover_ext}" '
                         f'media-type="{mt}" properties="cover-image"/>')
            items.append('<item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>')
        for i in range(len(self.chapters)):
            items.append(f'<item id="chap{i:03d}" href="chap{i:03d}.xhtml" '
                         f'media-type="application/xhtml+xml"/>')
        return items

    def _spine_items(self) -> list[str]:
        spine = []
        if self.cover_bytes:
            spine.append('<itemref idref="cover"/>')
        spine += [f'<itemref idref="chap{i:03d}"/>' for i in range(len(self.chapters))]
        return spine

    def _content_opf(self) -> str:
        m = self.meta
        esc = lambda s: html_lib.escape(str(s or ""))
        genres = "".join(f'<dc:subject>{esc(g)}</dc:subject>'
                         for g in (m.get("genres") or []))
        desc = m.get("description") or ""
        desc = re.sub(r"<[^>]+>", " ", desc)       # html strip
        desc = html_lib.unescape(desc)
        desc = re.sub(r"\s+", " ", desc).strip()
        cover_meta = '<meta name="cover" content="cover-image"/>' if self.cover_bytes else ""
        return (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
            'unique-identifier="bookid">\n'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
            f'<dc:identifier id="bookid">twbook-{esc(m.get("id"))}</dc:identifier>\n'
            f'<dc:title>{esc(m.get("title"))}</dc:title>\n'
            f'<dc:creator>{esc(m.get("author"))}</dc:creator>\n'
            '<dc:language>hi</dc:language>\n'
            f'<dc:description>{esc(desc)}</dc:description>\n'
            f'<dc:publisher>{esc(m.get("publisher") or "Thrill World")}</dc:publisher>\n'
            f'{genres}\n'
            f'{cover_meta}\n'
            '<meta property="dcterms:modified">2026-01-01T00:00:00Z</meta>\n'
            '</metadata>\n'
            '<manifest>\n' + "\n".join(self._manifest_items()) + '\n</manifest>\n'
            '<spine toc="ncx">\n' + "\n".join(self._spine_items()) + '\n</spine>\n'
            '</package>\n'
        )

    def _nav_xhtml(self) -> str:
        lis = "".join(
            f'<li><a href="chap{i:03d}.xhtml">{html_lib.escape(t)}</a></li>'
            for i, (t, _) in enumerate(self.chapters)
        )
        body = (f'<nav epub:type="toc" id="toc"><h1>Contents</h1><ol>{lis}</ol></nav>')
        return (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<!DOCTYPE html>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml" '
            'xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="hi">\n'
            '<head><meta charset="utf-8"/><title>Contents</title></head>\n'
            f'<body>\n{body}\n</body>\n</html>\n'
        )

    def _toc_ncx(self) -> str:
        pts = "".join(
            f'<navPoint id="n{i}" playOrder="{i+1}"><navLabel><text>'
            f'{html_lib.escape(t)}</text></navLabel>'
            f'<content src="chap{i:03d}.xhtml"/></navPoint>'
            for i, (t, _) in enumerate(self.chapters)
        )
        return (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
            f'<head><meta name="dtb:uid" content="twbook-{self.meta.get("id")}"/></head>\n'
            f'<docTitle><text>{html_lib.escape(str(self.meta.get("title") or ""))}</text></docTitle>\n'
            f'<navMap>{pts}</navMap>\n</ncx>\n'
        )

    def _cover_xhtml(self) -> str:
        body = (f'<div style="text-align:center;margin:0;padding:0;">'
                f'<img src="images/cover.{self.cover_ext}" alt="cover" '
                f'style="max-width:100%;height:auto;"/></div>')
        return _xhtml_doc(self.meta.get("title", "Cover"), body)

    # ---- build ----

    def build(self, out_path: Path) -> dict:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        container = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles>\n</container>\n'
        )
        opf = self._content_opf()
        nav = self._nav_xhtml()
        ncx = self._toc_ncx()

        # validate XML before zipping
        for nm, data in (("container.xml", container), ("content.opf", opf),
                         ("nav.xhtml", nav), ("toc.ncx", ncx)):
            _validate_xml(nm, data)

        chap_docs = []
        for i, (title, body) in enumerate(self.chapters):
            doc = _xhtml_doc(title, body)
            _validate_xml(f"chap{i:03d}.xhtml", doc)
            chap_docs.append(doc)

        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
            # mimetype FIRST + STORED
            zi = zipfile.ZipInfo("mimetype")
            zi.compress_type = zipfile.ZIP_STORED
            z.writestr(zi, "application/epub+zip")

            z.writestr("META-INF/container.xml", container)
            z.writestr("OEBPS/content.opf", opf)
            z.writestr("OEBPS/nav.xhtml", nav)
            z.writestr("OEBPS/toc.ncx", ncx)
            z.writestr("OEBPS/style.css", STYLE_CSS)
            if self.cover_bytes:
                z.writestr("OEBPS/cover.xhtml", self._cover_xhtml())
                z.writestr(f"OEBPS/images/cover.{self.cover_ext}", self.cover_bytes)
            for i, doc in enumerate(chap_docs):
                z.writestr(f"OEBPS/chap{i:03d}.xhtml", doc)

        dev = sum(sum(1 for c in body if "ऀ" <= c <= "ॿ")
                  for _, body in self.chapters)
        return {
            "path": str(out_path),
            "chapters": len(self.chapters),
            "cover": bool(self.cover_bytes),
            "devanagari_chars": dev,
            "size_kb": round(out_path.stat().st_size / 1024, 1),
        }


def urlparse_path(url: str) -> str:
    from urllib.parse import urlparse
    return urlparse(url).path


def safe_filename(title: str) -> str:
    return _slugify(title)
