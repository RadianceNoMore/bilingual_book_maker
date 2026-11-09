import json
import re
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile, ZIP_STORED

import pytest
from ebooklib import epub
from lxml import etree

from book_maker.glossary import Glossary
from book_maker.loader.epub_loader import EPUBBookLoader
from book_maker.protected_names import (NameAnnotations, NameMarkupError, ProtectedNames,
                                       source_windows, write_annotated_epub, _annotate_block,
                                       unit_request, parse_unit_response)
from book_maker.session_context import SessionHistory
from book_maker.translator.base_translator import Base
from book_maker.translation_quality import validate_translation_texts


class Model(Base):
    language = 'en'
    source_language = 'zh'
    glossary_auto_on = False
    protected_names = True
    model = 'offline-test'
    max_chunk_attempts = 3
    def __init__(self):
        self.pinned, self.learned = Glossary(), Glossary()
        self.session = SessionHistory()
        self.requests, self.translation_requests = [], []
        self.answers = None
        self._fatal_error_detected = False
        self.no_context_compact = False
    def rotate_key(self):
        pass
    def _session_budget(self):
        return 1000000
    def glossary_review_request(self, prompt):
        self.requests.append(prompt)
        if self.answers is not None:
            value = next(self.answers)
            if isinstance(value, Exception):raise value
            return value
        payload = json.loads(prompt.split('\n',1)[1])
        if prompt.startswith('Extract'):
            names = {'汉鲁凡戴':'Wrong Name','阿奇拉':'Wrong King'}
            return json.dumps({'entries':[{'term':t,'translation':v,'kind':'name'} for t,v in names.items()
                                          if any(t in s for s in payload['original_text'])]})
        names = {'汉鲁凡戴':'Haurvatat','阿奇拉':'Archelaus'}
        return json.dumps({'votes':[{'term':e['term'],'translation':names[e['term']],
                                    'reason':'Identity established from original passage'} for e in payload['entries']]})
    def source_preparation_request(self, prompt):
        self.requests.append(prompt)
        if self.answers is not None:
            raw=next(self.answers)
            if isinstance(raw,Exception):raise raw
            try:
                rows=json.loads(raw)['entries']
                if not isinstance(rows,list):return raw
                lines=[f"{r['term']} → {r['translation']} # {r['kind']}" for r in rows]
            except (ValueError,TypeError,KeyError):return raw
        else:
            payload=json.loads(prompt.split('ORIGINAL WINDOW:\n',1)[1])
            names={'汉鲁凡戴':'Wrong Name','阿奇拉':'Wrong King'}
            lines=[f'{t} → {v} # name' for t,v in names.items() if any(t in s for s in payload['original_text'])]
        return '## Summary\n阿奇拉是马其顿的王；汉鲁凡戴是完美天使。\n## Renderings\n<renderings>\n'+'\n'.join(lines)+'\n</renderings>'
    def translate(self, text):
        return self.translate_list([text])[0]
    def translate_list(self, texts):
        self.translation_requests.append(list(texts))
        assert 'Preserve each entire tag' in self._protected_name_instruction
        replies = []
        for text in texts:
            tags = re.findall(r'<bbm-name\b[^>]*>.*?</bbm-name>', text)
            # The model translates the protected inner name incorrectly. It
            # must still be restored from our approval, never the model's text.
            tags = [re.sub(r'>.*?</', '>Different Name</', tag) for tag in tags]
            replies.append('The English passage mentions ' + ' and '.join(tags) + '.')
        self._record_session_exchange(json.dumps(texts), json.dumps(replies))
        return replies


def state(tmp_path, model=None, resume=False):
    source = tmp_path/'source.epub'
    if not source.exists():source.write_bytes(b'stable-source')
    model = model or Model()
    value = ProtectedNames(model, source, resume)
    model._protected_names_state = value
    return value


