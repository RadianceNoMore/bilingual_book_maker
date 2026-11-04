"""`--use_context session` inside ChatGPTAPI: prefix stability and compaction.

Window mode's regression tests live in test_chatgptapi_translator.py; the ones
here are about the new mode, and above all about the invariant that pays for
it — the prefix of request N+1 must be exactly the prefix of request N plus
appended messages, or the endpoint's cache misses and session mode costs more
than the mode it replaces.
"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from book_maker.session_context import handoff_prompt
from openai import LengthFinishReasonError

from book_maker.translator.chatgptapi_translator import (
    ChatGPTAPI,
    batch_field_name,
    single_field_name,
)

SINGLE_FIELD = single_field_name("Chinese")
BATCH_FIELD = batch_field_name("Chinese")

# A phrase unique to the compact turn, taken from the real prompt so the
# tests cannot drift from it.
HANDOFF_MARKER = handoff_prompt()[:40]


def _usage(cached_tokens):
    if cached_tokens is None:
        return None
    return SimpleNamespace(
        prompt_tokens=100,
        prompt_tokens_details=SimpleNamespace(cached_tokens=cached_tokens),
    )


def _completion(content, cached_tokens=None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(message=SimpleNamespace(content=content, refusal=None))
        ],
        usage=_usage(cached_tokens),
    )


def _parsed_completion(content, cached_tokens=None):
    """`.parse` style completion — what a "strict" endpoint answers with."""
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    parsed=SimpleNamespace(**{SINGLE_FIELD: content}),
                    refusal=None,
                    content=None,
                ),
                finish_reason="stop",
            )
        ],
        usage=_usage(cached_tokens),
    )


def _translator(replies=None, verdict="unsupported", cached_tokens=None, **kwargs):
    """A ChatGPTAPI wired to a scripted client. No network, real __init__.

    `verdict` picks the path under test: the default sends every translation
    down the plain path, `"strict"` sends it through Structured Outputs — the
    path session mode actually uses.
    """
    kwargs.setdefault("context_flag", True)
    kwargs.setdefault("context_mode", "session")
    t = ChatGPTAPI(key="k", language="Chinese", **kwargs)
    t.model = "test-model"
    t.capabilities.record("test-model", verdict)

    sent = []
    answers = iter(replies or [])

    def _reply():
        try:
            return next(answers)
        except StopIteration:
            return "译文"

    def create(**call):
        sent.append(call)
        return _completion(_reply(), cached_tokens)

    def parse(**call):
        sent.append(call)
        return _parsed_completion(_reply(), cached_tokens)

    t.openai_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=Mock(side_effect=create),
                parse=Mock(side_effect=parse),
            )
        )
    )
    t.sent = sent
    return t


def _prefix(call):
    """Everything but the final (fresh) user message."""
    return call["messages"][:-1]


def test_real_framed_route_uses_prepared_context_and_restores_names_before_history(tmp_path):
    from book_maker.protected_names import ProtectedNames, NameAnnotations, unit_request
    from book_maker.glossary import Glossary
    from book_maker.translator.base_translator import BATCH_DELIMITER
    from book_maker.translation_quality import validate_translation_texts
    import re

    sources=['阿奇拉是马其顿的王。','阿奇拉说话了。']
    annotation=NameAnnotations(sources,Glossary.parse('阿奇拉 → Archelaus # name'))
    tags=[re.search(r'<bbm-name.*?</bbm-name>',text).group().replace('>阿奇拉<','>Wrong King<') for text in annotation.sent]
    extraction='## Summary\n阿奇拉是马其顿的王。\n## Renderings\n<renderings>\n阿奇拉 → Wrong King # name\n</renderings>'
    vote=json.dumps({'votes':[{'term':'阿奇拉','translation':'Archelaus','reason':'Original identity checked'}]})
    identities,_=unit_request(annotation.sent)
    response='<bbm-response>'+''.join(f'<bbm-unit id="{identity}">English prose {tag}.</bbm-unit>' for identity,tag in zip(identities,tags))+'</bbm-response>'
    t=_translator([extraction,vote,vote,vote,response],glossary_auto=True)
    t.language='English';t.protected_names=True
    assert not t.glossary_auto_on
    book=tmp_path/'book.epub';book.write_bytes(b'fixture')
    state=ProtectedNames(t,book,False)
    result=state.translate(sources,t.translate_list,lambda out:validate_translation_texts(sources,out,'en','zh'))
    assert result==['English prose Archelaus.']*2
    assert len(t.sent)==5
    assert 'original name' in t.sent[-1]['messages'][-1]['content']
    assert 'approved="Archelaus"' in t.sent[-1]['messages'][-1]['content']
    assert '是马其顿的王。' in t.sent[-1]['messages'][-1]['content']
    assert '## Summary' not in t.sent[-1]['messages'][-1]['content']
    assert len(t.session.messages())==2
    assert 'Wrong King' not in t.session.messages()[-1]['content']


def test_language_retry_feedback_reaches_plain_and_structured_user_prompts():
    from book_maker.loader.helper import untranslated_retry_feedback

    translator = _translator()
    with untranslated_retry_feedback(translator):
        plain = translator._user_content("source passage")
        structured = translator._create_structured_batch_messages(["one", "two"])
        assert "previous response copied" in plain
        assert any("previous response copied" in m["content"] for m in structured)
    assert "previous response copied" not in translator._user_content("next passage")


def test_framed_response_with_length_finish_reason_is_rejected_even_if_xml_is_closed():
    from book_maker.protected_names import unit_request, NameMarkupError
    translator=_translator()
    identities,_=unit_request(['source'])
    response=f'<bbm-response><bbm-unit id="{identities[0]}">English.</bbm-unit></bbm-response>'
    completion=_completion(response)
    completion.choices[0].finish_reason='length'
    translator.openai_client.chat.completions.create=Mock(return_value=completion)
    with pytest.raises(NameMarkupError,match='truncated'):
        translator.translate_protected_units(['source'])
    assert not translator.session.messages()


def test_protected_refusal_records_exact_request_once_and_keeps_accepted_state(tmp_path):
    from book_maker.protected_names import ProtectedNames
    from book_maker.provider_refusal import ProviderRefusal
    refusal = "Your request may go against my [guidelines](https://gemini.google/policy-guidelines/). Would you like to try something else?"
    t = _translator([refusal])
    t.language = 'English'; t.protected_names = True
    t.session.append('old user poison', 'old assistant poison')
    book = tmp_path / 'book.epub'; book.write_bytes(b'fixture')
    state = ProtectedNames(t, book, False)
    t._protected_names_state = state
    state.windows = [['ordinary source']]
    state.data['windows']['ready'] = {'sources': ['ordinary source'], 'done': True,
                                      'summary': 'future summary poison'}
    before = t.session.messages()
    with pytest.raises(ProviderRefusal, match='will not be retried or split'):
        state.translate(['ordinary source'], t.translate_list, lambda _: None)
    assert len(t.sent) == 1
    request = t.sent[0]
    assert all('poison' not in m['content'] for m in request['messages'])
    assert t.session.messages() == before
    assert state.data['records'] == {}
    log = book.with_name('book_protected_names_failures.jsonl')
    records = [json.loads(line) for line in log.read_text('utf8').splitlines()]
    assert len(records) == 1
    assert records[0]['request'] == request
    assert records[0]['response'] == refusal
    assert records[0]['response_metadata']['elapsed_seconds'] >= 0
    assert not hasattr(t, '_protected_name_instruction')


@pytest.mark.parametrize('stage', ['source_preparation_request', 'glossary_review_request'])
def test_preparation_and_review_refusals_also_stop_immediately(stage):
    from book_maker.provider_refusal import ProviderRefusal
    t = _translator(["I'm sorry, I cannot help with this request."])
    with pytest.raises(ProviderRefusal):
        getattr(t, stage)('ordinary source')
    assert len(t.sent) == 1


@pytest.mark.parametrize('structured', [False, True])
def test_compaction_refusal_never_overwrites_valid_snapshot_or_retries_history(tmp_path, structured):
    from book_maker.session_context import HandoffReport, parse_snapshot
    refusal = "Your request may go against my [guidelines](https://gemini.google/policy-guidelines/). Would you like to try something else?"
    path = tmp_path / 'handoff.md'
    HandoffReport(window=1, summary='Previous valid summary.').write_snapshot(path)
    before = path.read_bytes()
    t = _translator(handoff_path=path, context_compact_at=500)
    completion = _completion('' if structured else refusal)
    if structured:
        completion.choices[0].message.refusal = refusal
        completion.choices[0].finish_reason = 'content_filter'
    t.openai_client.chat.completions.create = Mock(return_value=completion)
    failures = []
    t._protected_names_state = SimpleNamespace(record_failure=lambda *args: failures.append(args))
    t.session.append('Accepted original chunk.', 'Accepted translated chunk.')
    old_window = t.session.windows
    t._compact_session()
    assert t.openai_client.chat.completions.create.call_count == 1
    assert path.read_bytes() == before
    assert parse_snapshot(path).summary == 'Previous valid summary.'
    assert t.session.messages() == []
    assert t.session.windows == old_window + 1
    assert failures[0][0] == 'context-compaction'
    assert t._protected_last_request['messages'][-1]['role'] == 'user'
    # The next ordinary accepted exchange does not retry the refused history.
    t._record_session_exchange('short new source', 'short new translation')
    assert t.openai_client.chat.completions.create.call_count == 1


def test_resume_ignores_snapshot_containing_provider_refusal(tmp_path):
    from book_maker.session_context import HandoffReport
    refusal = "I'm sorry, it appears I can't help with this particular request, as it may go against my [guidelines](https://gemini.google/policy-guidelines/)."
    path = tmp_path / 'handoff.md'
    HandoffReport(window=2, summary=refusal).write_snapshot(path)
    t = _translator(handoff_path=path)
    assert not t.restore_session_handoff()
    assert not t.session.messages()


def _tail_containing(translator, needle):
    """The tail message of the last request that carried `needle`.

    With a tiny compact budget a compact request follows every unit, so the
    last request is not the last *translation* — say which one is meant.
    """
    for call in reversed(translator.sent):
        content = call["messages"][-1]["content"]
        if needle in content:
            return content
    raise AssertionError(f"no request carried {needle!r}")


class TestSessionHistoryGrows:
    def test_first_request_has_no_history(self):
        t = _translator()
        t.get_translation("one")
        assert _prefix(t.sent[0]) == [t.sent[0]["messages"][0]]  # system only

    def test_second_request_carries_the_first_pair(self):
        t = _translator(["一"])
        t.get_translation("one")
        t.get_translation("two")
        contents = [m["content"] for m in t.sent[1]["messages"]]
        assert any("one" in c for c in contents)
        assert "一" in contents

    def test_history_accumulates_beyond_the_window_limit(self):
        """Window mode caps at context_paragraph_limit pairs; session does not."""
        t = _translator(["1", "2", "3", "4", "5"], context_paragraph_limit=2)
        for i in range(5):
            t.get_translation(f"unit {i}")
        assert sum("unit" in m["content"] for m in t.sent[-1]["messages"]) >= 4


class TestPrefixStability:
    def test_each_request_extends_the_previous_prefix(self):
        t = _translator(["一", "二", "三"])
        for text in ("one", "two", "three"):
            t.get_translation(text)
        for earlier, later in zip(t.sent, t.sent[1:]):
            earlier_prefix = json.dumps(_prefix(earlier), ensure_ascii=False)
            later_prefix = json.dumps(_prefix(later), ensure_ascii=False)
            assert later_prefix.startswith(earlier_prefix[:-1])

    def test_each_request_replays_the_previous_one_exactly(self):
        """The strong form: request N+1 opens with request N's messages verbatim.

        Storing the raw source instead of the text actually sent leaves the
        newest pair out of the cached prefix, so every request re-reads a
        paragraph at full input price for the life of the run.
        """
        t = _translator(["一", "二", "三"])
        for text in ("one", "two", "three"):
            t.get_translation(text)
        for earlier, later in zip(t.sent, t.sent[1:]):
            sent_before = earlier["messages"]
            assert later["messages"][: len(sent_before)] == sent_before

    def test_system_message_is_byte_identical_across_requests(self):
        t = _translator(["一", "二"])
        t.get_translation("one")
        t.get_translation("two")
        assert t.sent[0]["messages"][0] == t.sent[1]["messages"][0]


class TestCompact:
    def test_compacts_when_the_budget_is_reached(self, tmp_path):
        t = _translator(
            ["译文", "译文", "Summary: they walked."],
            context_compact_at=10,
            handoff_path=tmp_path / "book_handoff.md",
        )
        t.get_translation("a" * 200)
        before = len(t.sent)
        t.get_translation("b" * 200)
        assert len(t.sent) > before + 1  # an extra compact request went out

    def test_next_window_is_seeded_with_the_report(self, tmp_path):
        t = _translator(
            ["译文", "Summary: they walked.", "译文"],
            context_compact_at=10,
            handoff_path=tmp_path / "book_handoff.md",
        )
        t.get_translation("a" * 200)
        t.get_translation("b" * 200)
        seeded = [m["content"] for m in t.sent[-1]["messages"]]
        assert any("they walked" in c for c in seeded)

    def test_history_shrinks_after_a_compact(self, tmp_path):
        t = _translator(
            ["译文", "Summary.", "译文"],
            context_compact_at=10,
            handoff_path=tmp_path / "book_handoff.md",
        )
        t.get_translation("a" * 400)
        long_prefix = len(_prefix(t.sent[-1]))
        t.get_translation("b" * 400)
        assert len(_prefix(t.sent[-1])) <= long_prefix + 1

    def test_report_is_persisted(self, tmp_path):
        path = tmp_path / "book_handoff.md"
        t = _translator(
            ["译文", "Summary: they walked.", "译文"],
            context_compact_at=10,
            handoff_path=path,
        )
        t.get_translation("a" * 200)
        assert "they walked" in path.read_text(encoding="utf-8")

    def test_the_file_holds_the_latest_report_only(self, tmp_path):
        """Since 260913 it is a snapshot, not a log: a second compaction
        replaces the first rather than appending to it, so the file stays the
        size of one handoff however long the book is."""
        path = tmp_path / "book_handoff.md"
        t = _translator(
            ["译文", "Summary: they walked.", "译文", "Summary: they rested."],
            context_compact_at=10,
            handoff_path=path,
        )
        t.get_translation("a" * 200)
        t.get_translation("b" * 200)
        body = path.read_text(encoding="utf-8")
        assert "they rested" in body and "they walked" not in body

    def test_no_compact_in_window_mode(self, tmp_path):
        t = _translator(["译文"] * 4, context_mode="window", context_compact_at=10)
        t.get_translation("a" * 400)
        t.get_translation("b" * 400)
        assert len(t.sent) == 2


class TestUsageMeter:
    """The cache guard is gone. What replaced it is the meter the loader pins
    on the progress bar: in/out/cached tokens, summed over every billed
    request on every path. A history read back at full price shows up as a
    `cached` count that stays at zero — the operator's call, not a warning."""

    def test_plain_requests_are_metered(self):
        t = _translator(["一"] * 3, cached_tokens=0)
        for i in range(3):
            t.get_translation(f"unit {i}")
        assert t.usage.requests == 3
        assert t.usage.prompt == 300 and t.usage.cached == 0

    def test_cache_reads_are_summed(self):
        t = _translator()
        t.openai_client.chat.completions.create = Mock(
            side_effect=lambda **c: _completion("译文", cached_tokens=64)
        )
        for i in range(3):
            t.get_translation(f"unit {i}")
        assert t.usage.cached == 192
        assert t.usage_postfix() == {"in": "300", "out": "0", "cached": "192"}

    def test_an_answer_without_usage_is_not_counted(self):
        t = _translator(["一"] * 3)  # _usage(None): the endpoint said nothing
        for i in range(3):
            t.get_translation(f"unit {i}")
        assert t.usage.requests == 0 and t.usage_postfix() is None

    # The meter has to read on the structured path too: every "strict"
    # endpoint translates through `.parse`, and that is exactly where session
    # mode is used, so a reading taken only on the plain path is never taken.
    def test_the_structured_path_is_metered(self):
        t = _translator(["一"] * 3, verdict="strict", cached_tokens=64)
        for i in range(3):
            t.get_translation(f"unit {i}")
        assert t.openai_client.chat.completions.parse.call_count == 3
        assert t.usage.requests == 3 and t.usage.cached == 192

    def test_structured_batch_requests_are_metered_too(self):
        """`--accumulated_num` sends whole batches through `.parse`. Those are
        the requests being billed, so they are the ones to read."""
        t = _translator(verdict="strict")
        t.openai_client.chat.completions.parse = Mock(
            side_effect=lambda **c: SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            parsed=SimpleNamespace(
                                **{
                                    BATCH_FIELD: [
                                        SimpleNamespace(
                                            **{"id": 0, SINGLE_FIELD: "一"}
                                        ),
                                        SimpleNamespace(
                                            **{"id": 1, SINGLE_FIELD: "二"}
                                        ),
                                    ]
                                }
                            ),
                            refusal=None,
                            content=None,
                        ),
                        finish_reason="stop",
                    )
                ],
                usage=_usage(8),
            )
        )
        for i in range(4):
            assert t.translate_list([f"a {i}", f"b {i}"]) == ["一", "二"]
        assert t.usage.requests == 4 and t.usage.cached == 32

    def test_a_truncated_structured_answer_still_counts_its_request(self):
        """The truncated request was billed before it was thrown away."""
        error = LengthFinishReasonError(
            completion=SimpleNamespace(
                usage=_usage(0),
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content='{"x":"半'),
                        finish_reason="length",
                    )
                ],
            )
        )
        t = _translator(verdict="strict", cached_tokens=0)
        t.openai_client.chat.completions.parse = Mock(side_effect=error)

        t.get_translation("one")

        # Two billed requests went out for this paragraph: the truncated
        # structured one and the plain retranslation.
        assert t.usage.requests == 2

    def test_a_compaction_turn_is_metered(self):
        """The handoff request carries the whole window and is billed for it.

        It used to be the one request no meter saw, which understated a
        session run's cost worst at short compact budgets — where compaction
        is most frequent and the run looks cheapest.
        """
        t = _translator(context_compact_at=10)
        handoff = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="Handoff report.", refusal=None)
                )
            ],
            usage=SimpleNamespace(
                prompt_tokens=1000,
                completion_tokens=300,
                prompt_tokens_details=SimpleNamespace(cached_tokens=0),
            ),
        )
        t.openai_client.chat.completions.create = Mock(return_value=handoff)
        t.session.append("one", "一")
        t.session.append("two", "二")

        before = (t.usage.prompt, t.usage.completion, t.usage.requests)
        t._compact_session()

        assert t.usage.prompt == before[0] + 1000
        assert t.usage.completion == before[1] + 300
        assert t.usage.requests == before[2] + 1


