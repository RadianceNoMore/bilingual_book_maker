"""A model's own summary cannot authorize a name substitution."""

import pytest

from book_maker.glossary import Glossary
from book_maker.session_context import parse_handoff_glossary
from book_maker.translator.base_translator import Base


def translator():
    # This boundary is shared by every glossary-capable provider route.
    class Minimal(Base):
        def translate(self, text):
            return text
        def rotate_key(self):
            pass
    instance = Minimal.__new__(Minimal)
    instance.pinned = Glossary()
    instance.learned = Glossary()
    return instance


def test_wrong_model_mapping_is_preserved_as_evidence_but_never_injected():
    t = translator()
    t._accept_learned_glossary(Glossary.parse("初春饰利 → Konori Mii # people"))
    assert t.learned.lookup("初春饰利").translation == "Konori Mii"
    assert t.glossary_prompt_block("初春饰利看着屏幕。") == ""


def test_operator_pin_wins_over_model_swap_and_only_matching_units_receive_it():
    t = translator()
    t.pinned = Glossary.parse("初春饰利 → Uiharu Kazari")
    t._accept_learned_glossary(Glossary.parse("初春饰利 → Konori Mii"))
    block = t.glossary_prompt_block("初春饰利看着屏幕。")
    assert "Uiharu Kazari" in block and "Konori" not in block
    assert t.glossary_prompt_block("白井黑子走了。") == ""


def test_legacy_snapshot_mappings_are_not_promoted_to_instructions():
    t = translator()
    # Resume routes restore their old snapshots through this same method.
    t._accept_learned_glossary(Glossary.parse("固法美伟 → Yomikawa Aiho"))
    assert not t.glossary_prompt_block("固法美伟")


@pytest.mark.parametrize("line", ["艾尔芙 → Alf (people)", "> 艾尔芙 → Alf (people)"])
def test_model_category_labels_are_metadata_not_name_text(line):
    parsed = parse_handoff_glossary("<renderings>\n" + line + "\n</renderings>")
    entry = parsed.glossary.lookup("艾尔芙")
    assert entry.translation == "Alf"
    assert entry.note.lower() == "people"