def test_tags_show_source_and_approved_identity_and_restore_from_our_mapping():
    a=NameAnnotations(['汉鲁凡戴遇到了汉鲁凡戴。'],Glossary.parse('汉鲁凡戴 → Haurvatat'))
    assert a.sent[0].count('汉鲁凡戴')==2
    assert 'approved="Haurvatat"' in a.sent[0]
    assert len(a.expected[0])==2
    reply=re.sub('汉鲁凡戴','Achilles',a.sent[0]).replace('approved="Haurvatat"','approved="Wrong"')
    restored,marks=a.restore([reply])
    assert restored==['Haurvatat遇到了Haurvatat。']
    assert len(marks[0])==2 and marks[0][0]['model_text']=='Achilles'


def test_framed_paragraphs_are_reordered_by_id_without_relying_on_separators():
    ids,payload=unit_request(['甲 & 乙','第二段'])
    etree.fromstring(payload.encode())
    raw=f'<bbm-response><bbm-unit id="{ids[1]}">Second.</bbm-unit><bbm-unit id="{ids[0]}">First &amp; another.</bbm-unit></bbm-response>'
    assert parse_unit_response(raw,ids)==['First & another.','Second.']


@pytest.mark.parametrize('kind',['missing','duplicate','unknown','merged','nested','plain','stray'])
def test_framed_paragraph_errors_fail_closed(kind):
    ids,_=unit_request(['第一段','第二段'])
    units=[f'<bbm-unit id="{identity}">English.</bbm-unit>' for identity in ids]
    if kind=='missing':units.pop()
    if kind=='duplicate':units.append(units[0])
    if kind=='unknown':units[0]=units[0].replace(ids[0],'unknown')
    if kind=='merged':units=[f'<bbm-unit id="{ids[0]}">First and second.</bbm-unit>']
    if kind=='nested':units=[f'<bbm-unit id="{ids[0]}">{units[1]}</bbm-unit>']
    raw='<bbm-response>'+''.join(units)+'</bbm-response>'
    if kind=='plain':raw='First paragraph. Second paragraph.'
    if kind=='stray':raw=raw.replace('</bbm-response>','stray prose</bbm-response>')
    with pytest.raises(NameMarkupError):parse_unit_response(raw,ids)


def test_unresolved_longer_identity_and_adjacent_fragments_are_not_split_into_people():
    glossary=Glossary.parse('御坂 → Misaka\n一色 → Isshiki')
    assert '<bbm-name' not in NameAnnotations(['御坂一色来了。'],glossary).sent[0]
    assert '<bbm-name' not in NameAnnotations(['御坂一色来了。'],Glossary.parse('御坂 → Misaka'),['御坂一色']).sent[0]


def test_prepared_window_context_is_reused_by_chunks_retries_and_resume(tmp_path):
    s=state(tmp_path)
    sources=['阿奇拉是马其顿的王。','汉鲁凡戴是完美天使。']
    jobs=[SimpleNamespace(source_text=text,context_group='chapter',global_index=i) for i,text in enumerate(sources)]
    s.configure_windows(jobs,len)
    s.prepare_jobs(jobs)
    requests=len(s.model.requests)
    for text in sources:
        s.translate([text],s.model.translate_list,lambda out:None)
        assert '阿奇拉是马其顿的王' in s.data['windows'][next(iter(s.data['windows']))]['summary']
    assert len(s.model.requests)==requests==4
    restored=state(tmp_path,resume=True)
    restored.configure_windows(jobs,len)
    restored.prepare_jobs(jobs)
    assert not restored.model.requests
    assert restored.context_for(sources[:1]) == ''
    context = restored.context_for(sources[1:])
    assert 'PRECEDING original' in context
    assert sources[0] in context and sources[1] not in context


def test_translation_context_excludes_future_summaries_and_handles_split_children(tmp_path):
    s = state(tmp_path)
    sources = ['earlier event', 'first requested unit', 'second requested unit', 'future scene']
    jobs = [SimpleNamespace(source_text=t, context_group='chapter', global_index=i)
            for i, t in enumerate(sources)]
    s.configure_windows(jobs, len)
    s.data['windows']['fake'] = {'sources': sources, 'summary': 'future summary poison', 'done': True}
    for texts in (sources[1:3], sources[1:2], sources[2:3]):
        context = s.context_for(texts)
        assert 'earlier event' in context
        assert 'future' not in context
        assert all(text not in context for text in texts)


