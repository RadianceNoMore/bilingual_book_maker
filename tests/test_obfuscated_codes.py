from types import SimpleNamespace
import hashlib
import pickle

import pytest
from bs4 import BeautifulSoup
from ebooklib import epub, ITEM_DOCUMENT

from book_maker.obfuscated_codes import clean_document, strip_codes
from book_maker.loader.epub_loader import EPUBBookLoader
from book_maker.translation_quality import UntranslatedTextError


CODE = "T②就起六揪翼叁捌遛"


@pytest.mark.parametrize("code", [CODE, "T2就起六揪翼叁捌遛", "Ｔ２就起六揪翼叁捌遛"])
def test_confirmed_code_variants_preserve_dialogue(code):
    assert strip_codes(code + '\u3000“啊——！”') == '“啊——！”'


@pytest.mark.parametrize("text", [
    "本群免费提取全网平台已购vip章节，制成txt等格式。有想提取的私聊群主。",
    "Join our VIP group: 12345678", "T2战车开过了广场。", "第②章",
    "T2就起六揪翼叁捌遛续", "AT2就起六揪翼叁捌遛",
])
def test_ads_and_unconfirmed_text_are_unchanged(text):
    assert strip_codes(text) == text


def loader_for(model):
    loader = EPUBBookLoader.__new__(EPUBBookLoader)
    loader.translate_model = model
    loader.language = "en"
    loader.source_lang = "zh"
    loader.plan_mode = False
    loader.strip_obfuscated_codes = True
    return loader


def test_requests_keep_slots_but_do_not_send_codes_or_empty_paragraphs():
    calls = []
    model = SimpleNamespace(translate=lambda text: calls.append(text) or '“A—aaah!”')
    loader = loader_for(model)
    assert loader._translate_texts_aligned([CODE, CODE + ' “啊——！”']) == ["", '“A—aaah!”']
    assert calls == ['“啊——！”']
    assert loader._translate_texts_aligned([CODE]) == [""]
    assert len(calls) == 1


def test_output_cleanup_handles_inline_tags_and_preserves_ads_and_anchors():
    soup = BeautifulSoup(
        '<p>T②就起<b>六揪翼</b>叁捌遛　<em>“啊——！”</em></p>'
        f'<p>{CODE}</p><p id="anchor">{CODE}</p>'
        '<p>Join our VIP group: 12345678</p>', 'html.parser')
    assert len(clean_document(soup)) == 3
    assert CODE not in soup.get_text()
    assert soup.em.string == '“啊——！”'
    assert soup.find(id="anchor") is not None
    assert len(soup.find_all('p')) == 3
    assert "Join our VIP group: 12345678" in soup.get_text()


def test_cleanup_preserves_protected_code_markup():
    soup = BeautifulSoup(f'<p><code><span>{CODE}</span></code></p>', 'html.parser')
    assert clean_document(soup) == []
    assert soup.code.get_text() == CODE


def test_existing_checkpoint_slots_and_ids_are_preserved(capsys):
    loader = loader_for(SimpleNamespace())
    loader.p_to_save = [CODE + ' “A—aaah!”', 'An ordinary ad.']
    jobs = [SimpleNamespace(source_text=CODE + ' “啊——！”', global_index=0,
                            node_index=0, batch_index=0, job_id='original-id')]
    plans = [SimpleNamespace(item=SimpleNamespace(file_name='chapter.xhtml'), jobs=jobs)]
    loader._prepare_obfuscated_codes(plans)
    loader._validate_completed_translations(plans)
    assert loader.p_to_save == ['“A—aaah!”', 'An ordinary ad.']
    assert jobs[0].job_id == 'original-id'
    assert jobs[0].source_text.startswith(CODE)
    assert 'chapter.xhtml, unit 1' in capsys.readouterr().out


def test_suspect_cached_prose_retranslates_chunk_not_just_bad_slot(monkeypatch):
    loader = loader_for(SimpleNamespace())
    loader.p_to_save = [CODE + ' 原文仍然没有翻译', 'Already translated.']
    jobs = [SimpleNamespace(source_text=CODE + ' 原文仍然没有翻译', global_index=0,
                            node_index=0, batch_index=0, unit=None),
            SimpleNamespace(source_text='第二段文字', global_index=1,
                            node_index=1, batch_index=0, unit=None)]
    calls = []
    def translate(texts, **kwargs):
        calls.append(texts)
        return ['Now translated.', 'Second paragraph.']
    monkeypatch.setattr(loader, '_translate_texts_aligned', translate)
    loader._prepare_obfuscated_codes([
        SimpleNamespace(item=SimpleNamespace(file_name='chapter.xhtml'), jobs=jobs)])
    assert calls == [[job.source_text for job in jobs]]
    assert loader.p_to_save == ['Now translated.', 'Second paragraph.']


