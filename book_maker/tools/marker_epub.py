"""Offline catalogue inspection and display regeneration for marker EPUBs.

Examples::

    python -m book_maker.tools.marker_epub BOOK.epub export-catalogue names.json
    python -m book_maker.tools.marker_epub BOOK.epub apply-catalogue names.json
    python -m book_maker.tools.marker_epub BOOK.epub export-text plain.epub
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from book_maker.marker_bundle import (
    BundleError,
    apply_catalogue_update,
    catalogue_document,
    export_text_bundle,
    read_state,
)


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.stem}-", suffix=".json.tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def export_catalogue(book, destination):
    state = read_state(book, validate=True)
    _write_json(destination, catalogue_document(state))
    print(f"Catalogue exported: {Path(destination).resolve()}")


def apply_catalogue(book, updated):
    try:
        value = json.loads(Path(updated).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"Could not read catalogue update: {error}")
    state = apply_catalogue_update(book, value)
    mapped = sum(1 for entry in state["catalogue"].values() if entry.get("target"))
    print(
        f"Catalogue updated in place: {Path(book).resolve()} "
        f"({mapped}/{len(state['catalogue'])} labels mapped)"
    )


def export_text(book, destination):
    export_text_bundle(book, destination)
    print(f"Ordinary-text EPUB written: {Path(destination).resolve()}")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="python -m book_maker.tools.marker_epub",
        description="Inspect and regenerate the embedded marker state of an EPUB.",
    )
    parser.add_argument("book", help="EPUB produced by --protected-names")
    commands = parser.add_subparsers(dest="command", required=True)

    catalogue = commands.add_parser(
        "export-catalogue", help="write the authoritative catalogue as JSON"
    )
    catalogue.add_argument("destination", help="destination JSON file")
    catalogue.set_defaults(func=export_catalogue)

    apply = commands.add_parser(
        "apply-catalogue",
        help="apply display-target edits and regenerate CSS/navigation in place",
    )
    apply.add_argument("updated", help="edited catalogue JSON")
    apply.set_defaults(func=apply_catalogue)

    text = commands.add_parser(
        "export-text", help="write an ordinary-text EPUB without provider calls"
    )
    text.add_argument("destination", help="destination EPUB file")
    text.set_defaults(func=export_text)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args.book, args.destination if hasattr(args, "destination") else args.updated)
    except BundleError as error:
        parser.exit(2, f"error: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