def test_ambiguous_repeated_source_gets_no_guessed_context(tmp_path):
    s = state(tmp_path)
    s.windows = [['first', 'repeated', 'second', 'repeated']]
    assert s.context_for(['repeated']) == ''


def test_resume_drops_only_refused_handoff_seed_and_keeps_accepted_pairs(tmp_path):
    from book_maker.session_context import HandoffReport
    s = state(tmp_path)
    refusal = "I can help with all sorts of things, but that request may go against my [guidelines](https://gemini.google/policy-guidelines/)."
    seed = HandoffReport(window=2, summary=refusal).seed_text()
    s.data['session'] = {'messages': [{'role':'user', 'content':seed},
        {'role':'user', 'content':'Accepted original.'},
        {'role':'assistant', 'content':'Accepted translation.'}], 'windows':3}
    s.save()
    restored = state(tmp_path, resume=True)
    restored.restore_session()
    assert restored.model.session.messages() == s.data['session']['messages'][1:]
    assert restored.model.session.windows == 3


@pytest.mark.parametrize('mutation', ['missing','duplicate','unknown','broken','nested','moved','stray'])
def test_malformed_names_are_rejected_without_guessing(mutation):
    a=NameAnnotations(['阿奇拉走来了。','汉鲁凡戴走来了。'],Glossary.parse('阿奇拉 → Archelaus\n汉鲁凡戴 → Haurvatat'))
    replies=list(a.sent)
    tag=re.search(r'<bbm-name.*?</bbm-name>',replies[0]).group()
    if mutation=='missing':replies[0]=replies[0].replace(tag,'Archelaus')
    if mutation=='duplicate':replies[0]+=tag
    if mutation=='unknown':replies[0]=replies[0].replace(next(iter(a.expected[0])),'nUNKNOWN')
    if mutation=='broken':replies[0]=replies[0].replace('</bbm-name>','</bbm-name')
    if mutation=='nested':replies[0]=replies[0].replace('阿奇拉','<b>Archelaus</b>')
    if mutation=='moved':replies.reverse()
    if mutation=='stray':replies[0]+='</bbm-name>'
    with pytest.raises(NameMarkupError):a.restore(replies)


def test_longest_names_and_unicode_boundaries_and_xml_escaping():
    glossary=Glossary.parse('阿奇拉 → Archelaus\n阿奇拉一世 → Archelaus I\nAnn → A & B "C"')
    a=NameAnnotations(['阿奇拉一世和阿奇拉、Ann、Anna。'],glossary)
    assert len(a.expected[0])==3
    assert 'A &amp; B &quot;C&quot;' in a.sent[0]
    result,_=a.restore(a.sent)
    assert result==['Archelaus I和Archelaus、A & B "C"、Anna。']


def test_source_window_is_larger_than_requests_but_stays_bounded():
    jobs=[SimpleNamespace(source_text='a'*n,context_group=g) for n,g in [(3,'a'),(3,'a'),(3,'b'),(11,'b'),(2,'c')]]
    result=source_windows(jobs,10,len)
    assert [list(map(len,w)) for w in result]==[[3,3],[3],[11],[2]]
    assert sum(result,[])==[j.source_text for j in jobs]


def test_source_extraction_and_three_votes_happen_before_translation_and_are_deduplicated(tmp_path):
    s=state(tmp_path)
    s.prepare(['汉鲁凡戴，是完美天使。','阿奇拉是马其顿的王。'])
    assert len(s.model.requests)==4 and not s.model.translation_requests
    s.prepare(['阿奇拉是马其顿的王。'])
    assert len(s.model.requests)==5  # New window extraction, no repeated votes.
    assert s.trusted.lookup('阿奇拉').translation=='Archelaus'
    for prompt in s.model.requests:
        if prompt.startswith('Independent'):
            payload=json.loads(prompt.split('\n',1)[1])
            assert all(not e['votes'] and not e['rounds'] for e in payload['entries'])
            assert payload['original_text']


def test_split_vote_round_is_discarded_not_accumulated(tmp_path):
    m=Model()
    def response(target):
        return json.dumps({'votes':[{'term':'阿奇拉','translation':target,'reason':'source checked'}]})
    m.answers=iter([json.dumps({'entries':[{'term':'阿奇拉','translation':'A','kind':'name'}]})]+
                   [response(t) for t in ['A','B','C','B','A','A']])
    s=state(tmp_path,m)
    s.prepare(['阿奇拉是马其顿的王。'])
    assert s.trusted.lookup('阿奇拉').translation=='A'
    assert len(s.data['decisions']['阿奇拉']['rounds'])==1
    assert len(m.requests)==7


