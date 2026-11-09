"""Recovery discovery and lifecycle tests, without provider calls."""

import json
import os
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from book_maker.loader.epub_loader import EPUBBookLoader
from book_maker.marker_bundle import STATE_PATH
from book_maker.marker_names import WORKFLOW
from book_maker.marker_workflow import MarkerWorkflow


def _source(tmp_path, name="book.epub", content=b"source"):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _loader(source, resume):
    loader = EPUBBookLoader.__new__(EPUBBookLoader)
    loader.epub_name = str(source)
    loader.resume = resume
    loader.output_dir = str(source.parent / "output")
    loader.bin_path = str(source.parent / ".book.temp.bin")
    loader._run_fingerprint = lambda: "run-fingerprint"
    return loader


def _state(
    source_sha,
    fingerprint="run-fingerprint",
    run_id="run-1",
    status="partial",
    translations=(),
    job_ids=(),
):
    return {
        "schema_version": 1,
        "workflow": WORKFLOW,
        "run": {
            "source_sha256": source_sha,
            "source_name": "book.epub",
            "run_fingerprint": fingerprint,
            "effective_settings": {},
            "status": status,
            "run_id": run_id,
        },
        "checkpoint": {
            "accepted_job_ids": list(job_ids),
            "translations": list(translations),
            "localized_cache": {},
            "plan_fingerprint": None,
            "cleanup_version": None,
        },
        "catalogue": {},
        "session": {"seed": "", "exchanges": [], "windows": 1, "last_summary": ""},
        "mapping": {"pending": [], "failures": {}},
        "localization": {
            "version": 2,
            "single": False,
            "values": [],
            "destinations": [],
        },
    }


