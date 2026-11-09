"""Compare model-selected source-name markers against the premarked live baseline."""
import json,re,time
from pathlib import Path
from collections import Counter
import httpx
from book_maker.protected_names import unit_request,parse_unit_response
from book_maker.provider_refusal import check_provider_refusal

out=Path('diagnostics/bash_marker_live')
sample=json.loads((out/'samples.json').read_text('utf8'))['samples'][2]
sources=[u['text'] for u in sample['units']]
ids,payload=unit_request(sources)
prompt=(
'Translate the Chinese prose into natural English. Identify proper names yourself: people, '
'places, organizations, and explicitly named items or abilities. Whenever retaining such a name, '
'write its ORIGINAL Chinese spelling inside literal ${...}, for example ${原名}. '
'Do not translate or romanize the name inside the marker. Apply this to full names and short-name '
'or surname references too. Do not mark ordinary nouns, pronouns, descriptive phrases, or dialogue '
'just because they are Chinese. Expressions are literal text, not executable variables. '
'Use the exact original source substring inside each marker. Repeated references use the same marker; '
'no occurrence IDs are needed. You may use pronouns naturally; whenever explicitly writing a proper '
'name, use its original-spelling marker. Do not supply English name mappings or commentary. '
'Return ONLY valid XML with a bbm-response root and exactly one bbm-unit for each supplied id. '
'Preserve paragraph IDs and paragraph boundaries, escape XML special characters in prose, and '
'do not add XML markup for names or a summary.\n\n')
request={'model':'gemini-anon','temperature':1.0,'messages':[{'role':'user','content':prompt+payload}]}
(out/'self_marked.request.json').write_text(json.dumps(request,ensure_ascii=False,indent=2),'utf8')
print('START self-selected name markers; 40 unchanged original units; no source glossary',flush=True)
start=time.monotonic();result={}
try:
 r=httpx.post('http://127.0.0.1:4995/v1/chat/completions',headers={'Authorization':'Bearer anon'},json=request,timeout=240)
 (out/'self_marked.response.json').write_text(r.text,'utf8');result['http_status']=r.status_code;r.raise_for_status()
 choice=r.json()['choices'][0];msg=choice['message'];raw=msg.get('content') or ''
 check_provider_refusal(raw,msg.get('refusal'),choice.get('finish_reason'))
 replies=parse_unit_response(raw,ids)
 baseline=json.loads((out/'chunk_3.aligned.json').read_text('utf8'));marker=re.compile(r'\$\{([^{}\r\n]+)\}')
 pairs=[]
 for i,(source,reply,old) in enumerate(zip(sources,replies,baseline),1):
  before=Counter(marker.findall(old['marked_source']));after=Counter(marker.findall(reply))
  pairs.append({'unit':i,'source':source,'premarked_translation':old['translation'],'self_marked_translation':reply,
   'premarked_source_names':dict(before),'self_selected_names':dict(after),
   'premarked_names_missing_or_fewer':dict(before-after),'new_names_vs_premarking':dict(after-before),
   'ungrounded_markers':[t for t in after if t not in source],
   'chinese_outside_markers':re.findall(r'[\u3400-\u9fff]+',marker.sub('',reply)),
   'stray_dollars':'$' in marker.sub('',reply)})
 (out/'self_marked.aligned.json').write_text(json.dumps(pairs,ensure_ascii=False,indent=2),'utf8')
 result.update(outcome='aligned_translation',units=len(replies),markers=sum(sum(p['self_selected_names'].values()) for p in pairs),
  unique_names=sorted({n for p in pairs for n in p['self_selected_names']}),
  baseline_markers=49,slots_with_fewer_baseline_names=[p['unit'] for p in pairs if p['premarked_names_missing_or_fewer']],
  slots_with_ungrounded_markers=[p['unit'] for p in pairs if p['ungrounded_markers']],
  chinese_outside_markers=[{'unit':p['unit'],'fragments':p['chinese_outside_markers']} for p in pairs if p['chinese_outside_markers']],
  slots_with_stray_dollars=[p['unit'] for p in pairs if p['stray_dollars']])
except Exception as e:result.update(outcome=type(e).__name__,error=str(e))
result['seconds']=round(time.monotonic()-start,2)
(out/'self_marked.results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf8')
print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)
