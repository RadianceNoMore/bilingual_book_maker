import json
from types import SimpleNamespace

import pytest
from book_maker.glossary import Glossary, GlossaryEntry
from book_maker.glossary_gate import (GlossaryRejected, load_approved, review_handoff,
    register_chunk, transaction, commit, key_for, scope)
from book_maker.loader.epub_loader import EPUBBookLoader
from book_maker.session_context import SessionHistory, WindowText
from book_maker.translator.base_translator import Base

SOURCES = ["初春饰利看着屏幕。", "白井黑子走了。"]
OUTPUTS = ["Uiharu Kazari watched the screen.", "Shirai Kuroko left."]
ENTRY = {"term": "初春饰利", "translation": "Uiharu Kazari", "note": "name"}
SECOND = {"term": "白井黑子", "translation": "Shirai Kuroko", "note": "name"}

def terms(*entries):
    return Glossary([GlossaryEntry(**e) for e in entries or (ENTRY,)])

def vote(target="Uiharu Kazari", entry=None, reason="Source identity checked"):
    e = entry or ENTRY
    return {"id": key_for(e["term"], e["translation"]), "translation": target, "reason": reason}

def verdict(*votes):
    return json.dumps({"votes": list(votes) or [vote()]}, ensure_ascii=False)

class Model(Base):
    CHUNK_GLOSSARY_REVIEW = True
    glossary_auto_on = True
    language = "en"
    source_language = "zh"
    def __init__(self, tmp_path, replies=()):
        self.handoff_path = tmp_path / "book_handoff.md"
        self.glossary_source_sha256 = "book-content-hash"
        self.pinned, self.learned = Glossary(), Glossary()
        self.requests, self.replies = [], iter(replies)
        self.session = SessionHistory()
        self.compact_budget = 100000
        self.reports = []
        self._glossary_gate_active = True
        self._glossary_correcting = False
        self.no_context_compact = False
    def rotate_key(self):
        pass
    def translate(self, text):
        return text
    def _session_budget(self):
        return self.compact_budget
    def glossary_review_request(self, prompt):
        self.requests.append(prompt)
        return next(self.replies)
    def _compact_session(self):
        report = self.reports.pop(0)
        self._learn_from_handoff(report, WindowText.from_messages(self.session.messages()))
        self.session.reset("Reviewed factual summary")

def loader_for(model):
    loader = EPUBBookLoader.__new__(EPUBBookLoader)
    loader.translate_model = model
    loader.language, loader.source_lang = "en", "zh"
    loader.max_chunk_attempts = 1
    loader.retry_one_by_one = False
    loader.p_to_save = []
    loader._localized_cache = {}
    return loader

def handoff(entry=ENTRY):
    return "## Summary\nThe characters meet.\n## Renderings\n<renderings>\n" + entry["term"] + " → " + entry["translation"] + " # name\n</renderings>"

def test_normal_chunks_do_not_request_glossary_extraction_or_voting(tmp_path):
    model = Model(tmp_path)
    loader = loader_for(model)
    def translate(texts, *args):
        model._record_session_exchange(json.dumps(texts), json.dumps(OUTPUTS))
        return OUTPUTS
    loader._translate_texts_attempt = translate
    assert loader._translate_cleaned_texts_aligned(SOURCES) == OUTPUTS
    assert not model.requests
    assert len(model.session.messages()) == 2
    assert len(model._glossary_chunks) == 1

def test_compaction_uses_native_extraction_and_pass_does_not_retranslate(tmp_path):
    model = Model(tmp_path, [verdict(), verdict(), verdict()])
    model.compact_budget = 1
    model.reports = [handoff()]
    loader = loader_for(model)
    calls = []
    def translate(texts, *args):
        calls.append(list(texts))
        model._record_session_exchange(json.dumps(texts), json.dumps(OUTPUTS))
        return OUTPUTS
    loader._translate_texts_attempt = translate
    assert loader._translate_cleaned_texts_aligned(SOURCES) == OUTPUTS
    assert calls == [SOURCES]
    assert len(model.requests) == 3
    assert not model._glossary_repairs
    assert model.approved_glossary.lookup(ENTRY["term"])
    assert all("source_context" in p and SOURCES[0] in p for p in model.requests)
    assert not model._glossary_window_chunks

def test_fresh_round_does_not_combine_first_and_fourth_vote(tmp_path):
    model = Model(tmp_path, [verdict(vote(x)) for x in ("A", "B", "C", "A", "D", "D")])
    review_handoff(model, terms())
    decision = model._glossary_decisions[key_for(ENTRY["term"], ENTRY["translation"])]
    assert decision["translation"] == "D"
    assert len(decision["rounds"]) == 1 and len(decision["votes"]) == 3
    assert len(model.requests) == 6