def _bundle(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(path, "w") as archive:
        archive.writestr(STATE_PATH, json.dumps(state))


def _sha(source):
    from hashlib import sha256

    return sha256(source.read_bytes()).hexdigest()


def test_resume_selects_a_matching_partial_bundle(tmp_path):
    source = _source(tmp_path)
    bundle = source.parent / "ongoing" / "book_bilingual_temp.epub"
    state = _state(_sha(source), translations=["一"], job_ids=["job-1"])
    _bundle(bundle, state)
    loader = _loader(source, resume=True)
    path, found = loader._discover_marker_bundle()
    assert path == bundle
    assert found["run"]["status"] == "partial"


def test_completed_output_never_satisfies_resume(tmp_path):
    source = _source(tmp_path)
    output = source.parent / "output" / "book_bilingual.epub"
    _bundle(output, _state(_sha(source), status="complete"))
    loader = _loader(source, resume=True)
    assert loader._discover_marker_bundle() is None


def test_backup_subdirectory_is_excluded_from_discovery(tmp_path):
    source = _source(tmp_path)
    ongoing = source.parent / "ongoing"
    # The offline repair helper archives originals here; discovery scans only
    # ongoing/*.epub directly, never its subdirectories.
    _bundle(
        ongoing / "backup" / "archived_bilingual_temp.epub",
        _state(_sha(source), translations=["一"], job_ids=["job-1"]),
    )
    loader = _loader(source, resume=True)
    assert loader._discover_marker_bundle() is None


def test_context_repair_fields_are_discovered_and_preserved(tmp_path):
    source = _source(tmp_path)
    bundle = source.parent / "ongoing" / "book_bilingual_temp.epub"
    state = _state(_sha(source), translations=["一"], job_ids=["job-1"])
    state["session"]["context_validation"] = {"version": 1}
    state["session"]["context_repair"] = {
        "pending": True,
        "purpose": "offline-cleanup",
        "reason": "recognized provider error message",
    }
    _bundle(bundle, state)
    loader = _loader(source, resume=True)
    path, found = loader._discover_marker_bundle()
    assert path == bundle
    assert found["session"]["context_repair"]["pending"] is True
    assert found["session"]["context_validation"]["version"] == 1


def test_malformed_context_repair_fields_make_a_bundle_incompatible(tmp_path):
    source = _source(tmp_path)
    bundle = source.parent / "ongoing" / "book_bilingual_temp.epub"
    state = _state(_sha(source))
    state["session"]["context_repair"] = {"pending": "yes"}
    _bundle(bundle, state)
    loader = _loader(source, resume=True)
    assert loader._discover_marker_bundle() is None


def test_completed_output_does_not_hide_a_partial_recovery(tmp_path):
    source = _source(tmp_path)
    _bundle(
        source.parent / "output" / "book_bilingual.epub",
        _state(_sha(source), status="complete"),
    )
    recovery = source.parent / "ongoing" / "book_bilingual_temp.epub"
    _bundle(recovery, _state(_sha(source), translations=["一"], job_ids=["job-1"]))
    path, _state_found = _loader(source, resume=True)._discover_marker_bundle()
    assert path == recovery


def test_newest_compatible_recovery_wins(tmp_path):
    source = _source(tmp_path)
    ongoing = source.parent / "ongoing"
    older = ongoing / "older.epub"
    newer = ongoing / "newer.epub"
    _bundle(older, _state(_sha(source), run_id="older"))
    _bundle(newer, _state(_sha(source), run_id="newer"))
    os.utime(older, ns=(1_000_000_000, 1_000_000_000))
    os.utime(newer, ns=(2_000_000_000, 2_000_000_000))
    path, _found = _loader(source, resume=True)._discover_marker_bundle()
    assert path == newer


def test_compatible_tie_is_broken_by_normalized_path(tmp_path):
    source = _source(tmp_path)
    ongoing = source.parent / "ongoing"
    first = ongoing / "A.epub"
    second = ongoing / "b.epub"
    _bundle(second, _state(_sha(source), run_id="b"))
    _bundle(first, _state(_sha(source), run_id="a"))
    for path in (first, second):
        os.utime(path, ns=(1_000_000_000, 1_000_000_000))
    path, _found = _loader(source, resume=True)._discover_marker_bundle()
    expected = min((first, second), key=lambda value: os.path.normcase(str(value)))
    assert path == expected


def test_different_source_is_reported_and_not_reused(tmp_path, capsys):
    source = _source(tmp_path)
    _bundle(
        source.parent / "ongoing" / "other.epub",
        _state("different-source", run_id="other"),
    )
    path = _loader(source, resume=True)._discover_marker_bundle()
    assert path is None
    output = capsys.readouterr().out
    assert "source SHA-256 differs" in output
    assert "starting a fresh run" in output


def test_different_settings_are_reported_and_not_reused(tmp_path, capsys):
    source = _source(tmp_path)
    _bundle(
        source.parent / "ongoing" / "other.epub",
        _state(_sha(source), fingerprint="other", run_id="other"),
    )
    path = _loader(source, resume=True)._discover_marker_bundle()
    assert path is None
    assert "settings differ" in capsys.readouterr().out


def test_distinct_fresh_runs_get_distinct_run_ids(tmp_path):
    source = _source(tmp_path)
    settings = {"run_fingerprint": "same", "request_max_units": 40}
    first = MarkerWorkflow.new_state(str(source), None, settings)
    second = MarkerWorkflow.new_state(str(source), None, settings)
    assert first["run"]["run_id"] != second["run"]["run_id"]
    assert first["run"]["run_fingerprint"] == second["run"]["run_fingerprint"]


def test_same_filename_with_different_content_does_not_collide(tmp_path):
    first = tmp_path / "one" / "book.epub"
    second = tmp_path / "two" / "book.epub"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    output = tmp_path / "output"

    def loader_for(path):
        loader = EPUBBookLoader.__new__(EPUBBookLoader)
        loader.epub_name = str(path)
        loader.output_dir = str(output)
        loader._marker_state = SimpleNamespace(
            state={"run": {"source_sha256": _sha(path), "run_id": "run"}}
        )
        return loader

    assert loader_for(first)._marker_recovery_path() != loader_for(
        second
    )._marker_recovery_path()


def test_recovery_directory_resolution(tmp_path):
    source = _source(tmp_path / "books")
    direct = EPUBBookLoader.__new__(EPUBBookLoader)
    direct.epub_name = str(source)
    direct.output_dir = None
    assert direct._marker_ongoing_dir() == source.parent / "ongoing"

    custom = EPUBBookLoader.__new__(EPUBBookLoader)
    custom.epub_name = str(source)
    custom.output_dir = str(tmp_path / "translations" / "finished")
    assert custom._marker_ongoing_dir() == tmp_path / "translations" / "ongoing"


def test_noncanonical_recovery_in_ongoing_is_resumed(tmp_path):
    source = _source(tmp_path)
    moved = source.parent / "ongoing" / "moved-here.epub"
    _bundle(moved, _state(_sha(source), translations=["一"], job_ids=["job-1"]))
    path, _found = _loader(source, resume=True)._discover_marker_bundle()
    assert path == moved


def test_mismatching_file_at_cleanup_path_is_preserved(tmp_path, capsys):
    source = _source(tmp_path)
    loader = _loader(source, resume=True)
    loader._marker_state = SimpleNamespace(
        state={"run": {"source_sha256": _sha(source), "run_id": "active"}}
    )
    recovery = loader._marker_recovery_path()
    _bundle(recovery, _state(_sha(source), run_id="other"))
    loader._remove_marker_recovery()
    assert recovery.exists()
    assert "Left unrelated file untouched" in capsys.readouterr().out


def test_cleanup_removes_matching_recovery_and_matching_alternate(tmp_path):
    source = _source(tmp_path)
    loader = _loader(source, resume=True)
    loader._marker_state = SimpleNamespace(
        state={"run": {"source_sha256": _sha(source), "run_id": "active"}}
    )
    recovery = loader._marker_recovery_path()
    alternate = source.parent / "ongoing" / "moved.epub"
    _bundle(recovery, _state(_sha(source), run_id="active"))
    _bundle(alternate, _state(_sha(source), run_id="active"))
    loader._resume_bundle_path = alternate
    loader._remove_marker_recovery()
    assert not recovery.exists()
    assert not alternate.exists()


def test_fresh_start_leaves_previous_recovery_untouched(tmp_path):
    source = _source(tmp_path)
    old = source.parent / "ongoing" / "old.epub"
    _bundle(old, _state(_sha(source), run_id="old"))
    loader = _loader(source, resume=False)
    assert loader._discover_marker_bundle() is None
    assert old.exists()


def test_unreadable_recovery_is_reported(tmp_path, capsys):
    source = _source(tmp_path)
    broken = source.parent / "ongoing" / "broken.epub"
    broken.parent.mkdir(parents=True)
    broken.write_bytes(b"not an epub")
    assert _loader(source, resume=True)._discover_marker_bundle() is None
    assert "incompatible marker bundle" in capsys.readouterr().out


def _checkpoint_loader(source):
    loader = _loader(source, resume=False)
    loader._marker_state = SimpleNamespace(
        state={"run": {"source_sha256": _sha(source), "run_id": "active"}}
    )
    loader._stamp_disclosure = lambda book: None
    loader.strip_obfuscated_codes = False
    loader._reobfuscate_written = lambda path: None
    loader._marker_snapshot = lambda status: _state(
        _sha(source), run_id="active", status=status
    )
    return loader


def test_failed_checkpoint_replacement_preserves_previous_recovery(
    tmp_path, monkeypatch
):
    source = _source(tmp_path)
    loader = _checkpoint_loader(source)
    recovery = loader._marker_recovery_path()
    recovery.parent.mkdir(parents=True)
    recovery.write_bytes(b"previous recovery")
    monkeypatch.setattr(
        "book_maker.loader.epub_loader.epub.write_epub",
        lambda candidate, book, options: Path(candidate).write_bytes(b"candidate"),
    )

    def fail(*args, **kwargs):
        raise RuntimeError("checkpoint assembly failed")

    monkeypatch.setattr("book_maker.marker_bundle.finalize_bundle", fail)
    with pytest.raises(RuntimeError):
        loader._write_protected_book(recovery, SimpleNamespace(), status="partial")
    assert recovery.read_bytes() == b"previous recovery"


def test_failed_final_publication_preserves_the_recovery(tmp_path, monkeypatch):
    source = _source(tmp_path)
    loader = _checkpoint_loader(source)
    recovery = loader._marker_recovery_path()
    recovery.parent.mkdir(parents=True)
    _bundle(recovery, _state(_sha(source), run_id="active"))
    final = Path(loader.output_dir) / "book_bilingual.epub"
    monkeypatch.setattr(
        "book_maker.loader.epub_loader.epub.write_epub",
        lambda candidate, book, options: Path(candidate).write_bytes(b"candidate"),
    )

    def fail(*args, **kwargs):
        raise RuntimeError("publication failed")

    monkeypatch.setattr("book_maker.marker_bundle.finalize_bundle", fail)
    with pytest.raises(RuntimeError):
        loader._write_protected_book(final, SimpleNamespace(), status="complete")
    assert recovery.exists()
    assert not final.exists()
