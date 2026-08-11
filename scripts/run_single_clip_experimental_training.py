#!/usr/bin/env python3
"""Build and execute one owned, offline, fresh-Base 20-step experiment."""
from __future__ import annotations
import argparse,hashlib,json,os,shutil,subprocess,sys,time
from datetime import datetime,timezone
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
import orchestrate_qwen3_tts_bounded_probe as PROBE
import qwen3_tts_sft_safe as SAFE

def sha(p:Path)->str:
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
def canonical(v:Any)->bytes:return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
def pretty(v:Any)->bytes:return (json.dumps(v,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode()
def write_new(p:Path,b:bytes)->None:
 with p.open('xb') as f:f.write(b);f.flush();os.fsync(f.fileno())

def inventory(root:Path)->list[dict[str,Any]]:
 return [{'path':p.relative_to(root).as_posix(),'bytes':p.stat().st_size,'sha256':sha(p)} for p in sorted(root.rglob('*')) if p.is_file()]

def build_plan(a:argparse.Namespace,run:Path,policy:dict[str,Any])->dict[str,Any]:
 template=json.loads(a.template_plan.read_text(encoding='utf-8'))
 plan={k:v for k,v in template.items() if k not in {'plan_sha256','warm_start','formal_probe_receipt'}}
 dataset=a.admission/'dataset'; manifest=json.loads((dataset/'TRAINING_MANIFEST.json').read_text(encoding='utf-8'))
 prepared=a.prepared/'train_with_codes.jsonl'; rows=[json.loads(x) for x in prepared.read_text(encoding='utf-8').splitlines() if x]
 pins={x['audio_sha256']:x for x in manifest['train']}; shortest=min(rows,key=lambda x:(len(x['audio_codes']),x['audio']))
 short_pin=pins[sha(Path(shortest['audio']))]; longest=max(manifest['train'],key=lambda x:(x['duration_ms'],x['sample_id']))
 shutil.copyfile(prepared,run/'prepared'/'train_with_codes.jsonl')
 raw=b''.join((json.dumps({k:r[k] for k in ('audio','text','ref_audio')},ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n').encode() for r in rows)
 write_new(run/'inputs'/'train_raw.jsonl',raw)
 env={**SAFE.OFFLINE_VALUES,'CUDA_VISIBLE_DEVICES':'0','CUBLAS_WORKSPACE_CONFIG':':4096:8','TOKENIZERS_PARALLELISM':'false','WANDB_DISABLED':'true','PYTHONHASHSEED':'20260808','HF_HOME':str(a.training_root/'cache'/'huggingface'),'HF_DATASETS_CACHE':str(a.training_root/'cache'/'datasets'),'TRANSFORMERS_CACHE':str(a.training_root/'cache'/'transformers'),'TORCH_HOME':str(a.training_root/'cache'/'torch'),'PIP_CACHE_DIR':str(a.training_root/'cache'/'pip'),'TMP':str(run/'tmp'),'TEMP':str(run/'tmp'),'TMPDIR':str(run/'tmp')}
 plan['run_id']=a.run_id;plan['created_at_utc']=datetime.now(timezone.utc).isoformat();plan['mode']='formal' if a.fullstyle_v4 else 'bounded-probe';plan['offline_environment']=env
 plan['paths'].update({'training_root':str(a.training_root),'run_root':str(run),'dataset_root':str(dataset),'qa_trust_registry':str(a.trust/'allowed_signers'),'python_executable':str(a.python),'trainer':str(ROOT/'qwen3_tts_sft_safe.py'),'optimizer_implementation':str(ROOT/'chunked_adamw.py')})
 plan['dataset']={'generation_id':a.admission.name,'tree_sha256':json.loads((a.admission/'SIGNED_PRIVATE_ADMISSION_RECEIPT.json').read_text(encoding='utf-8'))['dataset_tree_sha256'],'manifest_sha256':sha(dataset/'TRAINING_MANIFEST.json'),'qa_decision':'fullstyle_v4_private_signed' if a.fullstyle_v4 else 'experimental_incremental_private_signed','train_samples':len(rows),'validation_samples':len(manifest['validation']),'heldout_samples':len(manifest['heldout']),'longest_train_sample':{'audio':longest['audio'],'bytes':longest['audio_bytes'],'sha256':longest['audio_sha256'],'duration_ms':longest['duration_ms']}}
 sample={'absolute_path':shortest['audio'],'audio_sha256':short_pin['audio_sha256'],'audio_bytes':short_pin['audio_bytes'],'audio_duration_ms':short_pin['duration_ms'],'duration_ms':short_pin['duration_ms'],'audio_frames':round(short_pin['duration_ms']*24),'code_frames':len(shortest['audio_codes']),'text_sha256':short_pin['text_sha256'],'train_index':rows.index(shortest)+1,'prepared_index':rows.index(shortest)+1,'prepared_row_sha256':hashlib.sha256((json.dumps(shortest,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n').encode()).hexdigest(),'curriculum_rank':1,'curriculum_row_sha256':hashlib.sha256(canonical({'audio':shortest['audio'],'text':shortest['text']})).hexdigest()}
 steps=100 if a.fullstyle_v4 else 20
 plan['bounded_probe']={'schema':'mentorflow.single-clip-fullstyle-v4-continuous.v1' if a.fullstyle_v4 else 'mentorflow.single-clip-experimental-bounded-probe.v1','curriculum_probe_shortest_non_release':not a.fullstyle_v4,'curriculum_sample':sample,'curriculum_group':{'group_id':'singleclip-fullstyle-v4-all59' if a.fullstyle_v4 else 'singleclip-exp-all19','rows':len(rows),'checkpoint_required':True,'experimental_short_data':not a.fullstyle_v4},'optimizer_steps':steps,'probe_checkpoint_required':not a.fullstyle_v4,'formal_checkpoint_written':bool(a.fullstyle_v4),'formal_release_receipt_eligible':False,'exact_training_contract_sha256':hashlib.sha256((b'singleclip-fullstyle-v4-fresh-base-100step' if a.fullstyle_v4 else b'singleclip-exp-v3b-fresh-base-20step')).hexdigest()}
 plan['training'].update({'epochs':20 if a.fullstyle_v4 else 40,'max_optimizer_steps':steps,'learning_rate':2e-6,'seed':20260808,'speaker_name':'tsubaki_singleclip_fullstyle_v4' if a.fullstyle_v4 else 'tsubaki_singleclip_exp','safe_trainer_sha256':sha(ROOT/'qwen3_tts_sft_safe.py')});plan['training']['optimizer']['implementation_sha256']=sha(ROOT/'chunked_adamw.py')
 plan.pop('warm_start',None);plan['resource_admission']=policy;plan['bounded_probe_policy']=policy;plan['bounded_probe_policy_sha256']=policy['policy_sha256']
 plan['prepared_source']={'prepared_path':str(run/'prepared'/'train_with_codes.jsonl'),'sha256':sha(prepared),'bytes':prepared.stat().st_size,'rows':len(rows),'source_receipt_sha256':sha(a.prepared/'PREPARED_RECEIPT.json'),'fresh_base_required':True}
 plan['raw_train_jsonl']={'path':str(run/'inputs'/'train_raw.jsonl'),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
 plan['probe_contract']={'schema':'mentorflow.single-clip-fullstyle-v4-training-contract.v1' if a.fullstyle_v4 else 'mentorflow.single-clip-experimental-probe-contract.v1','fresh_base_required':True,'old_checkpoint_forbidden':True,'optimizer_steps':steps,'single_continuous_run':bool(a.fullstyle_v4),'dataset_tree_sha256':plan['dataset']['tree_sha256'],'model_tree_sha256':plan['base_model']['tree_sha256']};plan['probe_contract_sha256']=hashlib.sha256(canonical(plan['probe_contract'])).hexdigest()
 plan['checkpoint_writer'].update({'implementation':str(ROOT/'streaming_safetensors.py'),'implementation_sha256':sha(ROOT/'streaming_safetensors.py'),'chunk_bytes':32*1024*1024,'format':'safetensors'})
 plan['artifact_policy']={'training_scope':'private_local_only','non_exportable':True,'public_release_eligible':False,'commercial_use_eligible':False,'release_upgrade_requires_new_signed_release_ready_manifest':True}
 plan['experimental_scope']={'short_data':not a.fullstyle_v4,'source_seconds':229.77 if a.fullstyle_v4 else 84.66,'awaiting_future_user_packs':False if a.fullstyle_v4 else True,'fresh_base':True,'old_checkpoint_read':False,'max_optimizer_steps_this_run':steps}
 plan['fullstyle_v4']=bool(a.fullstyle_v4)
 plan['plan_sha256']=hashlib.sha256(canonical(plan)).hexdigest();return plan

def run(a:argparse.Namespace)->None:
 run=a.training_root/'runs'/a.run_id
 if run.exists():raise RuntimeError('refusing overwrite run')
 for p in (run/'prepared',run/'inputs',run/'attempts'/'attempt-0001',run/'checkpoints',run/'tmp'):p.mkdir(parents=True,exist_ok=False)
 policy=dict(PROBE.bounded_policy(PROBE._sample_resources()))
 policy['policy_sha256']=hashlib.sha256(canonical({k:v for k,v in policy.items() if k!='policy_sha256'})).hexdigest()
 if policy['decision']!='GO':write_new(run/'RESOURCE_NO_GO.json',pretty(policy));raise RuntimeError('resource gate NO_GO: '+'; '.join(policy['reasons']))
 plan=build_plan(a,run,policy);write_new(run/'RUN_PLAN.json',pretty(plan));write_new(run/'RUN_PLAN.sha256',(plan['plan_sha256']+'\n').encode())
 env=os.environ.copy();env.update({k:str(v) for k,v in plan['offline_environment'].items()});env['MENTORFLOW_BOUNDED_ALLOCATOR_CAP_MIB']=str(policy['allocator_cap_mib']);env['MENTORFLOW_BOUNDED_START_TOTAL_VRAM_MIB']=str(policy['start_total_vram_mib']);env['MENTORFLOW_BOUNDED_START_FREE_VRAM_MIB']=str(policy['start_free_vram_mib'])
 command=[str(a.python),'-I',str(ROOT/'qwen3_tts_sft_safe.py'),'--plan',str(run/'RUN_PLAN.json'),'--prepared-jsonl',str(run/'prepared'/'train_with_codes.jsonl'),'--checkpoint-root',str(run/'checkpoints'),'--attempt-evidence-root',str(run/'attempts'/'attempt-0001')]
 gate=PROBE._WindowsGate();job=PROBE._WindowsGpuJob();proc=None;samples=[];violation=None;low=0;start=time.monotonic()
 try:
  with (run/'worker.stdout.log').open('xb') as out,(run/'worker.stderr.log').open('xb') as err:
   gated=[command[0],'-I','-c',PROBE._GATED_BOOTSTRAP,gate.name,*command];proc=subprocess.Popen(gated,cwd=ROOT.parent,env=env,stdout=out,stderr=err,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0x08000000));job.assign(proc);job_start=job.query();gate.release()
   while proc.poll() is None:
    sample=PROBE._sample_resources();reason=PROBE.watcher_violation(policy,sample);low,mreason=PROBE.memory_watcher_violation(sample,low);reason=reason or mreason;samples.append(sample)
    if reason or time.monotonic()-start>PROBE.MAXIMUM_RUN_SECONDS:violation=reason or 'timeout';job.terminate();break
    time.sleep(PROBE.WATCHER_INTERVAL_SECONDS)
   code=proc.wait();job_end=job.query()
 finally:gate.close();job.close()
 post=[]
 for _ in range(20):
  s=PROBE._sample_resources();post.append(s)
  if s['start_used_vram_mib']<=policy['start_used_vram_mib']+policy['thresholds']['maximum_exit_gpu_baseline_delta_mib']:break
  time.sleep(1)
 receipt={'schema':'mentorflow.single-clip-fullstyle-v4-training-owner.v1' if a.fullstyle_v4 else 'mentorflow.single-clip-experimental-training-owner.v1','status':'success' if code==0 and not violation else 'failed','run_id':a.run_id,'plan_sha256':plan['plan_sha256'],'exit_code':code,'violation':violation,'fresh_base':True,'warm_start':False,'old_checkpoint_read':False,'single_continuous_optimizer_run':bool(a.fullstyle_v4),'samples':samples,'post_exit_samples':post,'job_start':job_start,'job_end':job_end,'network_used':False}
 write_new(run/'OWNER_RECEIPT.json',pretty(receipt));print(json.dumps(receipt,ensure_ascii=False,indent=2))
 if receipt['status']!='success':raise RuntimeError('training worker failed')

def parser():
 p=argparse.ArgumentParser();p.add_argument('--admission',type=Path,required=True);p.add_argument('--trust',type=Path,required=True);p.add_argument('--prepared',type=Path,required=True);p.add_argument('--training-root',type=Path,required=True);p.add_argument('--run-id',required=True);p.add_argument('--template-plan',type=Path,required=True);p.add_argument('--python',type=Path,required=True);p.add_argument('--fullstyle-v4',action='store_true');return p
if __name__=='__main__':run(parser().parse_args())