def test_only_unresolved_entries_receive_next_fresh_round(tmp_path):
    replies = [verdict(vote(), vote("A", SECOND)), verdict(vote(), vote("B", SECOND)),
               verdict(vote(), vote("C", SECOND)), *[verdict(vote("D", SECOND)) for _ in range(3)]]
    model = Model(tmp_path, replies)
    review_handoff(model, terms(ENTRY, SECOND))
    assert all(ENTRY["term"] not in p for p in model.requests[3:])
    assert len(model._glossary_decisions[key_for(ENTRY["term"], ENTRY["translation"])]["votes"]) == 3

def test_rounds_are_bounded_across_resume(tmp_path):
    model = Model(tmp_path, [verdict(vote(x)) for x in ("A", "B", "C")])
    model.max_chunk_attempts = 1
    with pytest.raises(GlossaryRejected, match="fresh-round limit"):
        review_handoff(model, terms())
    restored = Model(tmp_path)
    restored.max_chunk_attempts = 1
    with pytest.raises(GlossaryRejected, match="fresh-round limit"):
        review_handoff(restored, terms())
    assert not restored.requests

def test_independent_reviewers_never_see_other_votes(tmp_path):
    model = Model(tmp_path, [verdict(vote(reason="SECRET_FIRST_VOTE")), verdict(), verdict()])
    review_handoff(model, terms())
    assert all("SECRET_FIRST_VOTE" not in prompt for prompt in model.requests)

def test_settled_and_pinned_entries_never_receive_new_votes(tmp_path):
    model = Model(tmp_path, [verdict(), verdict(), verdict()])
    review_handoff(model, terms())
    review_handoff(model, terms())
    review_handoff(model, terms(dict(ENTRY, translation="different")))
    model.pinned = terms(SECOND)
    review_handoff(model, terms(SECOND))
    assert len(model.requests) == 3

def test_empty_native_extraction_requires_no_review(tmp_path):
    model = Model(tmp_path)
    assert review_handoff(model, Glossary()) == ""
    assert not model.requests

@pytest.mark.parametrize("bad", ['not json', '{"votes":[]}', verdict({"id":"wrong","translation":"X","reason":"x"})])
def test_malformed_votes_fail_closed_and_keep_pending_handoff(tmp_path, bad):
    model = Model(tmp_path, [bad])
    with pytest.raises(GlossaryRejected):
        review_handoff(model, terms())
    restored = Model(tmp_path)
    load_approved(restored)
    assert restored._glossary_pending_handoff["entries"] == [ENTRY]

def test_partial_round_resumes_only_missing_votes(tmp_path):
    model = Model(tmp_path, [verdict()])
    with pytest.raises(StopIteration):
        review_handoff(model, terms())
    restored = Model(tmp_path, [verdict(), verdict()])
    review_handoff(restored, terms())
    assert len(restored.requests) == 2
    assert restored.approved_glossary.lookup(ENTRY["term"])

def test_rejected_original_retranslates_only_containing_chunks_and_discards_new_learning(tmp_path):
    wrong = dict(ENTRY, translation="Wrong Name")
    model = Model(tmp_path, [verdict(vote("Uiharu Kazari", wrong)) for _ in range(3)])
    affected = register_chunk(model, SOURCES, ["Wrong Name watched the screen.", OUTPUTS[1]])
    unrelated = register_chunk(model, ["今日は晴れです。"], ["It is sunny today."])
    review_handoff(model, terms(wrong))
    loader = loader_for(model)
    loader.p_to_save = ["Wrong Name watched the screen.", OUTPUTS[1], "It is sunny today."]
    loader._glossary_jobs = [SimpleNamespace(source_text=s, global_index=i, job_id=str(i))
                            for i, s in enumerate(SOURCES + ["今日は晴れです。"])]
    calls = []
    history = model.session.messages()
    def translate(texts, *args):
        calls.append(list(texts))
        assert model._glossary_correcting
        assert "Uiharu Kazari" in model._glossary_review_feedback
        model._record_session_exchange("Corrective text with NEW TERM", "corrected output")
        return OUTPUTS
    loader._translate_texts_attempt = translate
    loader._repair_glossary_chunks()
    assert calls == [SOURCES]
    assert loader.p_to_save == OUTPUTS + ["It is sunny today."]
    assert model.session.messages() == history
    assert model._glossary_chunks[affected]["translations"] == OUTPUTS
    assert model._glossary_chunks[unrelated]["translations"] == ["It is sunny today."]
    assert len(model.requests) == 3  # No extraction/review on corrective pass.
    assert not model._glossary_correcting