class TestWindowModeUnchanged:
    def test_window_mode_still_sends_two_context_messages(self):
        t = _translator(["一", "二"], context_mode="window", context_paragraph_limit=3)
        t.get_translation("one")
        t.get_translation("two")
        assert len(_prefix(t.sent[1])) == 3  # system + one user/assistant pair

    def test_window_mode_respects_the_paragraph_limit(self):
        t = _translator(
            ["1", "2", "3", "4"], context_mode="window", context_paragraph_limit=1
        )
        for text in ("a", "b", "c", "d"):
            t.get_translation(text)
        assert len(_prefix(t.sent[-1])) == 3


class TestOneHistoryPerRun:
    """A session run has exactly one history, and it is never cloned.

    Several workers cannot share one — chapters would interleave and the
    prefix stability the mode exists for would be gone — and a fresh
    history per chapter is window mode at session prices. So the pairing
    is refused on the command line (see tests/test_cli.py) and the loader
    only ever hands out the shared instance.
    """

    def _loader(self, tmp_path, workers):
        from book_maker.loader.epub_loader import EPUBBookLoader

        book = (
            Path(__file__).resolve().parent.parent / "test_books" / "animal_farm.epub"
        )
        target = tmp_path / book.name
        target.write_bytes(book.read_bytes())
        return EPUBBookLoader(
            str(target),
            ChatGPTAPI,
            "k",
            False,
            language="Chinese",
            context_flag=True,
            context_mode="session",
            parallel_workers=workers,
        )

    def test_sequential_runs_keep_the_shared_history(self, tmp_path):
        loader = self._loader(tmp_path, workers=1)
        assert loader._clone_translator_for_context() is loader.translate_model


class TestAsyncPathIsRefused:
    def test_session_mode_fails_loud_on_the_async_path(self):
        """It threads an immutable per-call context, so the session would
        never be appended to and every request would bill uncached."""
        import asyncio

        from book_maker.translator.base_translator import AsyncTranslationUnsupported

        t = _translator()
        with pytest.raises(AsyncTranslationUnsupported):
            asyncio.run(t.translate_async("text"))

    def test_window_mode_still_works_on_the_async_path(self):
        import asyncio

        t = _translator(context_mode="window")
        assert asyncio.iscoroutinefunction(t.translate_async)


class TestCompactResilience:
    """A compact failure must not throw away the book's accumulated context."""

    # A realistic budget: each unit below is ~200 estimated tokens, so the
    # 600-token budget trips after three of them and the window stays well
    # inside the "give up, it will only keep failing" size guard.
    BUDGET = 600
    UNIT = "x" * 800

    def _failing(self, tmp_path, failures, **kw):
        t = _translator(
            ["译文"] * 40,
            context_compact_at=self.BUDGET,
            handoff_path=tmp_path / "h.md",
            **kw,
        )
        real = t.openai_client.chat.completions.create
        state = {"left": failures}

        def create(**call):
            # The compact turn is the one asking for a handoff report.
            is_compact = HANDOFF_MARKER in call["messages"][-1]["content"]
            if is_compact and state["left"] > 0:
                state["left"] -= 1
                raise RuntimeError("boom")
            return real(**call)

        t.openai_client.chat.completions.create = Mock(side_effect=create)
        return t

    def test_a_transient_failure_keeps_the_history(self, tmp_path):
        t = self._failing(tmp_path, failures=1)
        for _ in range(3):
            t.get_translation(self.UNIT)
        assert t.session.messages(), "history was discarded on one failed compact"

    def test_it_retries_the_compact_on_the_next_unit(self, tmp_path):
        t = self._failing(tmp_path, failures=1)
        for _ in range(4):
            t.get_translation(self.UNIT)
        assert (tmp_path / "h.md").exists(), "the retry never produced a report"

    def test_it_gives_up_loudly_rather_than_growing_forever(self, tmp_path, capsys):
        t = self._failing(tmp_path, failures=99)
        for _ in range(10):
            t.get_translation(self.UNIT)
        assert "handoff report failed" in capsys.readouterr().out.lower()
        # Bounded: the window is reset rather than growing without end.
        assert t.session.estimated_tokens() <= 2 * self.BUDGET

    def test_a_failed_handoff_write_does_not_fail_the_translation(self, tmp_path):
        # A directory where the handoff file should be: writing it must fail.
        path = tmp_path / "h.md"
        path.mkdir()
        t = _translator(
            ["译文", "Summary.", "译文"], context_compact_at=10, handoff_path=path
        )
        assert t.get_translation("a" * 200) == "译文"


