#!/usr/bin/env python3
"""Offline CPU tokenizer preparation for a signed single-clip admission."""
from __future__ import annotations
import argparse,hashlib,json,os,subprocess
from datetime import datetime,timezone
from pathlib import Path
from typing import Any

def sha(p:Path)->str:
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
def pretty(v:Any)->bytes:return (json.dumps(v,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode()
def canonical(v:Any)->bytes:return (json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n').encode()
def write_new(p:Path,b:bytes)->None:
 p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('xb') as f:f.write(b);f.flush();os.fsync(f.fileno())

def run(a:argparse.Namespace)->None:
 for n in ('HF_HUB_OFFLINE','TRANSFORMERS_OFFLINE','HF_DATASETS_OFFLINE','PIP_NO_INDEX'):
  if os.environ.get(n)!='1':raise RuntimeError(f'offline env missing {n}')
 dataset=a.admission/'dataset'; receipt=json.loads((a.admission/'SIGNED_PRIVATE_ADMISSION_RECEIPT.json').read_text(encoding='utf-8'))
 if receipt.get('status') not in {'signed_private_experimental_admission','signed_private_fullstyle_v4_admission'} or not receipt.get('fresh_base_required'):raise RuntimeError('admission status invalid')
 payload=dataset/'ADMISSION_PAYLOAD.json';sig=Path(str(payload)+'.sig');allowed=a.trust/'allowed_signers'
 if sha(payload)!=receipt['payload_sha256'] or sha(sig)!=receipt['signature_sha256']:raise RuntimeError('signed admission drift')
 with payload.open('rb') as f:
  v=subprocess.run([str(a.ssh_keygen),'-Y','verify','-f',str(allowed),'-I','mentorflow','-n','mentorflow-private-voice','-s',str(sig)],stdin=f,capture_output=True)
 if v.returncode:raise RuntimeError('admission signature verify failed')
 final=a.output_root/a.prepare_id/'final';stage=a.output_root/a.prepare_id/'.partial'
 if final.exists() or stage.exists():raise RuntimeError('refusing overwrite')
 stage.mkdir(parents=True)
 manifest=json.loads((dataset/'TRAINING_MANIFEST.json').read_text(encoding='utf-8'));ref=dataset/manifest['reference']['audio']
 rows=[{'audio':str(dataset/x['audio']),'text':x['text'],'ref_audio':str(ref)} for x in manifest['train']]
 from qwen_tts import Qwen3TTSTokenizer
 tokenizer=Qwen3TTSTokenizer.from_pretrained(str(a.tokenizer),device_map='cpu',local_files_only=True)
 prepared=[];progress=stage/'PROGRESS.jsonl'
 for i,row in enumerate(rows,1):
  result=tokenizer.encode([row['audio']]);codes=result.audio_codes[0].cpu().tolist()
  if not codes or any(len(frame)!=16 or any(type(x)!=int or not 0<=x<=2047 for x in frame) for frame in codes):raise RuntimeError('invalid audio codes')
  prepared.append({**row,'audio_codes':codes});
  with progress.open('ab') as f:f.write(canonical({'index':i,'rows':len(rows),'audio_sha256':sha(Path(row['audio'])),'code_frames':len(codes)}));f.flush();os.fsync(f.fileno())
 raw=b''.join(canonical(x) for x in prepared);write_new(stage/'train_with_codes.jsonl',raw)
 code_frames=sum(len(x['audio_codes']) for x in prepared)
 out_receipt={'schema':'mentorflow.single-clip-experimental-cpu-prepare.v1','status':'success','created_at_utc':datetime.now(timezone.utc).isoformat(),'prepare_id':a.prepare_id,'admission_receipt_sha256':sha(a.admission/'SIGNED_PRIVATE_ADMISSION_RECEIPT.json'),'signed_payload_sha256':sha(payload),'rows':len(prepared),'code_frames':code_frames,'prepared_bytes':len(raw),'prepared_sha256':hashlib.sha256(raw).hexdigest(),'tokenizer_root':str(a.tokenizer),'tokenizer_config_sha256':sha(a.tokenizer/'config.json'),'device':'cpu','network_used':False,'old_dataset_read':False,'old_checkpoint_read':False,'fresh_base_training_required':True}
 write_new(stage/'PREPARED_RECEIPT.json',pretty(out_receipt));os.rename(stage,final);print(json.dumps({**out_receipt,'final':str(final)},ensure_ascii=False,indent=2))

def parser():
 p=argparse.ArgumentParser();p.add_argument('--admission',type=Path,required=True);p.add_argument('--trust',type=Path,required=True);p.add_argument('--tokenizer',type=Path,required=True);p.add_argument('--output-root',type=Path,required=True);p.add_argument('--prepare-id',required=True);p.add_argument('--ssh-keygen',type=Path,default=Path(r'C:\Windows\System32\OpenSSH\ssh-keygen.exe'));return p
if __name__=='__main__':run(parser().parse_args())
