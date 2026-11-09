"""One live request per sampled ordinary chunk; never writes translation state."""
import json,re,time,hashlib
from pathlib import Path
from collections import Counter
import httpx
from book_maker.protected_names import unit_request,parse_unit_response
from book_maker.provider_refusal import check_provider_refusal
from book_maker.session_context import estimate_tokens

out=Path(__file__).parent/'bash_marker_live'
d=json.loads((out/'samples.json').read_text('utf8'))
pattern=re.compile('|'.join(re.escape(t) for t in d['terms']))
marker=re.compile(r'\$\{([^{}\r\n]+)\}')
results=[]
for number,sample in enumerate(d['samples'],1):
 sources=[u['text'] for u in sample['units']]
 assert not any('${' in s for s in sources), 'Source has a reserved-marker collision'
 marked=[pattern.sub(lambda m:'${'+m.group()+'}',s) for s in sources]
 ids,payload=unit_request(marked)
 instruction=(
 'Translate the Chinese prose into natural English. Expressions of the form ${original Chinese name} '
 'are literal protected source names, not variables to execute. Keep the original Chinese spelling '
 'inside each such expression; do not romanize or translate it. Repeated references to the same '
 'name use the identical expression, without occurrence IDs. You may use pronouns naturally, '
 'but when explicitly writing a protected name, keep its ${original Chinese name} expression. '
 'Do not invent new protected expressions. Translate only the supplied text. '
 'Return ONLY valid XML with a bbm-response root and exactly one bbm-unit for each supplied id. '
 'Preserve paragraph IDs and paragraph boundaries, and escape XML special characters in prose. '
 'Do not add XML markup for names, commentary, or a summary.\n\n')
 req={'model':'gemini-anon','temperature':1.0,'messages':[{'role':'user','content':instruction+payload}]}
 (out/f'chunk_{number}.request.json').write_text(json.dumps(req,ensure_ascii=False,indent=2),'utf8')
 print('START',number,'SOURCE_CHUNK',sample['chunk_index'],'UNITS',len(sources),'MARKERS',sum(len(marker.findall(s)) for s in marked),flush=True)
 start=time.monotonic()
 result={'sample':number,'source_chunk_index':sample['chunk_index'],'source_documents':sorted(set(u['doc'] for u in sample['units'])),'units':len(sources),'estimated_source_tokens':sum(estimate_tokens(s) for s in sources)}
 try:
  response=httpx.post('http://127.0.0.1:4995/v1/chat/completions',headers={'Authorization':'Bearer anon'},json=req,timeout=240)
  result['http_status']=response.status_code
  (out/f'chunk_{number}.response.json').write_text(response.text,'utf8')
  response.raise_for_status();choice=response.json()['choices'][0];message=choice['message'];raw=message.get('content') or ''
  check_provider_refusal(raw,message.get('refusal'),choice.get('finish_reason'))
  translated=parse_unit_response(raw,ids)
  in_counts=Counter(x for s in marked for x in marker.findall(s));out_counts=Counter(x for s in translated for x in marker.findall(s))
  unknown={name:n for name,n in out_counts.items() if name not in in_counts}
  malformed=[{'slot':i+1,'text':s} for i,s in enumerate(translated) if '${' in marker.sub('',s) or re.search(r'\$\s+\{|\$｛',s)]
  lost={name:count for name,count in in_counts.items() if name not in out_counts}
  invented_markup=[i+1 for i,s in enumerate(translated) if '<bbm-name' in s]
  unprotected_source_names=[{'slot':i+1,'names':pattern.findall(marker.sub('',s))} for i,s in enumerate(translated) if pattern.search(marker.sub('',s))]
  result.update(outcome='aligned_translation',source_markers=sum(in_counts.values()),returned_markers=sum(out_counts.values()),source_unique_names=len(in_counts),returned_unique_names=len(out_counts),unknown_markers=unknown,malformed_markers=malformed,fully_missing_names=lost,invented_name_markup_slots=invented_markup,unprotected_source_names=unprotected_source_names,source_counts=dict(in_counts),returned_counts=dict(out_counts))
  pairs=[{'unit':i+1,'source':s,'marked_source':m,'translation':t} for i,(s,m,t) in enumerate(zip(sources,marked,translated))]
  (out/f'chunk_{number}.aligned.json').write_text(json.dumps(pairs,ensure_ascii=False,indent=2),'utf8')
 except Exception as e:result.update(outcome=type(e).__name__,error=str(e))
 result['seconds']=round(time.monotonic()-start,2);results.append(result)
 (out/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),'utf8')
 print(json.dumps(result,ensure_ascii=False),flush=True)
print('DONE',flush=True)
