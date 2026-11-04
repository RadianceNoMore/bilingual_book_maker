import json, hashlib, time, re
from pathlib import Path
from types import SimpleNamespace
import httpx
from book_maker.translator.chatgptapi_translator import ChatGPTAPI
from book_maker.protected_names import NameAnnotations, unit_request, parse_unit_response, INSTRUCTION
from book_maker.provider_refusal import check_provider_refusal
from book_maker.glossary import Glossary, GlossaryEntry
from book_maker.translation_quality import validate_translation_texts

root = Path(__file__).resolve().parents[1]
p = next((root/'input').glob('*protected_names.json'))
before = hashlib.sha256(p.read_bytes()).hexdigest()
d = json.loads(p.read_text('utf8'))
window = list(d['windows'].values())[1]
sources = window['sources'][40:42]
# These exact ordinary paragraphs and preceding excerpts were manually inspected.
context = 'Nearby PRECEDING original text, for reference only. Do not translate this context; translate only the requested units. Approved names take precedence:\n' + '\n'.join(window['sources'][32:40])
seed = d['session']['messages'][0]
# Only this inspected benign handoff seed is used, not the old full-window summary.
assert seed['role']=='user' and 'unverified handoff report' in seed['content']
glossary = Glossary(GlossaryEntry(t,v['target']) for t,v in d['decisions'].items() if v['status']=='approved')
a = NameAnnotations(sources, glossary)
ids, _ = unit_request(a.sent)
t = ChatGPTAPI(key='anon', language='English', api_base='http://127.0.0.1:4995/v1')
t.model = 'gemini-anon'
t._protected_request_glossary = Glossary(e for e in glossary.entries if any(e.term in s for s in sources))
captured = []
def capture(messages, stage):
    captured.append({'model':'gemini-anon','messages':messages,'temperature':1.0})
    raw='<bbm-response>'+''.join(f'<bbm-unit id="{i}">English.</bbm-unit>' for i in ids)+'</bbm-response>'
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=raw),finish_reason='stop')])
t._protected_completion=capture
requests=[]
for label, extra in [('xml_only',''),('preceding_context',context),('checked_handoff_seed',context)]:
    t._protected_name_instruction=INSTRUCTION+'\n\n'+extra
    t.translate_protected_units(a.sent)
    req=captured[-1]
    if label=='checked_handoff_seed':req['messages'].insert(-1,seed)
    requests.append((label,req))
out=Path(__file__).parent/'gemini_context_20261102'
out.mkdir(exist_ok=True)
results=[]
for round_no in range(1,4):
    for label,req in requests:
        name=f'{round_no}_{label}'
        (out/(name+'.request.json')).write_text(json.dumps(req,ensure_ascii=False,indent=2),'utf8')
        print('START',name,flush=True)
        start=time.monotonic()
        result={'case':label,'round':round_no}
        try:
            response=httpx.post('http://127.0.0.1:4995/v1/chat/completions',headers={'Authorization':'Bearer anon'},json=req,timeout=180)
            result['status']=response.status_code
            (out/(name+'.response.json')).write_text(response.text,'utf8')
            response.raise_for_status()
            choice=response.json()['choices'][0];msg=choice['message'];raw=msg.get('content') or ''
            check_provider_refusal(raw,msg.get('refusal'),choice.get('finish_reason'))
            parsed=parse_unit_response(raw,ids)
            restored,_=a.restore(parsed)
            validate_translation_texts(sources,restored,'en','zh')
            result.update(outcome='valid_translation',translations=restored)
        except Exception as e:
            result.update(outcome=type(e).__name__,error=str(e))
        result['seconds']=round(time.monotonic()-start,2)
        results.append(result)
        print(json.dumps(result,ensure_ascii=False),flush=True)
        (out/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),'utf8')
assert hashlib.sha256(p.read_bytes()).hexdigest()==before, 'Source ledger changed during diagnostics'
print('DONE. Source ledger unchanged.',flush=True)