def test_current_chunk_can_be_corrected_by_its_own_compaction(tmp_path):
    wrong = dict(ENTRY, translation="Wrong Name")
    model = Model(tmp_path, [verdict(vote("Uiharu Kazari", wrong)) for _ in range(3)])
    model.compact_budget = 1
    model.reports = [handoff(wrong)]
    loader = loader_for(model)
    calls = []
    def translate(texts, *args):
        calls.append(list(texts))
        result = OUTPUTS if model._glossary_correcting else ["Wrong Name watched.", OUTPUTS[1]]
        model._record_session_exchange(json.dumps(texts), json.dumps(result))
        return result
    loader._translate_texts_attempt = translate
    assert loader._translate_cleaned_texts_aligned(SOURCES) == OUTPUTS
    assert calls == [SOURCES, SOURCES]
    assert len(model.requests) == 3

def test_null_majority_uses_rejection_feedback_without_new_glossary_loop(tmp_path):
    model = Model(tmp_path, [verdict(vote(None)) for _ in range(3)])
    chunk = register_chunk(model, SOURCES, OUTPUTS)
    review_handoff(model, terms())
    loader = loader_for(model)
    def translate(texts, *args):
        assert "Rejected 初春饰利" in model._glossary_review_feedback
        return OUTPUTS
    loader._translate_texts_attempt = translate
    loader._repair_glossary_chunks()
    assert model._glossary_repairs[chunk]["status"] == "done"
    assert not model.approved_glossary
    assert len(model.requests) == 3

def test_completed_repairs_reapply_after_ledger_checkpoint_crash_gap(tmp_path):
    wrong = dict(ENTRY, translation="Wrong Name")
    model = Model(tmp_path, [verdict(vote("Uiharu Kazari", wrong)) for _ in range(3)])
    register_chunk(model, SOURCES, ["Wrong Name watched.", OUTPUTS[1]])
    review_handoff(model, terms(wrong))
    loader = loader_for(model)
    loader._translate_texts_attempt = lambda *args: OUTPUTS
    loader._repair_glossary_chunks()
    restored = Model(tmp_path)
    load_approved(restored)
    resumed = loader_for(restored)
    resumed.p_to_save = ["Wrong Name watched.", OUTPUTS[1]]
    resumed._glossary_jobs = [SimpleNamespace(source_text=s, global_index=i, job_id=str(i)) for i, s in enumerate(SOURCES)]
    resumed._localized_cache = {"nav:" + SOURCES[0]: "Wrong Name watched."}
    resumed._repair_glossary_chunks()
    assert resumed.p_to_save == OUTPUTS
    assert resumed._localized_cache["nav:" + SOURCES[0]] == OUTPUTS[0]
    assert not restored.requests

def test_resume_checks_source_identity_and_majority_record(tmp_path):
    model = Model(tmp_path, [verdict(), verdict(), verdict()])
    review_handoff(model, terms())
    restored = Model(tmp_path)
    restored.glossary_source_sha256 = "different book"
    with pytest.raises(ValueError, match="different language run"):
        load_approved(restored)
    path = tmp_path / "book_approved_glossary.json"
    data = json.loads(path.read_text(encoding="utf8"))
    next(iter(data["decisions"].values()))["votes"] = []
    path.write_text(json.dumps(data), encoding="utf8")
    with pytest.raises(ValueError, match="majority"):
        load_approved(Model(tmp_path))

def test_previous_two_pass_ledger_remains_valid_without_new_votes(tmp_path):
    model = Model(tmp_path)
    legacy = {"approved": True, "reviewed_terms": [ENTRY["term"]]}
    (tmp_path / "book_approved_glossary.json").write_text(json.dumps({
        "version": 1, "scope": scope(model), "chunks": [{"entries": [ENTRY], "reviews": [legacy, legacy]}]
    }), encoding="utf8")
    review_handoff(model, terms())
    assert model.approved_glossary.lookup(ENTRY["term"])
    assert not model.requests

def test_legacy_unreviewed_checkpoint_is_refused_before_translation(tmp_path):
    loader = loader_for(Model(tmp_path))
    loader.epub_name = str(tmp_path / "source.epub")
    (tmp_path / "source.epub").write_bytes(b"source")
    loader.resume = True
    loader.p_to_save = ["old output"]
    with pytest.raises(ValueError, match="Restart without --resume"):
        loader.make_bilingual_book()

def test_provider_without_review_adapter_fails_explicitly(tmp_path):
    loader = loader_for(SimpleNamespace(glossary_auto_on=True))
    with pytest.raises(ValueError, match="requires an OpenAI-compatible"):
        loader.make_bilingual_book()