def test_interrupted_vote_resumes_only_missing_reviewers(tmp_path):
    m=Model()
    m.answers=iter([json.dumps({'entries':[{'term':'阿奇拉','translation':'Archelaus','kind':'name'}]}),
                    json.dumps({'votes':[{'term':'阿奇拉','translation':'Archelaus','reason':'source checked'}]}),
                    RuntimeError('offline interruption')])
    first=state(tmp_path,m)
    with pytest.raises(RuntimeError):first.prepare(['阿奇拉是马其顿的王。'])
    second=state(tmp_path,resume=True)
    second.prepare(['阿奇拉是马其顿的王。'])
    assert len(second.model.requests)==2
    assert second.trusted.lookup('阿奇拉').translation=='Archelaus'


def test_malformed_preparation_is_retried_and_never_approved(tmp_path):
    bad='{"entries":"wrong"}'
    m=Model();m.answers=iter([bad]*3)
    s=state(tmp_path,m)
    with pytest.raises(NameMarkupError):s.prepare(['阿奇拉是马其顿的王。'])
    assert len(m.requests)==3 and not s.trusted and not m.translation_requests


def test_native_compaction_filter_discards_ungrounded_terms_without_repeating_preparation(tmp_path):
    m=Model();m.answers=iter(['{"entries":[{"term":"absent","translation":"X","kind":"name"}]}'])
    s=state(tmp_path,m)
    s.prepare(['阿奇拉是马其顿的王。'])
    assert len(m.requests)==1 and not s.trusted and not m.translation_requests


def test_failed_markup_never_enters_history_or_accepted_records(tmp_path):
    s=state(tmp_path)
    sources=['阿奇拉是马其顿的王。']
    def bad(texts):
        s.model._record_session_exchange('bad request','bad response')
        return ['Archelaus is a king.']  # Lost tag, despite plausible English.
    with pytest.raises(NameMarkupError):s.translate(sources,bad,lambda out:None)
    assert not s.model.session.messages() and not s.data['records']
    assert not hasattr(s.model,'_protected_name_instruction')
    result=s.translate(sources,s.model.translate_list,lambda out:validate_translation_texts(sources,out,'en','zh'))
    assert result==['The English passage mentions Archelaus.']
    assert len(s.model.session.messages())==2
    assert 'Different Name' not in json.dumps(s.model.session.messages())
    assert 'bbm-name' not in result[0] and len(s.data['records'])==1


def test_scope_changes_are_refused(tmp_path):
    s=state(tmp_path);s.prepare(['阿奇拉是马其顿的王。'])
    model=Model();model.language='fr'
    with pytest.raises(ValueError,match='different source/settings'):state(tmp_path,model,resume=True)


def test_valid_uncompacted_history_restores_on_resume(tmp_path):
    s=state(tmp_path)
    sources=['阿奇拉是马其顿的王。']
    s.translate(sources,s.model.translate_list,lambda out:validate_translation_texts(sources,out,'en','zh'))
    expected=s.model.session.messages()
    resumed=state(tmp_path,resume=True)
    resumed.restore_session()
    assert resumed.model.session.messages()==expected
    assert resumed.model.session.estimated_tokens()>0


def test_lost_tags_retry_then_split_without_saving_failed_history(tmp_path,monkeypatch):
    monkeypatch.setattr('tenacity.nap.time.sleep',lambda _:None)
    s=state(tmp_path)
    model=s.model
    loader=EPUBBookLoader.__new__(EPUBBookLoader)
    loader.translate_model=model;loader.language='en';loader.source_lang='zh';loader.max_chunk_attempts=2
    loader.retry_one_by_one=False
    calls=[]
    def translate(texts, *args):
        calls.append(len(texts))
        if len(texts)>2:
            model._record_session_exchange('malformed request','malformed response')
            return ['The English passage.']*len(texts)
        return model.translate_list(texts)
    loader._translate_texts_attempt=translate
    sources=['阿奇拉是马其顿的王。']*4
    result=loader._translate_cleaned_texts_aligned(sources)
    assert calls==[4,4,2,2]
    assert result==['The English passage mentions Archelaus.']*4
    assert len(model.session.messages())==4
    assert 'malformed response' not in json.dumps(model.session.messages())