class TestCompactIsVisible:
    """The handoff report is what the next window inherits, so it is worth
    seeing as it happens rather than only in the file afterwards."""

    def _run(self, tmp_path, report, capsys):
        t = _translator(
            ["译文", report, "译文"],
            context_compact_at=10,
            handoff_path=tmp_path / "h.md",
        )
        t.get_translation("a" * 200)
        return capsys.readouterr().out

    def test_the_summary_is_printed(self, tmp_path, capsys):
        out = self._run(tmp_path, "They walked to the barn.", capsys)
        assert "They walked to the barn." in out

    def test_the_window_number_is_printed(self, tmp_path, capsys):
        out = self._run(tmp_path, "Summary.", capsys)
        assert "window 1" in out.lower()

    def test_square_brackets_survive_rich_markup(self, tmp_path, capsys):
        """Reports really do contain things like [PGA]; rich would eat them."""
        out = self._run(tmp_path, "Based on the [PGA] edition.", capsys)
        assert "[PGA]" in out

    def test_quiet_suppresses_the_report(self, tmp_path, capsys):
        t = _translator(
            ["译文", "They walked to the barn.", "译文"],
            context_compact_at=10,
            handoff_path=tmp_path / "h.md",
        )
        t.quiet = True
        t.get_translation("a" * 200)
        assert "They walked to the barn." not in capsys.readouterr().out

    def test_quiet_still_writes_the_file(self, tmp_path, capsys):
        """Suppressing the echo must not lose the record."""
        path = tmp_path / "h.md"
        t = _translator(
            ["译文", "They walked to the barn.", "译文"],
            context_compact_at=10,
            handoff_path=path,
        )
        t.quiet = True
        t.get_translation("a" * 200)
        assert "They walked to the barn." in path.read_text(encoding="utf-8")

    def test_quiet_does_not_silence_a_failed_compact(self, tmp_path, capsys):
        """--quiet drops echoes, not warnings."""
        t = _translator(
            ["译文"] * 6, context_compact_at=10, handoff_path=tmp_path / "h.md"
        )
        t.quiet = True
        real = t.openai_client.chat.completions.create

        def create(**call):
            # Fail only the compact turn; the translation itself must succeed
            # or the run never reaches the compact at all.
            if HANDOFF_MARKER in call["messages"][-1]["content"]:
                raise RuntimeError("boom")
            return real(**call)

        t.openai_client.chat.completions.create = Mock(side_effect=create)
        t.get_translation("a" * 200)
        assert "handoff report failed" in capsys.readouterr().out.lower()

    def test_an_unclosed_bracket_does_not_raise(self, tmp_path, capsys):
        out = self._run(tmp_path, "A stray [bracket and /close tag", capsys)
        assert "stray" in out


class TestCompactionDisabled:
    """`--no-context-compact`: roll the window over, never pay for a report.

    The seam still happens where the budget puts it — what the flag removes is
    the handoff turn and everything it carries forward, so the next window
    starts empty, like Codex's `/new`.
    """

    UNIT = "x" * 800  # ~200 estimated tokens

    def _disabled(self, **kw):
        return _translator(
            ["译文"] * 40, no_context_compact=True, context_compact_at=600, **kw
        )

    def test_it_rolls_the_window_over_without_a_handoff_turn(self):
        t = self._disabled()
        for _ in range(4):
            t.get_translation(self.UNIT)
        assert t.session.windows > 1, "the window never rolled over"
        assert not any(
            HANDOFF_MARKER in call["messages"][-1]["content"] for call in t.sent
        ), "a handoff report was requested with compaction disabled"

    def test_the_next_window_starts_empty(self):
        t = self._disabled()
        for _ in range(4):
            t.get_translation(self.UNIT)
        # Only what was appended after the reset, and no seed message before it.
        assert t.session.estimated_tokens() < 600

    def test_it_writes_no_handoff_file(self, tmp_path):
        path = tmp_path / "h.md"
        t = self._disabled(handoff_path=path)
        for _ in range(4):
            t.get_translation(self.UNIT)
        assert not path.exists(), "a handoff file was written with compaction off"

    def test_without_the_flag_a_budget_still_compacts(self):
        t = _translator(["译文"] * 40, context_compact_at=600)
        for _ in range(4):
            t.get_translation(self.UNIT)
        assert any(
            HANDOFF_MARKER in call["messages"][-1]["content"] for call in t.sent
        ), "a positive budget must still ask for the handoff report"


