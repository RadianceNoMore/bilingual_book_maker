"""Moved files retain their command, import, and path-resolution contracts."""

import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from book_maker.cli import parse_prompt_arg
from book_maker.prompt_file import resolve_prompt_path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ("make_gg_book", "preview_progress", "fix_epub_metadata", "fix_en_metadata")
PROMPTS = ("prompt_decensor.json", "prompt_md.json", "prompt_md.prompt.md", "prompt_template_sample.txt")
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")


@pytest.mark.parametrize("name", TOOLS)
def test_root_import_is_the_implementation_module(name):
    original = importlib.import_module(name)
    relocated = importlib.import_module("book_maker.tools." + name)
    assert original is relocated
    assert callable(original.main)


@pytest.mark.parametrize("name", TOOLS)
def test_root_and_module_commands_return_the_same_help_or_usage(name):
    arguments = [] if name == "fix_en_metadata" else ["--help"]
    direct = subprocess.run([sys.executable, str(ROOT / (name + ".py")), *arguments],
                            cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    module = subprocess.run([sys.executable, "-m", "book_maker.tools." + name, *arguments],
                            cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    assert direct.returncode == module.returncode == (1 if name == "fix_en_metadata" else 0)
    assert direct.stdout == module.stdout
    assert direct.stderr == module.stderr


@pytest.mark.parametrize("name", PROMPTS)
def test_old_bare_prompt_names_resolve_to_new_examples(name, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert Path(resolve_prompt_path(name)) == ROOT / "examples/prompts" / name
    assert Path(resolve_prompt_path(name.upper())) == ROOT / "examples/prompts" / name


def test_existing_user_prompt_beats_the_bundled_alias(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "prompt_md.json").write_text('{"user": "My template: {text}"}', encoding="utf-8")
    assert parse_prompt_arg("prompt_md.json", announce=False)["user"] == "My template: {text}"
    with pytest.raises(FileNotFoundError):
        parse_prompt_arg(str(tmp_path / "missing/prompt_md.json"), announce=False)


def test_inline_template_is_not_treated_as_a_filesystem_path():
    template = "Translate {text}. " + "a" * 40000
    assert parse_prompt_arg(template, announce=False)["user"] == template


def test_fallback_runner_finds_main_and_preserves_state_location(tmp_path, monkeypatch):
    from book_maker.tools import make_gg_book as tool

    assert tool.skip_cache_path() == ROOT / "make_gg_book.skip.json"
    monkeypatch.setattr(tool, "skip_cache_path", lambda: tmp_path / "skips.json")
    calls = []

    def run(make_book, args, *, dry_run):
        calls.append((make_book, args, dry_run))
        return 0, ""

    monkeypatch.setattr(tool, "run_make_book", run)
    assert tool.main(["--book_name", str(tmp_path / "book.epub"), "--key", "test-key",
                      "--models", "test-model", "--dry-run"]) == 0
    assert calls[0][0] == ROOT / "make_book.py"
    assert calls[0][2] is True


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell is unavailable")
@pytest.mark.parametrize("script", ["run_grey_translation.ps1", "scripts/run_grey_translation.ps1"])
@pytest.mark.parametrize("whole", [False, True])
def test_launcher_preserves_arguments_and_repository_location(tmp_path, script, whole):
    captured = tmp_path / "args.json"
    harness = tmp_path / "launch.ps1"
    target = str(ROOT / script).replace("'", "''")
    harness.write_text(fr"""
$ErrorActionPreference = 'Stop'
function python {{
    @{{ args = @($args); cwd = (Get-Location).Path }} |
        ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $env:BBM_TEST_CAPTURE -Encoding utf8
    $global:LASTEXITCODE = 0
}}
& '{target}' -BookPath 'C:\Books\example.epub' -Resume -MaxUnits 25 -MaxTokens 2400 -WholeChapter:${str(whole).lower()} -TranslateFilename
exit $LASTEXITCODE
""", encoding="utf-8")
    result = subprocess.run([POWERSHELL, "-NoProfile", "-File", str(harness)], cwd=tmp_path,
                            env=dict(os.environ, BBM_TEST_CAPTURE=str(captured)),
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    recorded = json.loads(captured.read_text(encoding="utf-8-sig"))
    assert Path(recorded["cwd"]) == ROOT
    assert "--resume" in recorded["args"]
    assert "--translate-filename" in recorded["args"]
    assert "--protected-names" in recorded["args"]
    # The window-first workflow renamed these flags long after this launcher
    # test was written; assert what the launcher actually forwards today.
    assert "--name-window-tokens" in recorded["args"] and "8192" in recorded["args"]
    assert "--source-summary-tokens" in recorded["args"] and "1500" in recorded["args"]
    assert "25" in recorded["args"] and "2400" in recorded["args"]
    assert ("--whole-chapter" in recorded["args"]) == whole
    assert ("--accumulate-across-chapters" in recorded["args"]) != whole
    # Capture mode runs the launcher offline: no gateway preflight, so the
    # translator keeps the direct gemini-anon base and never the proxy port.
    assert "http://127.0.0.1:4995/v1" in recorded["args"]
    assert "http://127.0.0.1:4998/v1" not in recorded["args"]


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell is unavailable")
@pytest.mark.parametrize("script", ["translate_book.ps1", "scripts/translate_book.ps1"])
def test_general_launcher_locates_repo_in_dry_run_from_another_directory(tmp_path, script):
    book = tmp_path / "source with spaces.epub"
    book.write_bytes(b"")  # Dry-run must not open the EPUB or contact a provider.
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-File", str(ROOT / script), "-Book", str(book),
         "-Python", sys.executable, "-Key", "test-key", "-Models", "test-model",
         "-MaxAttempts", "1", "-DryRun"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "[dry-run]" in result.stdout
    assert book.read_bytes() == b""
