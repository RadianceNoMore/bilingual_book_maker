"""Regression coverage for bounded requests and reviewable learned vocabulary."""

import json
from types import SimpleNamespace

import pytest

from book_maker.batching import request_slices
from book_maker.cli import parse_args
from book_maker.glossary import Glossary
from book_maker.glossary_review import learn_stable, append_review, read_review
from book_maker.loader.epub_loader import EPUBBookLoader
from book_maker.translation_audit import audit_translations, write_audit


def test_bounds_obey_both_limits_and_preserve_every_slot():
    texts = ["a", "bb", "ccc", "dddd", "eeeee"]
    parts = [texts[s] for s in request_slices(texts, 2, 6, len)]
    assert parts == [["a", "bb"], ["ccc"], ["dddd"], ["eeeee"]]
    assert sum(parts, []) == texts


def test_one_oversized_unit_is_never_cut_or_dropped():
    texts = ["x" * 20, "y", "z"]
    assert [texts[s] for s in request_slices(texts, 40, 10, len)] == [[texts[0]], ["y", "z"]]
    assert list(request_slices([], 40, 4000, len)) == []


@pytest.mark.parametrize("whole,force,expected", [(False, False, [2, 2, 1]), (True, False, [5]), (True, True, [2, 2, 1])])
def test_dispatch_caps_and_whole_chapter_override_keep_units_aligned(whole, force, expected):
    loader = EPUBBookLoader.__new__(EPUBBookLoader)
    loader.whole_chapter = whole
    loader.request_max_units = 2
    loader.request_max_tokens = 4000
    calls = []

    def translate(texts, translator, units, batch_function, retry_one_by_one):
        calls.append(len(texts))
        assert units == [f"unit-{text}" for text in texts]
        return ["translated-" + text for text in texts]

    loader._translate_cleaned_texts_aligned = translate
    texts = list("abcde")
    replies = loader._translate_texts_aligned(texts, units=[f"unit-{t}" for t in texts], force_bounded=force)
    assert calls == expected
    assert replies == ["translated-" + text for text in texts]


def test_request_limits_are_selectable_and_positive():
    args = parse_args(["--book_name", "b.epub", "--request-max-units", "25", "--request-max-tokens", "1800"])
    assert (args.request_max_units, args.request_max_tokens) == (25, 1800)
    for flag in ["--request-max-units", "--request-max-tokens"]:
        with pytest.raises(SystemExit):
            parse_args(["--book_name", "b.epub", flag, "0"])


def test_later_rendering_is_a_proposal_and_pins_still_win():
    existing = Glossary.parse("希尔德 → Hilde # name")
    proposed = Glossary.parse("希尔德 → Hilda # name\n索菲娅 → Sophia # name")
    learned, events = learn_stable(existing, proposed, Glossary())
    assert learned.lookup("希尔德").translation == "Hilde"
    assert learned.lookup("索菲娅").translation == "Sophia"
    assert events[0]["event"] == "conflict"
    assert events[0]["kept"] == "Hilde"
    pinned = Glossary.parse("希尔德 → Hilde")
    learned, events = learn_stable(Glossary(), proposed, pinned)
    assert learned.lookup("希尔德") is None
    assert pinned.lookup("希尔德").translation == "Hilde"


def test_people_mapping_quarantines_base_term_without_discarding_people():
    existing = Glossary.parse("色雷斯 → Thracians")
    proposed = Glossary.parse("色雷斯人 → Thracians # people")
    learned, events = learn_stable(existing, proposed, Glossary())
    assert learned.lookup("色雷斯") is None
    assert learned.lookup("色雷斯人").translation == "Thracians"
    assert any(e["event"] == "quarantined" and e["term"] == "色雷斯" for e in events)


def test_place_note_detects_demonym_and_leaves_unknown_names_alone():
    learned, events = learn_stable(Glossary(), Glossary.parse("色雷斯 → Thracians # region\n汉鲁凡戴 → Hanrufande"), Glossary())
    assert learned.lookup("色雷斯") is None
    assert learned.lookup("汉鲁凡戴") is not None
    assert next(e for e in events if e["term"] == "汉鲁凡戴")["category"] == "unclassified"


def test_prompt_groups_types_and_keeps_notes_out_of_rendering():
    glossary = Glossary.parse("希尔德 → Hilde # name\n色雷斯 → Thrace # place\n色雷斯人 → Thracians # people")
    assert glossary.lookup("希尔德").translation == "Hilde"
    block = glossary.prompt_block("希尔德走向色雷斯，遇见色雷斯人。")
    assert "[name]" in block and "[place]" in block and "[people]" in block
    assert "notes describe context" in block


def test_review_history_survives_append_and_truncated_record(tmp_path):
    path = tmp_path / "review.jsonl"
    append_review(path, [dict(event="conflict", term="名字", kept="A", proposed="B")])
    append_review(path, [dict(event="quarantined", term="地方", proposed="People")])
    with path.open("a", encoding="utf-8") as stream:
        stream.write('{"incomplete":')
    assert [e["event"] for e in read_review(path)] == ["conflict", "quarantined"]
    append_review(path, [dict(event="learned", term="后来", proposed="Later")])
    assert read_review(path)[-1]["proposed"] == "Later"


def test_category_can_be_enriched_without_changing_established_name():
    learned, _ = learn_stable(Glossary.parse("希尔德 → Hilde"),
                              Glossary.parse("希尔德 → Hilde # name"), Glossary())
    assert "[name]" in learned.prompt_block("希尔德")