class TestAFixedStyleRidesTheWindowStart:
    """Decision 8: a style is a standing instruction, so it belongs where a
    window starts and nowhere else. On this route the standing channel is the
    system message — message 0, the head of the cached prefix — so it is
    still there after a rollover, and it is in no user turn at all."""

    STYLE = "Clipped, unsentimental, no adverbs."

    def test_it_is_in_the_system_message_and_no_user_turn(self):
        t = _translator(style_note=self.STYLE)
        t.translate("one", False)
        call = t.sent[0]
        assert self.STYLE in call["messages"][0]["content"]
        assert all(self.STYLE not in m["content"] for m in call["messages"][1:])

    def test_the_next_window_still_carries_it(self):
        t = _translator(
            replies=["译一", "Summary of window one.", "译二"],
            context_compact_at=1500,
            style_note=self.STYLE,
        )
        t.translate("a" * 8000, False)
        t.translate("b" * 10, False)
        assert t.session.windows == 2
        # the request after the rollover: its history is the seed, and the
        # style is where it always was
        assert self.STYLE in t.sent[-1]["messages"][0]["content"]
        assert all(self.STYLE not in m["content"] for m in t.sent[-1]["messages"][1:])

    def test_the_seed_does_not_repeat_it(self):
        """Including the seam's own seed, which is a user turn like any
        other. The style is on the standing channel for the new window
        already, and the handoff file has it verbatim; a copy here would be
        the same instruction in two places, one of which the hard seed cap
        can cut in half."""
        t = _translator(
            replies=["译一", "Summary of window one.", "译二"],
            context_compact_at=1500,
            style_note=self.STYLE,
        )
        t.translate("a" * 8000, False)
        t.translate("b" * 10, False)
        seed = t.sent[-1]["messages"][1]["content"]
        assert "continuing a translation" in seed
        assert "Summary of window one." in seed
        assert self.STYLE not in seed

    def test_the_model_is_not_asked_for_a_style_it_was_given(self):
        t = _translator(
            replies=["译一", "Summary of window one.", "译二"],
            context_compact_at=500,
            style_note=self.STYLE,
        )
        t.translate("a" * 4000, False)
        t.translate("b" * 10, False)
        compact = _tail_containing(t, HANDOFF_MARKER)
        from book_maker.session_context import _STYLE_REQUEST
        assert _STYLE_REQUEST not in compact

    def test_the_report_shows_the_users_style_verbatim(self, tmp_path, capsys):
        path = tmp_path / "h.md"
        t = _translator(
            replies=["译一", "Summary of window one.", "译二"],
            context_compact_at=500,
            style_note=self.STYLE,
            handoff_path=str(path),
        )
        t.translate("a" * 4000, False)
        t.translate("b" * 10, False)
        # the model was never asked to describe a style, so the section the
        # report shows is the operator's own words, unedited
        assert f"## Style\n\n{self.STYLE}" in path.read_text(encoding="utf-8")
