"""Localize EPUB reading-system labels from the translations in the book."""

from __future__ import annotations

import posixpath
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup
from ebooklib import ITEM_DOCUMENT, epub

from book_maker.marker_bundle import (
    LOCALIZATION_KEY_ATTRIBUTE,
    BundleError,
)


def toc_entries(toc):
    for entry in toc:
        if isinstance(entry, tuple):
            yield from toc_entries([entry[0]])
            yield from toc_entries(entry[1])
        else:
            yield entry


def target(href, base=""):
    """Return a package-relative document and fragment for a TOC or nav href."""
    parsed = urlsplit(href or "")
    if parsed.scheme or parsed.netloc:
        return None
    path = unquote(parsed.path)
    if path:
        path = posixpath.normpath(posixpath.join(posixpath.dirname(base), path))
    else:
        path = base
    if path.startswith("../") or path == "..":
        return None
    return path, unquote(parsed.fragment)


def entry_target(entry):
    return target(getattr(entry, "href", None) or getattr(entry, "file_name", ""))


def bilingual(source, translated, single):
    source, translated = (source or "").strip(), (translated or "").strip()
    if not translated or source == translated:
        return source
    return translated if single else f"{source} / {translated}"


def heading(soup, fragment=""):
    """Find the body heading named by an EPUB navigation target."""
    if fragment:
        anchor = soup.find(id=fragment)
        if anchor is None:
            anchor = soup.find(attrs={"name": fragment})
        if anchor is not None:
            if anchor.name in ("h1", "h2", "h3"):
                return anchor
            nested = anchor.find(["h1", "h2", "h3"])
            if nested:
                return nested
            nxt = anchor.find_next(["h1", "h2", "h3"])
            if nxt:
                return nxt
        return None
    body = soup.body or soup
    return body.find(["h1", "h2", "h3"])


def _replace_link_text(link, value):
    """Replace an anchor's label while keeping its inline wrapper elements."""
    text_nodes = list(link.find_all(string=True))
    if not text_nodes:
        link.append(value)
        return
    text_nodes[0].replace_with(value)
    for text_node in text_nodes[1:]:
        text_node.extract()


def _sync_links(
    soup, file_name, translated_titles, translated_labels, single,
    allow_target_fallback=False, binding_keys=None, binding_values=None,
):
    """Copy approved navigation labels into links in any XHTML document.

    ``binding_keys`` maps a source identity to the stable localization key
    that will survive serialization.  Links that were localized in an earlier
    pass keep that key in an attribute, so a second pass after catalogue
    changes does not have to infer identity from the now-colliding display
    text.
    """
    changed = False
    for link in soup.find_all("a", href=True):
        key = target(link["href"], file_name)
        if key is None:
            continue
        tagged = link.get(LOCALIZATION_KEY_ATTRIBUTE)
        pair = None
        stable_key = None
        if tagged and binding_values and tagged in binding_values:
            pair = binding_values[tagged]
            stable_key = tagged
        else:
            current = link.get_text(" ", strip=True)
            pair = next(
                (
                    (source, translated)
                    for (label_key, source), translated in translated_labels.items()
                    if label_key == key
                    and current in (
                        source, translated, f"{source} {translated}",
                        bilingual(source, translated, single),
                    )
                ),
                None,
            )
            if pair is not None:
                stable_key = (binding_keys or {}).get((key, pair[0]))
            if pair is None and allow_target_fallback:
                pair = translated_titles.get(key)
                if pair is not None:
                    stable_key = (binding_keys or {}).get((key, pair[0]))
        if pair and pair[1]:
            label = bilingual(*pair, single)
            current = link.get_text(" ", strip=True)
            if current != label:
                _replace_link_text(link, label)
                changed = True
            if stable_key and link.get(LOCALIZATION_KEY_ATTRIBUTE) != stable_key:
                link[LOCALIZATION_KEY_ATTRIBUTE] = stable_key
                changed = True
    return changed