def test_annotation_handles_names_split_across_inline_formatting():
    block=etree.fromstring(b'<p>Hello <em>Ha</em>urvatat, welcome.</p>')
    before=''.join(block.itertext())
    marks=[dict(id='n1',source='汉鲁凡戴',approved='Haurvatat',model_text='Wrong',start=6,end=15)]
    _annotate_block(block,marks)
    assert ''.join(block.itertext())==before
    assert len(block.findall('em'))==1
    assert len(block.xpath('.//span'))==2
    assert all(el.get('data-bbm-source')=='汉鲁凡戴' for el in block.xpath('.//span'))


def test_both_epubs_from_full_loader_are_readable_and_annotated_trace_is_self_contained(tmp_path,monkeypatch):
    for cls,name in [(epub.EpubWriter,'_write_items'),(epub.EpubReader,'_check_deprecated'),(epub.EpubReader,'_load_spine')]:
        monkeypatch.setattr(cls,name,getattr(cls,name))
    book=epub.EpubBook();book.set_identifier('protected-test');book.set_title('阿奇拉');book.set_language('zh')
    chapter=epub.EpubHtml(title='阿奇拉',file_name='chapter.xhtml',lang='zh')
    chapter.content='<html xmlns="http://www.w3.org/1999/xhtml"><head><title>阿奇拉</title></head><body><h1>阿奇拉</h1><p>阿奇拉是马其顿的王。</p><p>汉鲁凡戴是完美天使。</p></body></html>'
    book.add_item(chapter);book.add_item(epub.EpubNcx());book.spine=[chapter]
    book.toc=(epub.Link('chapter.xhtml','阿奇拉','opening'),)
    source=tmp_path/'source.epub';epub.write_epub(str(source),book)
    model=Model()
    loader=EPUBBookLoader(str(source),lambda *args,**kw:model,'unused',False,'en',source_lang='zh',single_translate=True,disclose=False)
    loader.translate_tags='h1,p';loader.accumulated_num=4000;loader.accumulate_across_chapters=True
    loader.quiet=True;loader.output_dir=str(tmp_path/'output');(tmp_path/'output').mkdir()
    loader.make_bilingual_book()
    clean=tmp_path/'output/source_bilingual.epub';annotated=tmp_path/'output/source_bilingual_annotated.epub'
    assert clean.exists() and annotated.exists()
    with ZipFile(clean) as a,ZipFile(annotated) as b:
        assert a.testzip() is None and b.testzip() is None
        assert b.infolist()[0].filename=='mimetype' and b.infolist()[0].compress_type==ZIP_STORED
        cleanroot=etree.fromstring(a.read('EPUB/chapter.xhtml'));markedroot=etree.fromstring(b.read('EPUB/chapter.xhtml'))
        assert ''.join(cleanroot.itertext())==''.join(markedroot.itertext())
        assert b'bbm-name' not in a.read('EPUB/chapter.xhtml')
        assert 'Archelaus' in ''.join(cleanroot.itertext()) and 'Haurvatat' in ''.join(cleanroot.itertext())
        assert markedroot.xpath('//*[@data-bbm-source]')
        assert json.loads(b.read('META-INF/bbm-protected-names.json'))['records']
        assert 'Wrong Name' not in ''.join(cleanroot.itertext())
    extraction=[p for p in model.requests if p.startswith('Prepare the UPCOMING')]
    assert any('是马其顿的王' in p and '是完美天使' in p for p in extraction)


def test_cli_flags_are_available_and_positive():
    from book_maker.cli import parse_args
    args=parse_args(['--book_name','book.epub','--protected-names','--glossary-window-tokens','9000'])
    assert args.protected_names and args.glossary_window_tokens==9000
    with pytest.raises(SystemExit):parse_args(['--book_name','book.epub','--glossary-window-tokens','0'])
