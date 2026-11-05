"""Atomic EPUB bundle storage, marker rendering, and CSS generation.

The authoritative catalogue and all resumable run state live in one JSON
resource inside the output EPUB (``META-INF/bbm-marker-state.json``).  CSS is
generated from that state, never maintained as a second mapping database.

Assembly happens in the OS temporary directory.  A short-lived copy on the
destination volume is used only for the final atomic ``os.replace``.
"""

from __future__ import annotations

import json
import os
import posixpath
import re
import shutil
import tempfile
from collections import Counter
from hashlib import sha256
from pathlib import Path
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

from lxml import etree

from book_maker.marker_names import (
    MARKER,
    MarkerError,
    WORKFLOW,
    WINDOW_WORKFLOW,
    display_text,
    marker_key,
    markers,
    marker_parts,
    validate_display_target,
    validate_summary,
)


STATE_PATH = "META-INF/bbm-marker-state.json"
CSS_NAME = "bbm-names.css"
SCHEMA_VERSION = 1
WINDOW_SCHEMA_VERSION = 2
LOCALIZATION_VERSION = 3
LEGACY_LOCALIZATION_VERSION = 2
CONTAINER_PATH = "META-INF/container.xml"
MIMETYPE = "application/epub+zip"
_XHTML_SUFFIXES = (".xhtml", ".html", ".htm")
_OPF_NS = "http://www.idpf.org/2007/opf"
_DC_NS = "http://purl.org/dc/elements/1.1/"
_XHTML_NS = "http://www.w3.org/1999/xhtml"
_TOKEN_PREFIX = "BBMLOC"
_GENERIC_TOKEN_RE = re.compile(r"BBMLOC[0-9a-f]{8,}X\d+Z")
LOCALIZATION_KEY_ATTRIBUTE = "data-bbm-loc"

_MARKER_BYTES = b"${"
_SPAN_BYTES = b"bbm-name"


class BundleError(ValueError):
    """The EPUB bundle is malformed or failed verification."""


# ---------------------------------------------------------------- JSON state