def collect_titles(book, plans):
    """Find TOC titles and their corresponding translated body jobs."""
    by_file = {plan.item.file_name: plan for plan in plans}
    result = []
    referenced = set()
    for entry in toc_entries(book.toc):
        source = (getattr(entry, "title", "") or "").strip()
        key = entry_target(entry)
        if not source or key is None:
            continue
        plan = by_file.get(key[0])
        if plan is None:
            continue
        node = heading(plan.soup, key[1])
        job = next((job for job in plan.jobs if job.node is node), None)
        result.append((entry, key, source, node, job))
        referenced.add(key[0])
    # EPUBs can have documents that never appear in the TOC, or no TOC at
    # all. Their reading-system titles still need localization.
    for plan in plans:
        if plan.item.file_name in referenced or isinstance(plan.item, epub.EpubNav):
            continue
        node = heading(plan.soup)
        source_node = plan.soup.title or node
        source = (
            source_node.get_text(" ", strip=True)
            if source_node is not None else getattr(plan.item, "title", "")
        )
        if source:
            job = next((job for job in plan.jobs if job.node is node), None)
            result.append((plan.item, (plan.item.file_name, ""), source, node, job))
    return result


def sync_documents(
    book, translated_titles, single, language, nav_labels=None, translated_files=None,
    translated_labels=None, binding_keys=None, nav_binding_keys=None,
    binding_values=None,
):
    """Sync approved labels into XHTML TOC links and EPUB navigation."""
    by_file = {}
    for key, (source, translated) in translated_titles.items():
        if translated:
            by_file.setdefault(key[0], (source, translated))
    for item in book.get_items_of_type(ITEM_DOCUMENT):
        pair = by_file.get(item.file_name)
        if isinstance(item, epub.EpubNav):
            continue
        soup = BeautifulSoup(item.content, "html.parser")
        changed = _sync_links(
            soup, item.file_name, translated_titles, translated_labels or {}, single,
            binding_keys=binding_keys, binding_values=binding_values,
        )
        label = bilingual(*pair, single) if pair else ""
        if label:
            if soup.head is None and soup.html is not None:
                soup.html.insert(0, soup.new_tag("head"))
            if soup.title is None and soup.head is not None:
                soup.head.append(soup.new_tag("title"))
            if soup.title is not None:
                if soup.title.get_text() != label:
                    soup.title.string = label
                    changed = True
        for meta in soup.find_all("meta", attrs={"name": "npkr-title"}):
            if label and meta.get("content") != label:
                meta["content"] = label
                changed = True
        file_selected = item.file_name in (translated_files or ())
        if (
            soup.html and language and single
            and (translated_files is None or file_selected)
        ):
            if (
                soup.html.get("lang") != language
                or soup.html.get("xml:lang") != language
            ):
                soup.html["lang"] = language
                soup.html["xml:lang"] = language
                changed = True
        if changed:
            item.content = soup.encode("utf-8")
    for item in book.get_items():
        if not isinstance(item, epub.EpubNav) or not item.content:
            continue
        soup = BeautifulSoup(item.content, "html.parser")
        changed = _sync_links(
            soup, item.file_name, translated_titles, translated_labels or {}, single,
            allow_target_fallback=True, binding_keys=binding_keys,
            binding_values=binding_values,
        )
        for node in soup.select("nav h2, nav li > span"):
            tagged = node.get(LOCALIZATION_KEY_ATTRIBUTE)
            pair = None
            stable_key = None
            if tagged and binding_values and tagged in binding_values:
                pair = binding_values[tagged]
                stable_key = tagged
            else:
                current = node.get_text(" ", strip=True)
                pair = next(
                    (
                        (source, translated)
                        for (file_name, source), translated in (nav_labels or {}).items()
                        if file_name == item.file_name
                        and current in (
                            source, translated, f"{source} {translated}",
                            bilingual(source, translated, single),
                        )
                    ),
                    None,
                )
                if pair is not None:
                    stable_key = (nav_binding_keys or {}).get(
                        (item.file_name, pair[0])
                    )
            if pair and not node.find("a"):
                label = bilingual(*pair, single)
                if node.get_text(" ", strip=True) != label:
                    node.string = label
                    changed = True
                if stable_key and node.get(LOCALIZATION_KEY_ATTRIBUTE) != stable_key:
                    node[LOCALIZATION_KEY_ATTRIBUTE] = stable_key
                    changed = True
        if changed and soup.title and book.title:
            soup.title.string = book.title
        if changed and soup.html and language and single:
            soup.html["lang"] = language
            soup.html["xml:lang"] = language
        if changed:
            item.content = soup.encode("utf-8")


