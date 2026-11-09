import json

import pytest

from book_maker.source_windows import (
    SourceWindow,
    SourceWindowError,
    WindowUnit,
    context_only_labels,
    extraction_candidates,
    source_window_prompt,
    validate_extraction,
)


def _window(source="一色和桃莉与食蜂操祈一同出现。"):
    unit = WindowUnit("u1", "u1", source, 0, "chapter-1")
    return SourceWindow("w000006", (unit,), 10)


def _reply(names, excluded=()):
    return json.dumps(
        {
            "window_id": "w000006",
            "names": names,
            "excluded_candidates": list(excluded),
        },
        ensure_ascii=False,
    )


def test_text202_rejected_short_name_can_be_excluded_from_longer_context_label():
    window = _window()
    context = set(context_only_labels(window, "旧摘要提到${御坂美琴}。"))
    assert context == {"御坂美琴"}

    first_reply = _reply(["一色", "御坂", "桃莉", "食蜂操祈"])
    # A context substring is not evidence that it occurs in this window.
    with pytest.raises(SourceWindowError, match="does not occur literally"):
        validate_extraction(first_reply, window, context_only=context)

    candidates = set(extraction_candidates(first_reply, window))
    assert candidates == {"一色", "桃莉", "食蜂操祈"}
    retry = _reply(["一色", "桃莉", "食蜂操祈"], ["御坂"])
    extracted = validate_extraction(
        retry, window, candidates, context_only=context
    )

    assert {row["source"] for row in extracted} == candidates
    assert all(row["source"] != "御坂" for row in extracted)


def test_text202_context_substring_is_never_accepted_as_a_name():
    window = _window()
    context = context_only_labels(window, "旧摘要提到${御坂美琴}。")

    with pytest.raises(SourceWindowError, match="does not occur literally"):
        validate_extraction(
            _reply(["御坂"]), window, context_only=context
        )


def test_text202_context_substring_cannot_hide_text_in_current_window():
    window = _window("御坂正要开口。")
    context = context_only_labels(window, "旧摘要提到${御坂美琴}。")

    with pytest.raises(SourceWindowError, match="cannot hide literal"):
        validate_extraction(
            _reply([], ["御坂"]), window, context_only=context
        )


def test_text202_unrelated_exclusion_is_ignored_without_becoming_a_decision():
    window = _window()
    context = context_only_labels(window, "旧摘要提到${御坂美琴}。")
    ignored = []

    assert validate_extraction(
        _reply([], ["完全无关"]),
        window,
        context_only=context,
        ignored_exclusions=ignored,
    ) == []
    assert ignored == ["完全无关"]


@pytest.mark.parametrize("label", ["坂美", "御"])
def test_text202_unrelated_interior_and_single_character_extras_are_ignored(label):
    window = _window()
    context = context_only_labels(window, "旧摘要提到${御坂美琴}。")
    ignored = []

    assert validate_extraction(
        _reply([], [label]), window, context_only=context,
        ignored_exclusions=ignored,
    ) == []
    assert ignored == [label]


def test_text202_trailing_name_part_can_be_excluded_but_not_protected():
    window = _window()
    context = context_only_labels(window, "旧摘要提到${御坂美琴}。")

    with pytest.raises(SourceWindowError, match="does not occur literally"):
        validate_extraction(
            _reply(["美琴"]), window, context_only=context
        )
    assert validate_extraction(
        _reply([], ["美琴"]), window, context_only=context
    ) == []


def test_text202_prompt_explains_context_substring_exclusion_boundary():
    window = _window()
    prompt = source_window_prompt(
        window,
        "旧摘要提到${御坂美琴}。",
        "Chinese",
        context_only=["御坂美琴"],
    )

    assert "shorter exact leading or trailing part" in prompt
    assert "at least two characters" in prompt
    assert "unless that exact spelling occurs literally in source_units" in prompt