def test_guardrail_still_rejects_untranslated_prose(monkeypatch):
    monkeypatch.setattr('tenacity.nap.time.sleep', lambda _: None)
    loader = loader_for(SimpleNamespace(translate=lambda text: text))
    with pytest.raises(UntranslatedTextError):
        loader._translate_texts_aligned([CODE + ' 原文仍然没有翻译'])


def test_cleanup_can_be_disabled():
    calls = []
    loader = loader_for(SimpleNamespace(translate=lambda text: calls.append(text) or 'A code.'))
    loader.strip_obfuscated_codes = False
    assert loader._translate_texts_aligned([CODE]) == ['A code.']
    assert calls == [CODE]


def test_epub_interrupt_and_resume_preserve_legacy_ids_and_clean_recovery(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / 'source.epub'
    book = epub.EpubBook()
    book.set_identifier('code-cleanup-fixture')
    book.set_title('Test')
    book.set_language('zh')
    chapters = []
    for index, body in enumerate([
        f'<p>{CODE}</p><p>{CODE} “啊——！”</p><p>请加入广告群</p>',
        '<p>第二章正文</p>',
    ]):
        chapter = epub.EpubHtml(uid=f'ch{index}', file_name=f'ch{index}.xhtml', title='Test')
        chapter.content = f'<html><body>{body}</body></html>'
        book.add_item(chapter)
        chapters.append(chapter)
    book.spine = chapters
    book.toc = tuple(chapters)
    epub.write_epub(str(source), book)
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()

    class Model:
        TRANSLATION_ERROR_MARKER = None
        interrupt = True
        calls = []

        def __init__(self, *args, **kwargs):
            self._fatal_error_detected = False

        def translate(self, text):
            type(self).calls.append(text)
            if text == '第二章正文' and type(self).interrupt:
                raise KeyboardInterrupt()
            return {'“啊——！”': '“A—aaah!”', '请加入广告群': 'Please join the advertising group.',
                    '第二章正文': 'Second chapter body.'}[text]

        def translate_list(self, texts):
            return [self.translate(text) for text in texts]

    def make_loader(resume):
        loader = EPUBBookLoader(str(source), Model, key='', resume=resume,
                                language='en', source_lang='zh')
        loader.translate_metadata = False
        loader.whole_chapter = True
        return loader

    loader = make_loader(False)
    with pytest.raises(SystemExit) as error:
        loader.make_bilingual_book()
    assert error.value.code == 130
    assert loader.p_to_save == ['', '“A—aaah!”', 'Please join the advertising group.']
    checkpoint = tmp_path / '.source.temp.bin'
    with checkpoint.open('rb') as file:
        state = pickle.load(file)
    old_ids = state['job_ids'][:]
    # Simulate an existing checkpoint written before this feature.
    state.pop('obfuscated_code_cleanup')
    with checkpoint.open('wb') as file:
        pickle.dump(state, file)
    recovery = epub.read_epub(str(tmp_path / 'source_bilingual_temp.epub'))
    text = ''.join(item.get_body_content().decode('utf-8')
                   for item in recovery.get_items_of_type(ITEM_DOCUMENT))
    assert CODE not in text
    assert '请加入广告群' in text
    Model.interrupt = False
    resumed = make_loader(True)
    resumed.make_bilingual_book()
    assert resumed._planned_job_ids[:3] == old_ids
    output = epub.read_epub(str(tmp_path / 'source_bilingual.epub'))
    text = ''.join(item.get_body_content().decode('utf-8')
                   for item in output.get_items_of_type(ITEM_DOCUMENT))
    assert CODE not in text
    assert '“啊——！”' in text and 'A—aaah!' in text
    assert '请加入广告群' in text and 'Please join the advertising group.' in text
    assert Model.calls.count('“啊——！”') == 1
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash
