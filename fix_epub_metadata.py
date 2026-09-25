"""Make a translated EPUB fully localized (Metadata, Title, and TOC).

Synchronizes:
- Chapter titles in <head><title>
- Table of Contents in toc.ncx (<docTitle>, <navLabel><text>)
- Table of Contents in nav.xhtml (<nav> <h2>, <span> volume labels, <a> chapter links)
- EPUB metadata in content.opf (<dc:title>, <dc:language>, <dc:description>, <dc:creator>)
- Document root <html lang="..."> attributes

Supports:
- Thai (default: th), English (en), or any target language
- Ciweimao, Novelpia, and standard web novel EPUB structures
- Both --single_translate and bilingual EPUBs

Usage:
    python fix_epub_metadata.py <translated_book.epub> [--lang th] [--title "Book Title"]
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import zipfile
from pathlib import Path
import xml.etree.ElementTree as ET

XHTML = "http://www.w3.org/1999/xhtml"
OPF = "http://www.idpf.org/2007/opf"
DC = "http://purl.org/dc/elements/1.1/"
NCX = "http://www.daisy.org/z3986/2005/ncx/"

for prefix, uri in (("x", XHTML), ("o", OPF), ("d", DC), ("n", NCX)):
    ET.register_namespace(prefix, uri)
ET.register_namespace("", XHTML)

# Chinese number translation helper for Volume names
CN_NUMS = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
    "十一": 11, "十二": 12, "十三": 13, "十四": 14, "十五": 15,
    "十六": 16, "十七": 17, "十八": 18, "十九": 19, "二十": 20,
    "百": 100,
}

COMMON_SECTION_TH = {
    "首页": "หน้าแรก",
    "前夕": "ก่อนรุ่งอรุณ",
    "希兰大革命": "การปฏิวัติใหญ่แห่งซีลาน",
    "红色孤岛": "เกาะโดดเดี่ยวสีแดง",
    "代理人战争": "สงครามตัวแทน",
    "终章": "บทส่งท้าย",
    "番外": "ตอนพิเศษ",
    "上架感言": "ประกาศจากผู้เขียน",
}

COMMON_SECTION_EN = {
    "首页": "Home",
    "前夕": "Eve",
    "终章": "Epilogue",
    "番外": "Extra",
    "上架感言": "Author's Note",
}


def cn_to_int(cn_str: str) -> int:
    val = 0
    if cn_str in CN_NUMS:
        return CN_NUMS[cn_str]
    for ch in cn_str:
        if ch in CN_NUMS:
            val = val * 10 + CN_NUMS[ch] if val else CN_NUMS[ch]
    return val if val else 1


def translate_volume_label(label: str, lang: str = "th") -> str:
    """Translate Volume / Section labels like '第一卷：前夕'."""
    text = label.strip()
    if lang == "th" and text in COMMON_SECTION_TH:
        return COMMON_SECTION_TH[text]
    if lang == "en" and text in COMMON_SECTION_EN:
        return COMMON_SECTION_EN[text]

    # Pattern: 第X卷：Title
    m = re.match(r"^第([一二三四五六七八九十\d]+)卷(?:[：:\s]*(.*))?$", text)
    if m:
        num_str = m.group(1)
        sub_title = (m.group(2) or "").strip()
        num = int(num_str) if num_str.isdigit() else cn_to_int(num_str)

        if lang == "th":
            trans_sub = COMMON_SECTION_TH.get(sub_title, sub_title)
            return f"เล่มที่ {num}: {trans_sub}" if trans_sub else f"เล่มที่ {num}"
        else:
            trans_sub = COMMON_SECTION_EN.get(sub_title, sub_title)
            return f"Volume {num}: {trans_sub}" if trans_sub else f"Volume {num}"

    return text


def has_cjk(text: str) -> bool:
    return any(
        "\u4e00" <= c <= "\u9fff" or "\u3040" <= c <= "\u30ff" or "\uac00" <= c <= "\ud7af"
        for c in text
    )


def has_thai(text: str) -> bool:
    return any("\u0e00" <= c <= "\u0e7f" for c in text)


def extract_best_title(headings: list[str], lang: str = "th") -> str:
    """Pick the translated title from a list of heading texts."""
    if not headings:
        return ""
    if lang == "th":
        for h in reversed(headings):
            if has_thai(h):
                return h
    elif lang == "en":
        for h in reversed(headings):
            if not has_cjk(h) and len(h.strip()) > 0:
                return h
    # Fallback to the last heading
    return headings[-1]


def xhtml_text(elem) -> str:
    return "".join(elem.itertext()).strip()


def fix_epub_metadata(
    epub_path: Path,
    lang: str = "th",
    custom_title: str | None = None,
    custom_author: str | None = None,
) -> int:
    if not epub_path.is_file():
        print(f"File not found: {epub_path}")
        return 1

    bak = epub_path.with_suffix(epub_path.suffix + ".bak")
    if not bak.is_file():
        shutil.copy2(epub_path, bak)
        print(f"📦 Backup created: {bak.name}")

    with zipfile.ZipFile(epub_path, "r") as z:
        raw = {n: z.read(n) for n in z.namelist()}

    opf_name = next((n for n in raw if n.endswith("content.opf")), None)
    if not opf_name:
        print("❌ content.opf not found in EPUB")
        return 1

    opf = ET.ElementTree(ET.fromstring(raw[opf_name]))
    opf_dir = opf_name.rsplit("/", 1)[0] if "/" in opf_name else ""

    # Spine order -> manifest hrefs
    manifest = {
        it.get("id"): it.get("href")
        for it in opf.getroot().find(f"{{{OPF}}}manifest")
    }
    spine_ids = [
        it.get("idref")
        for it in opf.getroot().find(f"{{{OPF}}}spine")
    ]
    doc_names = [
        f"{opf_dir}/{manifest[i]}" if opf_dir else manifest[i]
        for i in spine_ids
        if i in manifest
    ]

    h1_by_doc: dict[str, str] = {}
    title_page_doc = ""

    # Parse and update all document xhtml files
    for name in doc_names:
        if not name.endswith((".xhtml", ".html")):
            continue
        try:
            tree = ET.ElementTree(ET.fromstring(raw[name]))
        except ET.ParseError as e:
            continue

        root = tree.getroot()
        root.set("lang", lang)
        root.set("{http://www.w3.org/XML/1998/namespace}lang", lang)

        # Find h1, h2 tags
        h_elements = root.findall(f".//{{{XHTML}}}h1") + root.findall(f".//{{{XHTML}}}h2")
        h_texts = [xhtml_text(h) for h in h_elements if xhtml_text(h)]
        chosen_title = extract_best_title(h_texts, lang)

        if chosen_title:
            h1_by_doc[name] = chosen_title
            # Update <head><title>
            title_el = root.find(f".//{{{XHTML}}}title")
            if title_el is not None:
                title_el.text = chosen_title

        # Check if this is the title/cover page
        if not title_page_doc and ("chap_1" in name or "title" in name or "cover" in name):
            # Check if there is an h1 or title text
            if chosen_title:
                title_page_doc = name

        raw[name] = ET.tostring(root, encoding="utf-8", xml_declaration=True)

    # Determine book title
    book_title = custom_title
    if not book_title and title_page_doc and title_page_doc in h1_by_doc:
        book_title = h1_by_doc[title_page_doc]
    if not book_title:
        # Fallback to the first found doc title
        for n in doc_names:
            if n in h1_by_doc:
                book_title = h1_by_doc[n]
                break

    print(f"📖 Translated Book Title: {book_title!r}")

    # Update content.opf metadata
    meta = opf.getroot().find(f"{{{OPF}}}metadata")
    if meta is not None:
        if book_title:
            for el in meta.findall(f"{{{DC}}}title"):
                el.text = book_title
        for el in meta.findall(f"{{{DC}}}language"):
            el.text = lang
        if custom_author:
            for el in meta.findall(f"{{{DC}}}creator"):
                el.text = custom_author

        raw[opf_name] = ET.tostring(opf.getroot(), encoding="utf-8", xml_declaration=True)
        print(f"✅ content.opf updated (lang={lang}, title={book_title})")

    # Update toc.ncx (EPUB 2)
    ncx_name = next((n for n in raw if n.endswith("toc.ncx")), None)
    if ncx_name:
        ncx = ET.ElementTree(ET.fromstring(raw[ncx_name]))
        nroot = ncx.getroot()

        # Update docTitle
        doc_title = nroot.find(f"{{{NCX}}}docTitle/{{{NCX}}}text")
        if doc_title is not None and book_title:
            doc_title.text = book_title

        ncx_updated = 0
        for np in nroot.findall(f".//{{{NCX}}}navPoint"):
            label = np.find(f"{{{NCX}}}navLabel/{{{NCX}}}text")
            content = np.find(f"{{{NCX}}}content")
            if label is None:
                continue

            current_text = (label.text or "").strip()
            # If it's a volume/section header (has sub navPoints or starts with 第...卷)
            if np.find(f"{{{NCX}}}navPoint") is not None or "卷" in current_text or "首页" in current_text:
                new_vol = translate_volume_label(current_text, lang)
                if new_vol != current_text:
                    label.text = new_vol
                    ncx_updated += 1
                continue

            if content is not None:
                src = content.get("src", "").split("#")[0]
                target = f"{opf_dir}/{src}" if opf_dir else src
                if target in h1_by_doc:
                    label.text = h1_by_doc[target]
                    ncx_updated += 1

        raw[ncx_name] = ET.tostring(nroot, encoding="utf-8", xml_declaration=True)
        print(f"✅ toc.ncx updated: {ncx_updated} navigation labels synced")

    # Update nav.xhtml (EPUB 3)
    nav_name = next((n for n in raw if n.endswith("nav.xhtml")), None)
    if nav_name:
        try:
            nav = ET.ElementTree(ET.fromstring(raw[nav_name]))
            nav_root = nav.getroot()
            nav_root.set("lang", lang)
            nav_root.set("{http://www.w3.org/XML/1998/namespace}lang", lang)

            # Update nav title / h2
            h2 = nav_root.find(f".//{{{XHTML}}}nav//{{{XHTML}}}h2")
            if h2 is not None and book_title:
                h2.text = book_title

            nav_updated = 0
            # Update Volume spans
            for span in nav_root.findall(f".//{{{XHTML}}}span"):
                stext = xhtml_text(span)
                new_v = translate_volume_label(stext, lang)
                if new_v != stext:
                    span.text = new_v
                    nav_updated += 1

            # Update chapter <a> links
            for a in nav_root.findall(f".//{{{XHTML}}}a"):
                href = a.get("href", "").split("#")[0]
                target = f"{opf_dir}/{href}" if opf_dir else href
                if target in h1_by_doc:
                    a.text = h1_by_doc[target]
                    nav_updated += 1

            raw[nav_name] = ET.tostring(nav_root, encoding="utf-8", xml_declaration=True)
            print(f"✅ nav.xhtml updated: {nav_updated} links/spans synced")
        except Exception as e:
            print(f"⚠️ nav.xhtml warning: {e}")

    # Write back EPUB ensuring mimetype is first and uncompressed
    with zipfile.ZipFile(epub_path, "w") as z:
        if "mimetype" in raw:
            z.writestr("mimetype", raw["mimetype"], compress_type=zipfile.ZIP_STORED)
        for n, data in raw.items():
            if n != "mimetype":
                z.writestr(n, data, compress_type=zipfile.ZIP_DEFLATED)

    print(f"🎉 Fully localized EPUB ready: {epub_path.name}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Sync EPUB metadata, TOC, and titles to translated language.")
    parser.add_argument("epub", help="Path to the translated EPUB file")
    parser.add_argument("--lang", default="th", help="Target language code (e.g. th, en; default: th)")
    parser.add_argument("--title", default=None, help="Explicit translated book title override")
    parser.add_argument("--author", default=None, help="Explicit translated author override")

    args = parser.parse_args()
    sys.exit(fix_epub_metadata(Path(args.epub), lang=args.lang, custom_title=args.title, custom_author=args.author))


if __name__ == "__main__":
    main()
