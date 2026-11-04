"""Offline marker-context repair (``book_maker.marker_repair``) tests."""

import zipfile

import pytest
from ebooklib import epub

from book_maker.marker_bundle import (
    SCHEMA_VERSION,
    WORKFLOW,
    read_state,
    write_bundle,
)
from book_maker.marker_repair import RepairNotNeeded, repair_bundle, repair_state


PROVIDER_BOILERPLATE = (
    "I'm having a hard time fulfilling your request. "
    "Can I help you with something else instead?"
)
OTHER_PROVIDER_BOILERPLATE = (
    "Sorry, something went wrong. Please try your request again."
)
SEED_WRAPPER = "You are continuing a translation already in progress."


def _state(seed="", summary="", exchanges=()):
    return {
        "schema_version": SCHEMA_VERSION,
        "workflow": WORKFLOW,
        "run": {
            "source_sha256": "0" * 64,
            "run_fingerprint": "1" * 64,
            "status": "partial",
        },
        "checkpoint": {"accepted_job_ids": [], "translations": []},
        "catalogue": {},
        "session": {
            "seed": seed,
            "last_summary": summary,
            "exchanges": list(exchanges),
            "windows": 1,
        },
    }


def _bundle(tmp_path, state, name="recovery.epub"):
    book = epub.EpubBook()
    book.set_identifier("marker-repair-test")
    book.set_title("Repair fixture")
    book.set_language("en")
    chapter = epub.EpubHtml(title="One", file_name="chapter.xhtml", lang="en")
    chapter.content = "<html><body><p>fixture</p></body></html>"
    book.add_item(chapter)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", chapter]
    source = tmp_path / "source.epub"
    epub.write_epub(str(source), book)
    path = tmp_path / name
    write_bundle(source, path, state)
    assert (zipfile.ZipFile(path).namelist()[0]) == "mimetype"
    return path


def test_repair_state_clears_only_the_poisoned_field():
    state = _state(
        seed=PROVIDER_BOILERPLATE, summary="An already valid summary."
    )
    changes = repair_state(state)
    assert changes == ["seed"]
    assert state["session"]["seed"] == ""
    assert state["session"]["last_summary"] == "An already valid summary."
    record = state["session"]["context_repair"]
    assert record["pending"] is True
    assert record["purpose"] == "offline-cleanup"
    assert "seed" in record["reason"]
    quarantined = state["diagnostics"]["failures"][-1]
    assert quarantined["stage"] == "offline-context-restore"
    assert quarantined["response"] == PROVIDER_BOILERPLATE


def test_repair_state_clears_both_fields_with_wrapped_seed():
    state = _state(
        seed=SEED_WRAPPER + "\n\n" + PROVIDER_BOILERPLATE,
        summary=OTHER_PROVIDER_BOILERPLATE,
    )
    changes = repair_state(state)
    assert changes == ["last_summary", "seed"]
    assert state["session"]["seed"] == ""
    assert state["session"]["last_summary"] == ""


def test_repair_state_leaves_clean_bundles_alone():
    state = _state(seed="Valid summary.", summary="Valid summary.")
    assert repair_state(state) == []
    assert "context_repair" not in state["session"]


def test_repair_bundle_replaces_state_atomically_and_backs_up(tmp_path):
    state = _state(seed=PROVIDER_BOILERPLATE, summary=OTHER_PROVIDER_BOILERPLATE)
    recovery = _bundle(tmp_path, state)

    changes, backup = repair_bundle(recovery, backup_dir=tmp_path / "backup")

    assert changes == ["last_summary", "seed"]
    assert backup.exists() and backup.parent == tmp_path / "backup"
    cleaned = read_state(recovery, validate=True)
    assert cleaned["session"]["seed"] == ""
    assert cleaned["session"]["last_summary"] == ""
    assert cleaned["session"]["context_repair"]["pending"] is True
    # Accepted-looking material is untouched and resources are preserved.
    assert cleaned["checkpoint"] == state["checkpoint"]
    assert cleaned["diagnostics"]["failures"][-1]["stage"] == (
        "offline-context-restore"
    )
    with zipfile.ZipFile(recovery) as archive:
        assert "EPUB/chapter.xhtml" in archive.namelist()
        assert archive.namelist()[0] == "mimetype"

    # A second run finds nothing left to poison and changes nothing.
    with pytest.raises(RepairNotNeeded):
        repair_bundle(recovery, backup_dir=tmp_path / "backup")
    assert read_state(recovery, validate=True)["session"]["seed"] == ""


def test_clean_bundle_is_reported_as_needing_no_repair(tmp_path):
    recovery = _bundle(tmp_path, _state(seed="Valid summary."))
    with pytest.raises(RepairNotNeeded) as report:
        repair_bundle(recovery, backup_dir=tmp_path / "backup")
    assert "recognized provider error" in str(report.value)