def test_audit_does_not_accept_short_name_inside_different_long_name():
    report = audit_translations([("ch1", "u1", "亚历克斯", "Alexander arrived.")],
                                Glossary.parse("亚历克斯 → Alex # name"), target="en")
    assert report["counts"] == {"term_rendering_absent": 1}


def test_audit_reports_locations_without_rewriting_accepted_text(tmp_path):
    glossary = Glossary.parse("希尔德 → Hilde # name")
    pairs = [("text/ch_1.xhtml", "job-1", "希尔德来了。", "Hilda arrived."),
             ("text/ch_2.xhtml", "job-2", "她说话了。", "Here is the translation: She spoke.")]
    events = [dict(event="conflict", term="希尔德", kept="Hilde", proposed="Hilda")]
    report = audit_translations(pairs, glossary, events, "en", "zh")
    assert report["counts"] == {"glossary_conflict": 1, "term_rendering_absent": 1, "translation_guard": 1}
    assert report["findings"][0]["locations"][0]["chapter"] == "text/ch_1.xhtml"
    assert pairs[0][3] == "Hilda arrived."
    path = tmp_path / "report.json"
    write_audit(path, pairs, glossary, target="en", source_language="zh")
    assert json.loads(path.read_text(encoding="utf-8"))["checked_units"] == 2


def test_final_report_reads_only_target_slots_not_bilingual_source(tmp_path):
    loader = EPUBBookLoader.__new__(EPUBBookLoader)
    loader.language, loader.source_lang = "en", "zh"
    loader.translate_model = SimpleNamespace(glossary=Glossary(), handoff_path=None)
    loader.p_to_save = ["She arrived."]
    loader.output_path = lambda name: str(tmp_path / name)
    plans = [SimpleNamespace(item=SimpleNamespace(file_name="ch1.xhtml"), jobs=[
        SimpleNamespace(job_id="id1", source_text="她走到了。", global_index=0)])]
    loader._write_consistency_report(plans, "book")
    report = json.loads((tmp_path / "book_translation_report.json").read_text(encoding="utf-8"))
    assert report["checked_units"] == 1
    assert report["findings"] == []


def test_toc_checkpoints_each_group_and_resume_skips_approved_labels():
    class Translator:
        TRANSLATION_ERROR_MARKER = None
        _fatal_error_detected = False

        def __init__(self):
            self.calls = []
            self.fail = True

        def translate(self, text):
            return self.translate_list([text])[0]

        def translate_list(self, texts):
            self.calls.append(list(texts))
            if self.fail and len(self.calls) == 2:
                raise RuntimeError("simulated interruption")
            return ["Approved " + text for text in texts]

    loader = EPUBBookLoader.__new__(EPUBBookLoader)
    loader.translate_metadata, loader.is_test, loader.plan_mode = True, False, False
    loader.whole_chapter = True  # TOC bounds apply even here
    loader.request_max_units, loader.request_max_tokens = 2, 4000
    loader.max_chunk_attempts = 1
    loader.language, loader.source_lang = "en", "en"
    loader.p_to_save, loader._localized_cache = [], {}
    loader.translate_model = Translator()
    snapshots = []
    loader._save_progress = lambda: snapshots.append(dict(loader._localized_cache))
    plan = SimpleNamespace(item=SimpleNamespace(file_name="chapter.xhtml"), selected=True,
                           navigation_labels=[], jobs=[])
    records = [(None, ("chapter.xhtml", str(i)), f"Title {i}", None, None) for i in range(5)]
    with pytest.raises(RuntimeError, match="simulated interruption"):
        loader._translate_toc_batch([plan], records)
    assert len(snapshots[-1]) == 2
    loader.translate_model.fail = False
    loader.translate_model.calls = []
    loader._translate_toc_batch([plan], records)
    assert loader.translate_model.calls == [["Title 2", "Title 3"], ["Title 4"]]
    assert len(loader._localized_cache) == 5


def test_complete_epub_run_bounds_requests_and_emits_report(tmp_path, monkeypatch):
    from ebooklib import epub

    class Model:
        TRANSLATION_ERROR_MARKER = None
        _fatal_error_detected = False

        def __init__(self, *args, **kwargs):
            self.calls = []

        def translate(self, text):
            self.calls.append([text])
            return "Translated English passage."

        def translate_list(self, texts):
            self.calls.append(list(texts))
            return ["Translated English passage." for _ in texts]

    monkeypatch.chdir(tmp_path)
    book = epub.EpubBook()
    book.set_identifier("bounded-policy-test")
    book.set_title("测试书名")
    book.set_language("zh")
    chapters = []
    for i in range(3):
        chapter = epub.EpubHtml(uid=f"c{i}", file_name=f"ch{i}.xhtml", title=f"章节标题{i}", lang="zh")
        chapter.content = "<html><body>" + "".join(f"<p>这是第{n}段测试文字。</p>" for n in range(5)) + "</body></html>"
        book.add_item(chapter)
        chapters.append(chapter)
    book.toc = tuple(epub.Link(c.file_name, c.title, c.id) for c in chapters)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", *chapters]
    path = tmp_path / "fixture.epub"
    epub.write_epub(str(path), book)
    loader = EPUBBookLoader(str(path), Model, key="", resume=False, language="en", source_lang="zh")
    loader.accumulated_num = 4000
    loader.request_max_units = 2
    loader.make_bilingual_book()
    assert max(map(len, loader.translate_model.calls)) <= 2
    assert (tmp_path / "fixture_bilingual.epub").exists()
    report = json.loads((tmp_path / "fixture_translation_report.json").read_text(encoding="utf-8"))
    assert report["checked_units"] >= 15
    assert report["counts"] == {}
    assert not (tmp_path / ".fixture.temp.bin").exists()