def state_bytes(state):
    return json.dumps(
        state, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


def read_state(epub_path, validate=False):
    with ZipFile(epub_path) as archive:
        try:
            data = archive.read(STATE_PATH)
        except KeyError as error:
            raise BundleError(f"Missing embedded marker state in {epub_path}") from error
    try:
        state = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BundleError(f"Corrupt embedded marker state in {epub_path}") from error
    if validate:
        validate_state(state)
    return state


def has_state(epub_path):
    try:
        with ZipFile(epub_path) as archive:
            return STATE_PATH in archive.namelist()
    except (OSError, ValueError):
        return False


def _validate_recovery_journal(state):
    """Validate the optional accepted-paragraph journal.

    Old bundles simply omit it.  The journal is temporary: entries disappear
    once their identity appears in the accepted checkpoint, but while a batch
    is incomplete it is the only record of paragraphs accepted before the
    original chunk returned.
    """
    journal = state.get("paragraph_recovery")
    if journal is None:
        return
    if not isinstance(journal, dict):
        raise BundleError("Marker paragraph recovery journal is not an object")
    if journal.get("version") != 1:
        raise BundleError(
            f"Unsupported paragraph recovery version {journal.get('version')!r}"
        )
    entries = journal.get("entries")
    if not isinstance(entries, list):
        raise BundleError("Marker paragraph recovery entries are malformed")
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise BundleError("Marker paragraph recovery entry is not an object")
        identity = entry.get("identity")
        if not isinstance(identity, str) or not identity:
            raise BundleError("Marker paragraph recovery entry has no identity")
        digest = entry.get("source_sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise BundleError("Marker paragraph recovery entry has no source digest")
        if not isinstance(entry.get("source"), str):
            raise BundleError("Marker paragraph recovery entry has no source text")
        if sha256(entry["source"].encode("utf-8")).hexdigest() != digest:
            raise BundleError(
                "Marker paragraph recovery source digest does not match its text"
            )
        if not isinstance(entry.get("master"), str):
            raise BundleError("Marker paragraph recovery entry has no accepted text")
        index = entry.get("index")
        if not isinstance(index, int) or index < 0:
            raise BundleError("Marker paragraph recovery entry has no original index")
        request_digest = entry.get("request_sha256")
        if request_digest is not None and (
            not isinstance(request_digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", request_digest)
        ):
            raise BundleError("Marker paragraph recovery request digest is malformed")
        kind = entry.get("kind")
        if kind is not None and kind not in ("body", "localization"):
            raise BundleError("Marker paragraph recovery kind is malformed")
        key = (identity, digest)
        if key in seen:
            raise BundleError("Marker paragraph recovery has a duplicate entry")
        seen.add(key)


def validate_state(state):
    """Validate the schema, types, and catalogue keys of marker state."""
    if not isinstance(state, dict):
        raise BundleError("Marker state must be a JSON object")
    workflow = state.get("workflow")
    expected_schema = (
        WINDOW_SCHEMA_VERSION if workflow == WINDOW_WORKFLOW else SCHEMA_VERSION
    )
    if workflow not in (WORKFLOW, WINDOW_WORKFLOW):
        raise BundleError(f"Unsupported marker workflow {workflow!r}")
    if state.get("schema_version") != expected_schema:
        raise BundleError(
            f"Unsupported marker state schema {state.get('schema_version')!r}"
        )
    run = state.get("run")
    if not isinstance(run, dict) or not isinstance(run.get("source_sha256"), str):
        raise BundleError("Marker state is missing its source fingerprint")
    if not isinstance(run.get("run_fingerprint"), str):
        raise BundleError("Marker state is missing its run fingerprint")
    checkpoint = state.get("checkpoint")
    if not isinstance(checkpoint, dict):
        raise BundleError("Marker state is missing its checkpoint")
    translations = checkpoint.get("translations")
    job_ids = checkpoint.get("accepted_job_ids")
    if not isinstance(translations, list) or not isinstance(job_ids, list):
        raise BundleError("Marker checkpoint translations/job ids are malformed")
    if len(translations) != len(job_ids):
        raise BundleError("Marker checkpoint translations and job ids differ in length")
    if any(not isinstance(value, str) for value in translations):
        raise BundleError("Marker checkpoint contains a non-text translation")
    catalogue = state.get("catalogue")
    if not isinstance(catalogue, dict):
        raise BundleError("Marker state is missing its catalogue")
    for label, entry in catalogue.items():
        if not isinstance(label, str) or not label:
            raise BundleError("Catalogue label is malformed")
        if not isinstance(entry, dict):
            raise BundleError(f"Catalogue entry for {label!r} is not an object")
        expected = marker_key(label)
        if entry.get("key") != expected or entry.get("label") != label:
            raise BundleError(f"Catalogue identity is inconsistent for {label!r}")
        target = entry.get("target")
        if target is not None:
            try:
                validate_display_target(target)
            except MarkerError as error:
                raise BundleError(
                    f"Invalid display target for {label!r}: {error}"
                ) from error
        if not isinstance(entry.get("count"), int) or entry["count"] < 0:
            raise BundleError(f"Catalogue count is invalid for {label!r}")
        if workflow == WINDOW_WORKFLOW:
            extracted = entry.get("extracted_from", [])
            if not isinstance(extracted, list):
                raise BundleError(
                    f"Catalogue extraction provenance is invalid for {label!r}"
                )
            for provenance in extracted:
                if not isinstance(provenance, dict):
                    raise BundleError(
                        f"Catalogue extraction provenance is malformed for {label!r}"
                    )
                if not isinstance(provenance.get("window_id"), str) or not isinstance(
                    provenance.get("unit_ids"), list
                ) or not isinstance(provenance.get("occurrences"), list):
                    raise BundleError(
                        f"Catalogue extraction provenance is incomplete for {label!r}"
                    )
    if workflow == WINDOW_WORKFLOW:
        windows = state.get("source_windows")
        if not isinstance(windows, dict) or windows.get("schema_version") != 1:
            raise BundleError("Source-window recovery state is missing or unsupported")
        if not isinstance(windows.get("name_window_tokens"), int) or windows["name_window_tokens"] < 1:
            raise BundleError("Source-window name budget is malformed")
        if not isinstance(windows.get("source_summary_tokens"), int) or windows["source_summary_tokens"] < 1:
            raise BundleError("Source-window summary budget is malformed")
        if not isinstance(windows.get("current_summary", ""), str):
            raise BundleError("Source-window cumulative summary is malformed")
        try:
            validate_summary(
                windows.get("current_summary", ""),
                catalogue.keys(),
                check_plain_labels=False,
            ) if windows.get("current_summary") else None
        except MarkerError as error:
            raise BundleError(
                f"Source-window cumulative summary is invalid: {error}"
            ) from error
        plan = windows.get("plan", [])
        records = windows.get("windows", [])
        auxiliary = windows.get("auxiliary", [])
        if not isinstance(plan, list) or not isinstance(records, list) or not isinstance(auxiliary, list):
            raise BundleError("Source-window plan or records are malformed")
        plan_ids = set()
        plan_by_id = {}
        for row in plan:
            if not isinstance(row, dict) or not isinstance(row.get("window_id"), str):
                raise BundleError("Source-window plan row is malformed")
            if row["window_id"] in plan_ids:
                raise BundleError("Source-window plan has duplicate window IDs")
            plan_ids.add(row["window_id"])
            unit_ids = row.get("unit_ids")
            source_digest = row.get("source_sha256")
            if (
                not isinstance(unit_ids, list)
                or any(not isinstance(identity, str) or not identity for identity in unit_ids)
                or len(unit_ids) != len(set(unit_ids))
                or not isinstance(source_digest, str)
                or not re.fullmatch(r"[0-9a-f]{64}", source_digest)
            ):
                raise BundleError("Source-window plan row has no source coverage")
            plan_by_id[row["window_id"]] = row
        record_ids = set()
        for record in records:
            if not isinstance(record, dict) or record.get("window_id") not in plan_ids:
                raise BundleError("Prepared source-window record is not in the frozen plan")
            if record["window_id"] in record_ids:
                raise BundleError("Source-window records contain a duplicate window")
            record_ids.add(record["window_id"])
            if record.get("status") not in ("prepared", "translating", "summary_pending", "complete"):
                raise BundleError("Source-window status is invalid")
            if not isinstance(record.get("source_sha256"), str) or not isinstance(record.get("units"), list):
                raise BundleError("Source-window record has no source membership")
            if not re.fullmatch(r"[0-9a-f]{64}", record["source_sha256"]):
                raise BundleError("Source-window source digest is malformed")
            plan_row = plan_by_id[record["window_id"]]
            if (
                record["source_sha256"] != plan_row["source_sha256"]
                or [unit.get("unit_id") for unit in record["units"] if isinstance(unit, dict)]
                != plan_row["unit_ids"]
            ):
                raise BundleError(
                    "Prepared source-window membership differs from its frozen plan"
                )
            if not isinstance(record.get("extraction_request_sha256"), str) or not re.fullmatch(
                r"[0-9a-f]{64}", record["extraction_request_sha256"]
            ):
                raise BundleError("Source-window extraction request fingerprint is malformed")
            incoming = record.get("incoming_summary")
            if (
                not isinstance(incoming, str)
                or record.get("incoming_summary_sha256")
                != sha256(incoming.encode("utf-8")).hexdigest()
            ):
                raise BundleError("Source-window incoming summary digest is malformed")
            extracted_names = record.get("extracted_names")
            if not isinstance(extracted_names, list) or any(
                not isinstance(label, str) or not label for label in extracted_names
            ):
                raise BundleError("Source-window extracted name collection is malformed")
            unit_ids = set()
            for unit in record["units"]:
                if not isinstance(unit, dict) or not isinstance(unit.get("unit_id"), str):
                    raise BundleError("Prepared source-window unit is malformed")
                if unit["unit_id"] in unit_ids:
                    raise BundleError("Prepared source-window unit ID is duplicated")
                unit_ids.add(unit["unit_id"])
                if not isinstance(unit.get("prepared"), str) or not isinstance(unit.get("spans"), list):
                    raise BundleError("Prepared source-window unit has no bracket record")
                if not isinstance(unit.get("source_sha256"), str) or not re.fullmatch(
                    r"[0-9a-f]{64}", unit["source_sha256"]
                ):
                    raise BundleError("Prepared source-window unit source digest is malformed")
                try:
                    expected_markers = Counter(markers(unit["prepared"]))
                    marker_rows = unit.get("expected_markers")
                    if not isinstance(marker_rows, list) or any(
                        not isinstance(label, str) or not label for label in marker_rows
                    ):
                        raise TypeError("expected marker list is malformed")
                    saved_markers = Counter(marker_rows)
                except (MarkerError, TypeError) as error:
                    raise BundleError(
                        "Prepared source-window markers are malformed"
                    ) from error
                if expected_markers != saved_markers:
                    raise BundleError(
                        "Prepared source-window expected markers do not match its text"
                    )
                if "translation" in unit and not isinstance(unit["translation"], str):
                    raise BundleError("Accepted source-window translation is not text")
                if isinstance(unit.get("translation"), str):
                    try:
                        actual_markers = Counter(markers(unit["translation"]))
                    except MarkerError as error:
                        raise BundleError(
                            "Accepted source-window translation has malformed markers"
                        ) from error
                    if actual_markers != saved_markers:
                        raise BundleError(
                            "Accepted source-window translation does not preserve its "
                            "prepared marker multiset"
                        )
                if not isinstance(unit.get("accepted"), bool):
                    raise BundleError("Source-window accepted marker is malformed")
                if unit["accepted"] != isinstance(unit.get("translation"), str):
                    raise BundleError(
                        "Source-window accepted flag and translation disagree"
                    )
            accepted = record.get("accepted_unit_ids", [])
            if (
                not isinstance(accepted, list)
                or any(
                    not isinstance(identity, str) or identity not in unit_ids
                    for identity in accepted
                )
                or len(accepted) != len(set(accepted))
                or set(accepted)
                != {
                    unit["unit_id"]
                    for unit in record["units"]
                    if unit.get("accepted") is True
                }
            ):
                raise BundleError("Source-window accepted unit progress is malformed")
            if record.get("status") == "complete":
                if set(accepted) != unit_ids:
                    raise BundleError("Completed source window still has unaccepted units")
                if not isinstance(record.get("outgoing_summary"), str) or not record["outgoing_summary"]:
                    raise BundleError("Completed source window has no validated summary")
                if not isinstance(record.get("summary_validation"), dict):
                    raise BundleError("Completed source window has no summary validation metadata")
                validation = record["summary_validation"]
                if (
                    validation.get("version") != 1
                    or validation.get("policy")
                    not in (None, "plain-labels-allowed-v1")
                    or not isinstance(validation.get("language"), str)
                    or not isinstance(validation.get("budget_tokens"), int)
                    or not isinstance(validation.get("estimated_tokens"), int)
                    or validation["estimated_tokens"] > validation["budget_tokens"]
                    or not isinstance(validation.get("input_sha256"), str)
                    or not re.fullmatch(r"[0-9a-f]{64}", validation["input_sha256"])
                    or not isinstance(validation.get("request_sha256"), str)
                    or not re.fullmatch(r"[0-9a-f]{64}", validation["request_sha256"])
                ):
                    raise BundleError("Completed source-window summary metadata is malformed")
                try:
                    validate_summary(record["outgoing_summary"], catalogue.keys(), check_plain_labels=False)
                except MarkerError as error:
                    raise BundleError(
                        f"Completed source-window summary is invalid: {error}"
                    ) from error
        for row in auxiliary:
            if not isinstance(row, dict) or not isinstance(row.get("identity"), str):
                raise BundleError("Auxiliary source extraction record is malformed")
            if not isinstance(row.get("source_sha256"), str) or not isinstance(row.get("prepared"), str):
                raise BundleError("Auxiliary source extraction has no source identity")
            if "translation" in row and not isinstance(row["translation"], str):
                raise BundleError("Auxiliary source translation is not text")
    session = state.get("session", {})
    if not isinstance(session, dict):
        raise BundleError("Marker session state is not an object")
    context_validation = session.get("context_validation")
    if context_validation is not None and not isinstance(
        context_validation, dict
    ):
        raise BundleError("Marker context validation record is malformed")
    if (
        isinstance(context_validation, dict)
        and not isinstance(context_validation.get("version"), int)
    ):
        raise BundleError("Marker context validation record has no version")
    context_repair = session.get("context_repair")
    if context_repair is not None and not isinstance(context_repair, dict):
        raise BundleError("Marker context repair record is malformed")
    if isinstance(context_repair, dict) and not isinstance(
        context_repair.get("pending"), bool
    ):
        raise BundleError("Marker context repair record has no pending flag")
    mapping = state.get("mapping", {})
    if not isinstance(mapping, dict):
        raise BundleError("Marker mapping state is not an object")
    localization = state.get("localization")
    if localization is not None:
        if not isinstance(localization, dict):
            raise BundleError("Marker localization state is not an object")
        version = localization.get("version")
        if version not in (None, 1, LEGACY_LOCALIZATION_VERSION, LOCALIZATION_VERSION):
            raise BundleError(
                f"Unsupported marker localization version {version!r}"
            )
        destinations = localization.get("destinations", [])
        if not isinstance(destinations, list):
            raise BundleError("Marker localization destinations are malformed")
        for record in destinations:
            if not isinstance(record, dict):
                raise BundleError("Localization destination is not an object")
            if not isinstance(record.get("resource"), str) or not record["resource"]:
                raise BundleError("Localization destination has no resource")
            path = record.get("path")
            if not isinstance(path, list) or any(
                not isinstance(index, int) or index < 0 for index in path
            ):
                raise BundleError("Localization destination path is malformed")
            if not isinstance(record.get("element"), str) or not record["element"]:
                raise BundleError("Localization destination has no expected element")
            destination = record.get("destination")
            if version == LOCALIZATION_VERSION:
                if destination not in ("text", "tail", "attribute"):
                    raise BundleError("Localization destination type is malformed")
            elif destination not in ("text", "attribute"):
                raise BundleError("Localization destination type is malformed")
            if destination == "attribute" and not isinstance(
                record.get("attribute"), str
            ):
                raise BundleError("Attribute destination has no attribute name")
            if not isinstance(record.get("source"), str) or not isinstance(
                record.get("master"), str
            ):
                raise BundleError("Localization destination has no master text")
            if not isinstance(record.get("single"), bool):
                raise BundleError("Localization destination has no presentation mode")
    _validate_recovery_journal(state)
    return state


# ------------------------------------------------------------- EPUB helpers


def _read_opf(archive):
    try:
        container = etree.fromstring(archive.read(CONTAINER_PATH))
    except (KeyError, etree.XMLSyntaxError) as error:
        raise BundleError("EPUB container.xml is missing or malformed") from error
    rootfile = container.find(
        ".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile"
    )
    if rootfile is None:
        rootfile = container.find(".//rootfile")
    if rootfile is None or not rootfile.get("full-path"):
        raise BundleError("EPUB container.xml names no package document")
    opf_path = rootfile.get("full-path")
    try:
        opf_data = archive.read(opf_path)
    except KeyError as error:
        raise BundleError(f"EPUB package document {opf_path} is missing") from error
    try:
        opf = etree.fromstring(opf_data)
    except etree.XMLSyntaxError as error:
        raise BundleError(f"EPUB package document {opf_path} is malformed") from error
    return opf_path, opf, opf_data


def _css_path_for(opf_path):
    return posixpath.normpath(
        posixpath.join(posixpath.dirname(opf_path), CSS_NAME)
    )


def _manifest_css_item(opf):
    manifest = opf.find(f"{{{_OPF_NS}}}manifest")
    if manifest is None:
        raise BundleError("EPUB package document has no manifest")
    for item in manifest.findall(f"{{{_OPF_NS}}}item"):
        href = item.get("href") or ""
        if posixpath.basename(href) == CSS_NAME:
            return manifest, item
    return manifest, None


def _ensure_css_manifest(opf, opf_path):
    manifest, item = _manifest_css_item(opf)
    if item is not None:
        # Reuse the item but keep it pointing at the single generated file.
        item.set("href", CSS_NAME)
        item.set("media-type", "text/css")
        return item.get("id") or "bbm-names-css"
    used = {entry.get("id") for entry in manifest.findall(f"{{{_OPF_NS}}}item")}
    item_id = "bbm-names-css"
    suffix = 1
    while item_id in used:
        item_id = f"bbm-names-css-{suffix}"
        suffix += 1
    item = etree.SubElement(manifest, f"{{{_OPF_NS}}}item")
    item.set("id", item_id)
    item.set("href", CSS_NAME)
    item.set("media-type", "text/css")
    return item_id


def _remove_css_manifest(opf):
    manifest, item = _manifest_css_item(opf)
    if item is not None:
        manifest.remove(item)


def _relative_href(css_path, document_path):
    return posixpath.relpath(css_path, posixpath.dirname(document_path) or ".")


def _local_name(element):
    return etree.QName(element).localname


def _in_head(element):
    current = element
    while current is not None:
        if _local_name(current).lower() in ("head", "script", "style", "title"):
            return True
        current = current.getparent()
    return False


def _make_span(label):
    key = marker_key(label)
    outer = etree.Element(f"{{{_XHTML_NS}}}span")
    outer.set("class", "bbm-name")
    outer.set("data-bbm-key", key)
    inner = etree.SubElement(outer, f"{{{_XHTML_NS}}}span")
    inner.set("class", "bbm-original")
    inner.text = label
    return outer


def _replace_text_slot(element, attr, text):
    """Replace markers in one element text/tail slot with span elements."""
    items = []
    buffer = ""
    for kind, value in marker_parts(text):
        if kind == "text":
            buffer += value
        else:
            if buffer:
                items.append(buffer)
                buffer = ""
            items.append(_make_span(value))
    if buffer:
        items.append(buffer)
    if not any(isinstance(item, etree._Element) for item in items):
        return False

    if attr == "text":
        parent = element
        index = 0
        last = None
        parent.text = None
    else:
        parent = element.getparent()
        if parent is None:
            return False
        index = parent.index(element) + 1
        # Text before the first span belongs to the element whose tail this
        # slot was; using it as the anchor attaches that text correctly.
        last = element
        element.tail = None

    pending = ""
    for item in items:
        if isinstance(item, str):
            pending += item
            continue
        if last is None:
            parent.text = pending or None
        else:
            last.tail = pending or None
        parent.insert(index, item)
        index += 1
        last = item
        pending = ""
    if last is None:
        parent.text = pending or None
    else:
        last.tail = pending or None
    return True


def render_markers_in_tree(root):
    """Convert marker expressions in body text nodes into name spans."""
    elements = list(root.iter())
    changed = False
    for element in elements:
        if _in_head(element):
            continue
        if isinstance(element.text, str) and "${" in element.text:
            changed = _replace_text_slot(element, "text", element.text) or changed
        if (
            isinstance(element.tail, str)
            and "${" in element.tail
            and element.getparent() is not None
            and not _in_head(element.getparent())
        ):
            changed = _replace_text_slot(element, "tail", element.tail) or changed
    return changed


def _inject_css_link(root, href, present=True):
    head = next(
        (node for node in root.iter() if _local_name(node).lower() == "head"), None
    )
    if head is None:
        if present:
            html = next(
                (node for node in root.iter() if _local_name(node).lower() == "html"),
                None,
            )
            if html is None:
                return False
            head = etree.Element(f"{{{_XHTML_NS}}}head")
            html.insert(0, head)
        else:
            return False
    changed = False
    for link in list(head):
        if _local_name(link).lower() != "link":
            continue
        link_href = link.get("href") or ""
        if posixpath.basename(link_href) == CSS_NAME:
            if present and link_href != href:
                link.set("href", href)
                changed = True
            elif not present:
                head.remove(link)
                changed = True
    if present and not any(
        _local_name(child).lower() == "link"
        and posixpath.basename(child.get("href") or "") == CSS_NAME
        for child in head
    ):
        link = etree.SubElement(head, f"{{{_XHTML_NS}}}link")
        link.set("rel", "stylesheet")
        link.set("type", "text/css")
        link.set("href", href)
        changed = True
    return changed


def _parse_document(data):
    parser = etree.XMLParser(
        resolve_entities=False, no_network=True, recover=False, huge_tree=True
    )
    return etree.fromstring(data, parser)


def _serialize_document(root):
    tree = root.getroottree()
    doctype = getattr(tree.docinfo, "doctype", "") or None
    return etree.tostring(
        tree,
        encoding="utf-8",
        xml_declaration=True,
        doctype=doctype,
    )


# ------------------------------------------------------------------- CSS


def css_string(value):
    """Escape one CSS string literal, including controls and Unicode."""
    out = ['"']
    for char in value:
        code = ord(char)
        if char in ('"', "\\"):
            out.append("\\" + char)
        elif code < 0x20 or code == 0x7F:
            out.append(f"\\{code:x} ")
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


def generate_css(catalogue):
    """Generate one stylesheet from the authoritative target mappings."""
    rules = [
        "/* Generated by bilingual_book_maker marker workflow. */",
        ".bbm-name { white-space: normal; }",
    ]
    for label, entry in sorted(catalogue.items()):
        target = entry.get("target")
        if not target:
            continue
        key = entry.get("key") or marker_key(label)
        selector = f'[data-bbm-key="{key}"]'
        rules.append(f"{selector} > .bbm-original {{ display: none; }}")
        rules.append(
            f"{selector}::before {{ content: {css_string(target)}; }}"
        )
    return ("\n".join(rules) + "\n").encode("utf-8")


# --------------------------------------------------------- localization


def _mapping(catalogue):
    return {
        entry.get("key") or marker_key(label): entry.get("target")
        for label, entry in (catalogue or {}).items()
        if isinstance(entry, dict) and entry.get("target")
    }


# ------------------------------------------- destination-aware localization
#
# A localization master is retained exactly as the model wrote it, markers
# included.  A destination is the written resource plus a structural address
# to the element or attribute that displays that master.  Regeneration renders
# the master through the *current* catalogue and assigns the result; it never
# runs replacements over already-displayed text.


def _element_children(element):
    return [child for child in element if isinstance(child.tag, str)]


def _element_path(element):
    """Index path over element children only (comments do not shift it)."""
    path = []
    current = element
    while True:
        parent = current.getparent()
        if parent is None:
            break
        index = 0
        found = False
        for sibling in parent:
            if sibling is current:
                found = True
                break
            if isinstance(sibling.tag, str):
                index += 1
        if not found:
            raise BundleError("Could not address a localization destination")
        path.append(index)
        current = parent
    path.reverse()
    return path


def _resolve_element(root, path):
    current = root
    for index in path:
        children = _element_children(current)
        if index >= len(children):
            raise BundleError(
                "A localization destination no longer resolves in this EPUB"
            )
        current = children[index]
    return current


def _element_text(element):
    return "".join(element.itertext())


def _set_element_text(element, value):
    """Assign text to the first text slot, keeping inline wrappers intact."""
    slots = []
    if isinstance(element.text, str):
        slots.append((element, "text"))
    for child in element.iter():
        if child is element or not isinstance(child.tag, str):
            continue
        if isinstance(child.text, str):
            slots.append((child, "text"))
        if isinstance(child.tail, str):
            slots.append((child, "tail"))
    if not slots:
        element.text = value
        return
    first, attribute = slots[0]
    setattr(first, attribute, value)
    for slot, attribute in slots[1:]:
        setattr(slot, attribute, None)


class LocalizationBindings:
    """Explicit source-to-master bindings for one serialization.

    The loader fills the identity maps while it still knows which source
    produced each display value.  Tokens carry those bindings through
    ebooklib's generated nav, NCX, and OPF output; the finished EPUB stores
    only structural destinations, never the transient registry.
    """

    def __init__(self, single):
        self.single = bool(single)
        self.records = []
        self.by_key = {}
        self.file = {}
        self.toc = {}
        self.dc = {}
        self.nonce = None

    def add(self, key, source, master, display=None):
        if not isinstance(master, str):
            raise BundleError("A localization master must be text")
        key = str(key or "")
        for index, record in enumerate(self.records, 1):
            if key and record["key"] == key:
                if (
                    record["source"] != (source or "")
                    or record["master"] != master
                ):
                    raise BundleError(
                        "Conflicting localization bindings for one source "
                        f"identity {key!r}"
                    )
                if display and not record["display"]:
                    record["display"] = display
                self.by_key.setdefault(key, index)
                return index
        self.records.append(
            {
                "key": key,
                "source": source or "",
                "master": master,
                "display": display if isinstance(display, str) else "",
                "single": self.single,
            }
        )
        if key:
            self.by_key.setdefault(key, len(self.records))
        return len(self.records)

    def id_for_key(self, key):
        return self.by_key.get(str(key or ""))

    def prepare_nonce(self, texts):
        """Choose a per-serialization nonce and reject stale tokens."""
        candidates = []
        for value in texts:
            if isinstance(value, bytes):
                value = value.decode("utf-8", "ignore")
            if isinstance(value, str):
                candidates.append(value)
        for value in candidates:
            if _TOKEN_PREFIX in value:
                raise BundleError(
                    "A localization token leaked into the serialization source"
                )
        while True:
            nonce = uuid4().hex
            if not any(nonce in value for value in candidates):
                self.nonce = nonce
                return nonce

    def prepare_nonce_for_book(self, book):
        return self.prepare_nonce(_binding_texts(book))

    def token(self, binding_id):
        if self.nonce is None:
            raise BundleError("Localization serialization nonce is not prepared")
        return f"{_TOKEN_PREFIX}{self.nonce}X{int(binding_id)}Z"

    def record(self, binding_id):
        try:
            return self.records[int(binding_id) - 1]
        except (IndexError, TypeError, ValueError) as error:
            raise BundleError(
                f"Unknown localization binding id {binding_id!r}"
            ) from error

    def pattern(self):
        if self.nonce is None:
            raise BundleError("Localization serialization nonce is not prepared")
        return re.compile(
            re.escape(f"{_TOKEN_PREFIX}{self.nonce}X") + r"(\d+)Z"
        )


def _binding_texts(book):
    """All text that must not collide with a new serialization token."""
    texts = [getattr(book, "title", "") or ""]
    for item in book.get_items():
        content = getattr(item, "content", b"")
        if isinstance(content, bytes):
            texts.append(content.decode("utf-8", "ignore"))
        elif isinstance(content, str):
            texts.append(content)
    for metas in (getattr(book, "metadata", {}) or {}).values():
        for values in metas.values():
            for value, _attrs in values:
                texts.append(value or "")
    for entry in _toc_nodes(getattr(book, "toc", ())):
        texts.append(getattr(entry, "title", "") or "")
    return texts


def _toc_nodes(toc):
    for entry in toc or ():
        if isinstance(entry, (tuple, list)):
            yield entry[0]
            yield from _toc_nodes(entry[1])
        else:
            yield entry


def _legacy_localization_affected(state, previous_catalogue):
    """Whether a catalogue edit changes any saved legacy display master."""
    catalogue = state.get("catalogue") or {}
    previous = previous_catalogue or {}
    localization = state.get("localization") or {}
    version = localization.get("version")
    entries = []
    if version in (None, 1):
        for item in localization.get("values") or []:
            if not isinstance(item, dict):
                continue
            key = item.get("key") or ""
            if not key.startswith(("toc:", "nav:", "dc:")):
                continue
            if isinstance(item.get("master"), str):
                entries.append(
                    (
                        item.get("source") or "",
                        item["master"],
                        bool(localization.get("single")),
                    )
                )
    else:
        for item in localization.get("destinations") or []:
            if isinstance(item, dict) and isinstance(item.get("master"), str):
                entries.append(
                    (
                        item.get("source") or "",
                        item["master"],
                        bool(item.get("single")),
                    )
                )
        if not entries:
            for item in localization.get("values") or []:
                if not isinstance(item, dict):
                    continue
                key = item.get("key") or ""
                if not key.startswith(("toc:", "nav:", "dc:")):
                    continue
                if isinstance(item.get("master"), str):
                    entries.append(
                        (
                            item.get("source") or "",
                            item["master"],
                            bool(localization.get("single")),
                        )
                    )
    for source, master, single in entries:
        old = _render_destination(
            {"source": source, "master": master, "single": single}, previous
        )
        new = _render_destination(
            {"source": source, "master": master, "single": single}, catalogue
        )
        if old != new:
            return True
    return False





def _localization_values(localization):
    return [
        item
        for item in (localization.get("values") or [])
        if isinstance(item, dict) and isinstance(item.get("master"), str)
    ]


def _token_destination_record(
    resource, element, slot, attribute, source, master, single
):
    return {
        "resource": resource,
        "path": _element_path(element),
        "element": element.tag,
        "destination": slot,
        "attribute": attribute,
        "source": source or "",
        "master": master,
        "single": bool(single),
    }


def _consume_token_slot(
    records, changed, resource, element, slot, attribute, text, bindings, catalogue
):
    if not isinstance(text, str) or _TOKEN_PREFIX not in text:
        return False
    matches = list(bindings.pattern().finditer(text))
    if not matches:
        raise BundleError(
            f"Unknown localization token in {resource}; refusing to publish"
        )
    if len(matches) > 1:
        raise BundleError(
            f"Multiple localization tokens occupy one slot in {resource}"
        )
    match = matches[0]
    prefix, suffix = text[: match.start()], text[match.end():]
    if _TOKEN_PREFIX in prefix or _TOKEN_PREFIX in suffix:
        raise BundleError(
            f"Unresolved localization token in {resource}; refusing to publish"
        )
    binding = bindings.record(match.group(1))
    source = prefix + (binding.get("source") or "") + suffix
    master = prefix + binding["master"] + suffix
    record = _token_destination_record(
        resource,
        element,
        slot,
        attribute,
        source,
        master,
        binding["single"],
    )
    rendered = _render_destination(record, catalogue)
    if slot == "attribute":
        element.set(attribute, rendered)
    elif slot == "tail":
        element.tail = rendered
    else:
        element.text = rendered
    records.append(record)
    changed.add(resource)
    return True


def capture_token_destinations(state, roots, bindings):
    """Replace serialization tokens with final slots and structural records."""
    catalogue = state.get("catalogue") or {}
    localization = state.setdefault("localization", {})
    records = []
    changed = set()
    for resource in sorted(roots):
        root = roots[resource]
        for element in root.iter():
            if not isinstance(element.tag, str):
                continue
            for attribute, text in list(element.attrib.items()):
                _consume_token_slot(
                    records,
                    changed,
                    resource,
                    element,
                    "attribute",
                    attribute,
                    text,
                    bindings,
                    catalogue,
                )
            _consume_token_slot(
                records,
                changed,
                resource,
                element,
                "text",
                None,
                element.text,
                bindings,
                catalogue,
            )
            _consume_token_slot(
                records,
                changed,
                resource,
                element,
                "tail",
                None,
                element.tail,
                bindings,
                catalogue,
            )
    assert_no_localization_tokens(roots)
    localization["version"] = LOCALIZATION_VERSION
    localization["destinations"] = records
    return changed


def assert_no_localization_tokens(roots):
    """Fail if any transient serialization token survived capture."""
    if isinstance(roots, dict):
        values = roots.values()
    else:
        values = roots
    for root in values:
        for element in root.iter():
            if not isinstance(element.tag, str):
                continue
            slots = [element.text, element.tail, *element.attrib.values()]
            for text in slots:
                if isinstance(text, str) and _GENERIC_TOKEN_RE.search(text):
                    raise BundleError(
                        "A localization token survived serialization; "
                        "refusing to publish"
                    )


def _render_destination(record, catalogue):
    """Render one destination, preserving any surrounding slot whitespace."""
    source = record.get("source") or ""
    translated = display_text(record.get("master") or "", _mapping(catalogue))
    if not translated.strip() or source.strip() == translated.strip():
        return source
    return translated if bool(record.get("single")) else f"{source} / {translated}"


def regenerate_localization_destinations(state, roots):
    """Render every recorded destination from its immutable master.

    Returns the set of resource paths whose parsed tree changed.
    """
    localization = state.get("localization") or {}
    version = localization.get("version")
    destinations = localization.get("destinations") or []
    changed = set()
    for record in destinations:
        resource = record["resource"]
        root = roots.get(resource)
        if root is None:
            raise BundleError(
                f"Localization destination resource is missing: {resource}"
            )
        element = _resolve_element(root, record["path"])
        if element.tag != record["element"]:
            raise BundleError(
                "Localization destination element does not match its recorded "
                f"address in {resource}"
            )
        value = _render_destination(record, state.get("catalogue") or {})
        destination = record["destination"]
        if destination == "attribute":
            attribute = record["attribute"]
            if (element.get(attribute) or "") != value:
                element.set(attribute, value)
                changed.add(resource)
        elif version == LOCALIZATION_VERSION:
            if destination == "tail":
                if (element.tail or "") != value:
                    element.tail = value
                    changed.add(resource)
            else:
                if (element.text or "") != value:
                    element.text = value
                    changed.add(resource)
        elif _element_text(element) != value:
            _set_element_text(element, value)
            changed.add(resource)
    return changed


# ---------------------------------------------------------------- assembly


def _iter_document_infos(infos):
    for info in infos:
        if info.filename.lower().endswith(_XHTML_SUFFIXES):
            yield info


def _iter_ncx_infos(infos):
    for info in infos:
        if info.filename.lower().endswith(".ncx"):
            yield info


def _build_candidate(
    source_epub,
    candidate_path,
    state,
    render=True,
    previous_catalogue=None,
    bindings=None,
):
    """Assemble one complete candidate EPUB in the OS temporary directory."""
    with ZipFile(source_epub) as archive:
        infos = archive.infolist()
        opf_path, opf, _opf_data = _read_opf(archive)
        css_path = _css_path_for(opf_path)
        localization = state.get("localization") or {}
        localization_version = localization.get("version")
        trusted = localization_version == LOCALIZATION_VERSION
        token_capture = bool(
            render
            and previous_catalogue is None
            and trusted
            and bindings is not None
        )
        apply_destinations = bool(
            render and previous_catalogue is not None and trusted
        )
        if previous_catalogue is not None and not trusted:
            # Version-1/2 bundles cannot prove which master belongs to a
            # destination.  Never migrate or repair identity from displayed
            # text; permit only an edit that leaves every saved master alone.
            if _legacy_localization_affected(state, previous_catalogue):
                raise BundleError(
                    "This marker bundle predates trusted destination "
                    "provenance; the edited mapping affects saved navigation "
                    "or metadata masters, so it needs a source-backed "
                    "regeneration. Re-run translation from the source EPUB "
                    "instead of editing this older bundle."
                )
        if (
            render
            and previous_catalogue is None
            and trusted
            and bindings is None
            and (_localization_values(localization) or localization.get("destinations"))
        ):
            raise BundleError(
                "Version-3 localization requires explicit source bindings; "
                "refusing to infer identities from displayed text."
            )
        destination_resources = {
            record.get("resource")
            for record in (localization.get("destinations") or [])
            if isinstance(record, dict)
        }

        documents = {}
        dirty = set()
        needs_css = False
        capture_all = token_capture
        for info in _iter_document_infos(infos):
            data = archive.read(info)
            if not render and _SPAN_BYTES not in data:
                continue
            wants_render = render and _MARKER_BYTES in data
            wants_link = _SPAN_BYTES in data or wants_render
            wants_destination = info.filename in destination_resources
            if not (
                wants_render
                or wants_link
                or wants_destination
                or capture_all
                or not render
            ):
                continue
            root = _parse_document(data)
            changed = False
            if wants_render:
                changed = render_markers_in_tree(root) or changed
            if render and wants_link:
                changed = _inject_css_link(
                    root, _relative_href(css_path, info.filename), present=True
                ) or changed
                needs_css = True
            elif not render:
                changed = _inject_css_link(root, "", present=False) or changed
            documents[info.filename] = root
            if changed:
                dirty.add(info.filename)

        ncx_roots = {}
        for info in _iter_ncx_infos(infos):
            if not (
                capture_all
                or info.filename in destination_resources
            ):
                continue
            data = archive.read(info)
            ncx_roots[info.filename] = _parse_document(data)

        roots = dict(documents)
        roots.update(ncx_roots)
        roots[opf_path] = opf
        if apply_destinations:
            dirty.update(regenerate_localization_destinations(state, roots))
        if token_capture:
            dirty.update(capture_token_destinations(state, roots, bindings))
        elif trusted:
            # Offline edits and bundles with no assignments still must not
            # carry transient tokens from an earlier failed serialization.
            assert_no_localization_tokens(roots)

        if render and needs_css:
            _ensure_css_manifest(opf, opf_path)
        elif not render:
            _remove_css_manifest(opf)
        opf_tree = opf.getroottree()
        opf_bytes = etree.tostring(
            opf_tree,
            encoding="utf-8",
            xml_declaration=True,
            doctype=getattr(opf_tree.docinfo, "doctype", "") or None,
        )

        docs = {}
        for name, root in roots.items():
            if name != opf_path and name in dirty:
                docs[name] = _serialize_document(root)

        if trusted:
            packed = state_bytes(state)
            if _GENERIC_TOKEN_RE.search(packed.decode("utf-8", "ignore")):
                raise BundleError(
                    "A localization token reached embedded marker state"
                )
            for data in list(docs.values()) + [opf_bytes]:
                if _GENERIC_TOKEN_RE.search(data.decode("utf-8", "ignore")):
                    raise BundleError(
                        "A localization token survived into a published resource"
                    )

        with ZipFile(candidate_path, "w") as out:
            # mimetype must be the first entry and stored without compression.
            mimetype = MIMETYPE.encode("ascii")
            if any(info.filename == "mimetype" for info in infos):
                mimetype = archive.read("mimetype")
            first = ZipInfo("mimetype")
            first.compress_type = ZIP_STORED
            out.writestr(first, mimetype)
            for info in infos:
                name = info.filename
                if name == "mimetype" or name == STATE_PATH:
                    continue
                if name == css_path:
                    continue
                if name == opf_path:
                    data = opf_bytes
                elif name in docs:
                    data = docs[name]
                else:
                    data = archive.read(info)
                out.writestr(info, data)
            if render and needs_css:
                css = ZipInfo(css_path)
                css.compress_type = ZIP_DEFLATED
                out.writestr(css, generate_css(state.get("catalogue") or {}))
            state_info = ZipInfo(STATE_PATH)
            state_info.compress_type = ZIP_DEFLATED
            out.writestr(state_info, state_bytes(state))


def _atomic_replace(candidate, destination, verify=False):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, staged = tempfile.mkstemp(
        prefix=".bbm-stage-", suffix=".epub.tmp", dir=destination.parent
    )
    os.close(fd)
    try:
        shutil.copyfile(candidate, staged)
        if verify:
            verify_bundle(staged)
        os.replace(staged, destination)
    finally:
        if os.path.exists(staged):
            os.unlink(staged)


def finalize_bundle(
    source_epub,
    output_epub,
    state,
    render=True,
    previous_catalogue=None,
    validate=True,
    bindings=None,
):
    """Render markers/CSS, embed state, verify, and atomically replace output.

    The source may already be the destination: the candidate is assembled in
    the OS temporary directory before the old file is touched.  ``bindings``
    carries explicit source identities through ebooklib's disposable
    serialization; it is never embedded in the finished bundle.
    """
    if validate:
        validate_state(state)
    tmp_dir = tempfile.mkdtemp(prefix="bbm-bundle-")
    candidate = os.path.join(tmp_dir, "candidate.epub")
    try:
        _build_candidate(
            source_epub,
            candidate,
            state,
            render=render,
            previous_catalogue=previous_catalogue,
            bindings=bindings,
        )
        expected = state if validate else None
        verify_bundle(candidate, expected_state=expected)
        _atomic_replace(candidate, output_epub)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def write_bundle(source_epub, output_epub, state, validate=False):
    """Embed JSON state in an existing EPUB and atomically replace output."""
    tmp_dir = tempfile.mkdtemp(prefix="bbm-bundle-")
    candidate = os.path.join(tmp_dir, "candidate.epub")
    try:
        with ZipFile(source_epub) as source, ZipFile(candidate, "w") as result:
            infos = source.infolist()
            mimetype = MIMETYPE.encode("ascii")
            if any(info.filename == "mimetype" for info in infos):
                mimetype = source.read("mimetype")
            first = ZipInfo("mimetype")
            first.compress_type = ZIP_STORED
            result.writestr(first, mimetype)
            for info in infos:
                if info.filename in ("mimetype", STATE_PATH):
                    continue
                result.writestr(info, source.read(info))
            state_info = ZipInfo(STATE_PATH)
            state_info.compress_type = ZIP_DEFLATED
            result.writestr(state_info, state_bytes(state))
        if validate:
            verify_bundle(candidate, expected_state=state)
        _atomic_replace(candidate, output_epub)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# -------------------------------------------------------------- verification


def _check_mimetype(archive):
    infos = archive.infolist()
    if not infos or infos[0].filename != "mimetype":
        raise BundleError("EPUB mimetype is not the first ZIP entry")
    if infos[0].compress_type != ZIP_STORED:
        raise BundleError("EPUB mimetype entry is compressed")
    if archive.read("mimetype") != MIMETYPE.encode("ascii"):
        raise BundleError("EPUB mimetype entry has the wrong media type")


def verify_bundle(epub_path, expected_state=None):
    """Check ZIP integrity, EPUB container/OPF structure, and embedded state."""
    try:
        with ZipFile(epub_path) as archive:
            if archive.testzip() is not None:
                raise BundleError("EPUB ZIP integrity check failed")
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise BundleError("EPUB contains duplicate ZIP entries")
            _check_mimetype(archive)
            opf_path, opf, _opf_data = _read_opf(archive)
            manifest = opf.find(f"{{{_OPF_NS}}}manifest")
            if manifest is None:
                raise BundleError("EPUB package document has no manifest")
            for item in manifest.findall(f"{{{_OPF_NS}}}item"):
                href = item.get("href") or ""
                if not href or "://" in href:
                    continue
                resolved = posixpath.normpath(
                    posixpath.join(posixpath.dirname(opf_path), href)
                )
                if resolved not in names:
                    raise BundleError(
                        f"EPUB manifest item {href!r} is missing from the archive"
                    )
            try:
                state = json.loads(archive.read(STATE_PATH).decode("utf-8"))
            except KeyError as error:
                raise BundleError("EPUB has no embedded marker state") from error
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise BundleError("EPUB embedded marker state is corrupt") from error
            if isinstance(state, dict) and state.get("workflow"):
                validate_state(state)
            if expected_state is not None and state != expected_state:
                raise BundleError("EPUB embedded marker state did not round-trip")
            css_item = None
            for item in manifest.findall(f"{{{_OPF_NS}}}item"):
                if posixpath.basename(item.get("href") or "") == CSS_NAME:
                    css_item = item
                    break
            if css_item is not None:
                css_href = posixpath.normpath(
                    posixpath.join(
                        posixpath.dirname(opf_path), css_item.get("href")
                    )
                )
                if css_href not in names:
                    raise BundleError("EPUB name stylesheet is missing")
            return state
    except OSError as error:
        raise BundleError(f"Could not read EPUB bundle: {error}") from error


# ---------------------------------------------------------- text export


def _replace_element_with_text(element, text):
    parent = element.getparent()
    if parent is None:
        return
    tail = element.tail or ""
    previous = element.getprevious()
    if previous is None:
        parent.text = (parent.text or "") + text + tail
    else:
        previous.tail = (previous.tail or "") + text + tail
    parent.remove(element)


def _strip_name_spans(root, catalogue):
    changed = False
    for element in list(root.iter()):
        if not isinstance(element.tag, str):
            continue
        classes = (element.get("class") or "").split()
        if "bbm-name" not in classes:
            continue
        key = element.get("data-bbm-key") or ""
        entry = next(
            (
                value
                for value in catalogue.values()
                if isinstance(value, dict) and value.get("key") == key
            ),
            None,
        )
        original = "".join(element.itertext())
        if element.find(".//*[@class='bbm-original']") is not None:
            original = "".join(
                "".join(child.itertext())
                for child in element
                if "bbm-original" in (child.get("class") or "").split()
            ) or original
        text = (entry or {}).get("target") or original
        _replace_element_with_text(element, text)
        changed = True
    return changed


def export_text_bundle(epub_path, destination):
    """Write an ordinary-text EPUB with target-or-original name text."""
    state = read_state(epub_path, validate=True)
    catalogue = state.get("catalogue") or {}
    tmp_dir = tempfile.mkdtemp(prefix="bbm-text-")
    candidate = os.path.join(tmp_dir, "candidate.epub")
    try:
        with ZipFile(epub_path) as archive:
            opf_path, opf, _opf_data = _read_opf(archive)
            css_path = _css_path_for(opf_path)
            docs = {}
            for info in _iter_document_infos(archive.infolist()):
                data = archive.read(info)
                if _SPAN_BYTES not in data:
                    continue
                root = _parse_document(data)
                changed = _strip_name_spans(root, catalogue)
                changed = _inject_css_link(root, "", present=False) or changed
                docs[info.filename] = _serialize_document(root) if changed else data
            _remove_css_manifest(opf)
            opf_bytes = etree.tostring(
                opf, encoding="utf-8", xml_declaration=True
            )
            with ZipFile(candidate, "w") as out:
                first = ZipInfo("mimetype")
                first.compress_type = ZIP_STORED
                out.writestr(first, archive.read("mimetype"))
                for info in archive.infolist():
                    name = info.filename
                    if name in ("mimetype", STATE_PATH, css_path):
                        continue
                    data = opf_bytes if name == opf_path else docs.get(
                        name, archive.read(info)
                    )
                    out.writestr(info, data)
                state_info = ZipInfo(STATE_PATH)
                state_info.compress_type = ZIP_DEFLATED
                out.writestr(state_info, state_bytes(state))
        verify_bundle(candidate, expected_state=state)
        _atomic_replace(candidate, destination)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# --------------------------------------------------------- catalogue edits


def _normalise_catalogue_update(updated):
    if isinstance(updated, dict) and "catalogue" in updated:
        updated = updated["catalogue"]
    if isinstance(updated, list):
        result = {}
        for entry in updated:
            if not isinstance(entry, dict) or not entry.get("label"):
                raise BundleError("Catalogue update entry is missing its label")
            result[entry["label"]] = entry
        return result
    if not isinstance(updated, dict):
        raise BundleError("Catalogue update must be a JSON object or list")
    return updated


def apply_catalogue_update(epub_path, updated):
    """Apply target-only edits, then regenerate CSS, navigation and metadata."""
    state = read_state(epub_path, validate=True)
    old_catalogue = json.loads(json.dumps(state["catalogue"]))
    updates = _normalise_catalogue_update(updated)
    catalogue = state["catalogue"]
    immutable = ("key", "label", "count", "references", "contexts", "paragraphs")
    for label, raw in updates.items():
        if label not in catalogue:
            raise BundleError(f"Catalogue update names an unknown label: {label!r}")
        entry = catalogue[label]
        if not isinstance(raw, dict):
            raise BundleError(f"Catalogue update for {label!r} is not an object")
        for field in immutable:
            if field in raw and raw[field] != entry.get(field):
                raise BundleError(
                    f"Catalogue update may not change {field!r} for {label!r}"
                )
        if "target" not in raw:
            continue
        target = raw["target"]
        if target is not None:
            target = validate_display_target(target)
        entry["target"] = target
        entry["mapping_status"] = "mapped" if target else "pending"
        pending = state.setdefault("mapping", {}).setdefault("pending", [])
        if target:
            pending[:] = [value for value in pending if value != label]
        elif label not in pending:
            pending.append(label)
    finalize_bundle(
        epub_path, epub_path, state, render=True, previous_catalogue=old_catalogue
    )
    return state


def catalogue_document(state):
    """The exportable catalogue, with labels kept as the mapping keys."""
    return json.loads(json.dumps(state.get("catalogue") or {}, ensure_ascii=False))


# Keep the regex import used by callers that want to detect wire markers.
WIRE_MARKER = MARKER