@pytest.mark.parametrize("localize", [False, True])
def test_finished_epub_replays_corrected_cached_text_without_old_name(tmp_path, monkeypatch, localize):
    import zipfile
    from ebooklib import epub
    book = epub.EpubBook()
    book.set_identifier("compaction-review-fixture")
    book.set_title("Test Book")
    book.set_language("zh")
    chapter = epub.EpubHtml(title="Opening", file_name="chapter.xhtml", lang="zh")
    heading = "<h1>初春饰利</h1>" if localize else ""
    chapter.content = "<html><body>" + heading + "<p>初春饰利看着屏幕。</p><p>白井黑子走了。</p></body></html>"
    book.add_item(chapter)
    book.add_item(epub.EpubNcx())
    book.toc = (epub.Link("chapter.xhtml", "初春饰利" if localize else "Opening", "opening"),)
    book.spine = [chapter]
    source = tmp_path / "source.epub"
    epub.write_epub(str(source), book)
    wrong = dict(ENTRY, translation="Wrong Name")
    model = Model(tmp_path, [verdict(vote("Uiharu Kazari", wrong)) for _ in range(3)])
    model.reports = [handoff(wrong)]
    model._fatal_error_detected = False
    model.context_mode = "session"
    loader = EPUBBookLoader(str(source), lambda *a, **k: model, "unused", False, "en",
                            source_lang="zh", single_translate=True, disclose=False)
    loader.translate_metadata = localize
    loader.translate_tags = "h1,p"
    loader.accumulated_num = 4000
    loader.accumulate_across_chapters = True
    loader.quiet = True
    loader.output_dir = str(tmp_path / "output")
    (tmp_path / "output").mkdir()
    calls = []
    def translate(texts, *args):
        calls.append(list(texts))
        mapping = {SOURCES[0]: OUTPUTS[0] if model._glossary_correcting else "Wrong Name watched the screen.",
                   SOURCES[1]: OUTPUTS[1], "初春饰利": "Uiharu Kazari" if model._glossary_correcting else "Wrong Name"}
        values = [mapping.get(source, source) for source in texts]
        model._record_session_exchange(json.dumps(texts), json.dumps(values))
        return values
    monkeypatch.setattr(loader, "_translate_texts_attempt", translate)
    monkeypatch.setattr(loader, "_write_consistency_report", lambda *a: None)
    loader.make_bilingual_book()
    output = tmp_path / "output" / "source_bilingual.epub"
    assert output.exists()
    with zipfile.ZipFile(output) as archive:
        text = archive.read("EPUB/chapter.xhtml").decode("utf8")
        if localize:
            ncx = archive.read("EPUB/toc.ncx").decode("utf8")
            assert "Uiharu Kazari" in ncx and "Wrong Name" not in ncx
    assert "Uiharu Kazari" in text and "Wrong Name" not in text
    assert "Shirai Kuroko" in text
    assert calls.count(SOURCES) == 2
    assert len(model.requests) == 3


def test_real_compaction_restores_original_renderings_prompt(tmp_path):
    from book_maker.translator.chatgptapi_translator import ChatGPTAPI
    from book_maker.session_context import handoff_prompt
    model = Model(tmp_path, [verdict(), verdict(), verdict()])
    register_chunk(model, SOURCES, OUTPUTS)
    model.session.append(json.dumps(SOURCES), json.dumps(OUTPUTS))
    model.style_note = ""
    captured = []
    def compact_request(messages):
        captured.append(messages[-1]["content"])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=handoff()))])
    model._compact_request = compact_request
    model._note_usage = lambda completion: None
    model._show_handoff = lambda report: None
    ChatGPTAPI._compact_session(model)
    assert captured == [handoff_prompt(with_glossary=True, with_style=False)]
    assert "At most 16 names" in captured[0]
    assert len(model.requests) == 3
    assert not model._glossary_window_chunks


def test_uncompacted_tail_and_pending_repairs_survive_resume(tmp_path):
    wrong = dict(ENTRY, translation="Wrong Name")
    model = Model(tmp_path, [verdict(vote("Uiharu Kazari", wrong)) for _ in range(3)])
    chunk = register_chunk(model, SOURCES, ["Wrong Name watched.", OUTPUTS[1]])
    restored = Model(tmp_path)
    load_approved(restored)
    assert restored._glossary_window_chunks == [chunk]
    review_handoff(model, terms(wrong))
    resumed = Model(tmp_path)
    load_approved(resumed)
    assert resumed._glossary_repairs[chunk]["status"] == "pending"
    loader = loader_for(resumed)
    loader._translate_texts_attempt = lambda *args: OUTPUTS
    loader._repair_glossary_chunks()
    assert resumed._glossary_repairs[chunk]["status"] == "done"
    assert not resumed.requests