def set_dc_values(book, name, translations, single):
    """Replace OPF text while keeping each value's attributes and order."""
    dc = epub.NAMESPACES["DC"]
    sources = getattr(book, "_bbm_metadata_sources", None)
    if sources is None:
        sources = book._bbm_metadata_sources = {}
    values = sources.setdefault(name, list(book.metadata.get(dc, {}).get(name, [])))
    if not values:
        return
    updated = []
    for index, (source, attrs) in enumerate(values):
        translated = translations.get((name, index), "")
        if name == "description":
            value = (translated if single else f"{source}\n\n{translated}") if translated else source
        else:
            value = bilingual(source, translated, single)
        updated.append((value, attrs))
    book.metadata[dc][name] = updated
    if name == "title":
        book.title = updated[0][0]


def apply_serialization_tokens(book, bindings):
    """Put opaque binding tokens into a disposable serialization copy.

    Returns a callable that restores every mutated value.  Display
    localization decisions are already complete; these tokens carry explicit
    source identities through ebooklib's generated nav/NCX/OPF output.
    """
    if bindings is None:
        return None
    bindings.prepare_nonce_for_book(book)
    originals = []

    def tokenize_tagged(soup):
        changed = False
        for element in soup.find_all(
            attrs={LOCALIZATION_KEY_ATTRIBUTE: True}
        ):
            key = element.get(LOCALIZATION_KEY_ATTRIBUTE)
            binding_id = bindings.id_for_key(key)
            if binding_id is None:
                raise BundleError(
                    "A localized element has no source binding; refusing to "
                    f"serialize {key!r}"
                )
            _replace_link_text(element, bindings.token(binding_id))
            del element[LOCALIZATION_KEY_ATTRIBUTE]
            changed = True
        return changed

    for item in book.get_items_of_type(ITEM_DOCUMENT):
        is_nav = isinstance(item, epub.EpubNav)
        file_binding = bindings.file.get(item.file_name) if not is_nav else None
        title_binding = (
            bindings.dc.get(("title", 0)) if is_nav else file_binding
        )
        if (
            title_binding is None
            and file_binding is None
            and not item.content
        ):
            continue
        soup = BeautifulSoup(item.content, "html.parser")
        changed = tokenize_tagged(soup)
        if title_binding is not None:
            token = bindings.token(title_binding)
            if soup.title is not None:
                soup.title.string = token
                changed = True
        if file_binding is not None:
            token = bindings.token(file_binding)
            for meta in soup.find_all("meta", attrs={"name": "npkr-title"}):
                meta["content"] = token
                changed = True
        if changed:
            originals.append((item, "content", item.content))
            item.content = soup.encode("utf-8")

    for node in toc_entries(book.toc):
        binding_id = bindings.toc.get(id(node))
        if binding_id is None:
            continue
        originals.append((node, "title", getattr(node, "title", None)))
        node.title = bindings.token(binding_id)

    dc = epub.NAMESPACES["DC"]
    for (name, index), binding_id in bindings.dc.items():
        entries = book.metadata.get(dc, {}).get(name, [])
        if index >= len(entries):
            continue
        originals.append((entries, index, entries[index]))
        value, attrs = entries[index]
        entries[index] = (bindings.token(binding_id), attrs)

    title_binding = bindings.dc.get(("title", 0))
    if title_binding is not None:
        originals.append((book, "title", book.title))
        book.title = bindings.token(title_binding)

    def restore():
        for target_object, attribute, value in reversed(originals):
            if isinstance(target_object, list):
                target_object[attribute] = value
            else:
                setattr(target_object, attribute, value)

    return restore
