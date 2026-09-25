"""Make a translated (single_translate, EN) epub fully English.

Body translation (--translate-tags) never touches: each file's <head><title>,
content.opf metadata (dc:title/description/subjects/language), toc.ncx
(docTitle/navLabels), or per-file npkr-title metas. This script syncs all of
them from the already-translated English body. Stdlib only.

Usage:
    python fix_en_metadata.py <translated_en.epub>

The file is updated in place (original kept once as .bak).
Rerun after the full-book translation; untranslated (still-Korean) chapters
keep their Korean labels until their bodies are translated.
"""

from __future__ import annotations

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
    ET.register_namespace("" if prefix == "x" and False else prefix, uri)
# Keep default-namespace documents default (avoid ns0: prefixes) by
# registering the empty prefix for each flavor when serializing manually.
ET.register_namespace("", XHTML)

# Conservative Korean tag map for dc:subject (novelpia genre/status tags).
# Unknown subjects are left untouched and reported.
SUBJECT_MAP = {
    "패러디": "Parody",
    "전생": "Reincarnation",
    "백합": "Yuri",
    "TS": "TS",
    "FATE": "FATE",
    "FGO": "FGO",
    "자유": "Free",
    "Status: 자유": "Status: Free",
}


def xhtml_text(elem) -> str:
    return "".join(elem.itertext()).strip()


def say(msg: str) -> None:
    try:
        sys.stdout.write(msg + "\n")
    except UnicodeEncodeError:
        sys.stdout.write(
            msg.encode("utf-8", "backslashreplace").decode("ascii", "replace")
            + "\n"
        )


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    except Exception:
        pass
    if len(sys.argv) != 2:
        say("usage: python fix_en_metadata.py <translated_en.epub>")
        return 1
    epub_path = Path(sys.argv[1])
    if not epub_path.is_file():
        say(f"not found: {epub_path}")
        return 1

    bak = epub_path.with_suffix(epub_path.suffix + ".bak")
    if not bak.is_file():
        shutil.copy2(epub_path, bak)
        say(f"backup: {bak.name}")

    with zipfile.ZipFile(epub_path, "r") as z:
        raw = {n: z.read(n) for n in z.namelist()}

    def parse_xhtml(name: str) -> ET.ElementTree:
        return ET.ElementTree(ET.fromstring(raw[name]))

    opf_name = next(n for n in raw if n.endswith("content.opf"))
    ncx_name = next(n for n in raw if n.endswith("toc.ncx"))
    opf = ET.ElementTree(ET.fromstring(raw[opf_name]))
    ncx = ET.ElementTree(ET.fromstring(raw[ncx_name]))

    # Spine order -> manifest hrefs.
    manifest = {
        it.get("id"): it.get("href")
        for it in opf.getroot().find(f"{{{OPF}}}manifest")
    }
    opf_dir = opf_name.rsplit("/", 1)[0]
    spine_ids = [
        it.get("idref")
        for it in opf.getroot().find(f"{{{OPF}}}spine")
    ]
    doc_names = [f"{opf_dir}/{manifest[i]}" for i in spine_ids if i in manifest]

    # Per-document English h1 (None when missing/still Korean-agnostic).
    h1_by_doc: dict[str, str] = {}
    for name in doc_names:
        if not name.endswith(("xhtml", "html")):
            continue
        try:
            tree = parse_xhtml(name)
        except ET.ParseError as e:
            say(f"  skip {name}: parse error {e}")
            continue
        root = tree.getroot()
        root.set("lang", "en")
        root.set("{http://www.w3.org/XML/1998/namespace}lang", "en")
        h1 = root.find(f".//{{{XHTML}}}h1")
        title_el = root.find(f".//{{{XHTML}}}title")
        h1_text = xhtml_text(h1) if h1 is not None else ""
        if h1_text:
            h1_by_doc[name] = h1_text
            if title_el is not None:
                title_el.text = h1_text
        for meta in root.findall(f".//{{{XHTML}}}meta[@name='npkr-title']"):
            if h1_text:
                meta.set("content", h1_text)
        raw[name] = ET.tostring(root, encoding="utf-8", xml_declaration=True)

    # Book title = title-page h1.
    book_title = next(
        (t for n, t in h1_by_doc.items() if "title-page" in n), ""
    )
    say(f"book title: {book_title!r}")

    # Synopsis = class-less <p> on the title page -> dc:description.
    synopsis = ""
    for name in doc_names:
        if "title-page" not in name:
            continue
        root = ET.fromstring(raw[name])
        paras = [
            xhtml_text(p)
            for p in root.findall(f".//{{{XHTML}}}p")
            if not (p.get("class") or "") and xhtml_text(p)
        ]
        synopsis = "\n\n".join(paras)
    say(f"synopsis paras: {len(synopsis.splitlines()) if synopsis else 0}")

    meta = opf.getroot().find(f"{{{OPF}}}metadata")
    changed_meta = []
    if book_title:
        for el in meta.findall(f"{{{DC}}}title"):
            el.text = book_title
            changed_meta.append("dc:title")
    for el in meta.findall(f"{{{DC}}}language"):
        if (el.text or "").strip().lower() != "en":
            el.text = "en"
            changed_meta.append("dc:language")
    if synopsis:
        for el in meta.findall(f"{{{DC}}}description"):
            el.text = synopsis
            changed_meta.append("dc:description")
    unmapped = []
    for el in meta.findall(f"{{{DC}}}subject"):
        cur = (el.text or "").strip()
        if cur in SUBJECT_MAP and cur != SUBJECT_MAP[cur]:
            el.text = SUBJECT_MAP[cur]
            changed_meta.append(f"dc:subject {cur!r}")
        elif cur not in SUBJECT_MAP.values() and any(
            "\uac00" <= c <= "\ud7a3" for c in cur
        ):
            unmapped.append(cur)
    raw[opf_name] = ET.tostring(opf.getroot(), encoding="utf-8", xml_declaration=True)
    say("opf updated: " + (", ".join(changed_meta) or "nothing"))
    if unmapped:
        say("opf subjects left Korean (no map): " + str(sorted(set(unmapped))))

    # NCX: docTitle + per-file navLabels from translated h1s.
    nroot = ncx.getroot()
    doc_title = nroot.find(f"{{{NCX}}}docTitle/{{{NCX}}}text")
    if doc_title is not None and book_title:
        doc_title.text = book_title
    nav_updated, nav_kept = 0, 0
    for np in nroot.findall(f".//{{{NCX}}}navPoint"):
        content = np.find(f"{{{NCX}}}content")
        label = np.find(f"{{{NCX}}}navLabel/{{{NCX}}}text")
        if content is None or label is None:
            continue
        src = content.get("src", "").split("#")[0]
        # content src is relative to the OPF dir already
        target = f"{opf_dir}/{src}"
        if target in h1_by_doc:
            label.text = h1_by_doc[target]
            nav_updated += 1
        else:
            nav_kept += 1
    raw[ncx_name] = ET.tostring(nroot, encoding="utf-8", xml_declaration=True)
    say(f"ncx labels: {nav_updated} synced from h1, {nav_kept} kept")

    with zipfile.ZipFile(epub_path, "w", zipfile.ZIP_DEFLATED) as z:
        for n, data in raw.items():
            z.writestr(n, data)
    say(f"wrote {epub_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
