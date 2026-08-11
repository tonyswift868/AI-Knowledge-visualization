#!/usr/bin/env python3
"""Create an OpenSSH-Ed25519 signed private experimental voice admission."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def sha(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda:f.read(1024*1024),b""): h.update(b)
    return h.hexdigest()


def pretty(v: Any) -> bytes: return (json.dumps(v,ensure_ascii=False,sort_keys=True,indent=2)+"\n").encode()
def canonical(v: Any) -> bytes: return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()


def write_new(p: Path, raw: bytes) -> None:
    p.parent.mkdir(parents=True,exist_ok=True)
    with p.open("xb") as f: f.write(raw); f.flush(); os.fsync(f.fileno())


def pin(root: Path, path: Path) -> dict[str, Any]:
    return {"path":path.relative_to(root).as_posix(),"bytes":path.stat().st_size,"sha256":sha(path)}


def run(a: argparse.Namespace) -> None:
    final=a.training_root/"generations"/a.generation_id; trust_final=a.training_root/"private-trust"/a.generation_id
    if final.exists() or trust_final.exists(): raise RuntimeError("refusing to overwrite admission")
    stage_parent=a.training_root/".admission-staging"; stage_parent.mkdir(parents=True,exist_ok=True)
    stage=stage_parent/a.generation_id; trust_stage=stage_parent/(a.generation_id+"-trust")
    if stage.exists() or trust_stage.exists(): raise RuntimeError("staging exists")
    dataset=stage/"dataset"; (dataset/"audio").mkdir(parents=True); (dataset/"reference").mkdir(); (dataset/"evidence").mkdir(); trust_stage.mkdir()
    curated_receipt=a.curated/("CURATION_RECEIPT.json" if a.fullstyle_v4 else "CONSENSUS_CURATED_RECEIPT.json"); audit_receipt=a.audit/"SPEAKER_AUDIT_RECEIPT.json"
    cr=json.loads(curated_receipt.read_text(encoding="utf-8")); ar=json.loads(audit_receipt.read_text(encoding="utf-8"))
    if a.fullstyle_v4:
        if cr.get("admitted_samples")!=65 or cr.get("excluded_samples")!=5 or cr.get("admitted_duration_seconds")!=229.77: raise RuntimeError("fullstyle v4 curated admission mismatch")
    elif cr.get("status")!="experimental_incremental_consensus_curated_pending_new_speaker_audit" or cr.get("admitted")!=24 or cr.get("admitted_duration_seconds")!=84.66: raise RuntimeError("strict curated admission mismatch")
    expected_admitted=65 if a.fullstyle_v4 else 24
    if ar.get("status")!="passed_experimental_incremental_private_local_training_gate" or ar.get("admitted")!=expected_admitted or ar.get("outliers")!=0 or ar.get("curation_receipt_sha256")!=sha(curated_receipt): raise RuntimeError("speaker audit mismatch")
    rows=[json.loads(x) for x in (a.curated/"dataset"/"trainer-neutral-manifest.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    audit_rows=[json.loads(x) for x in (a.audit/"SPEAKER_AUDIT_ROWS.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    score={x["sample_id"]:x["cosine_to_robust_centroid"] for x in audit_rows}
    candidates=[x for x in rows if 2500<=x["duration_ms"]<=8000]
    reference=max(candidates,key=lambda x:(score[x["sample_id"]],-abs(x["duration_ms"]-5000),x["sample_id"]))
    for row in rows:
        src=a.curated/"dataset"/row["audio"]; dst=dataset/"audio"/src.name; shutil.copyfile(src,dst)
        if sha(dst)!=row["audio_sha256"]: raise RuntimeError("audio copy mismatch")
    ref_src=dataset/str(reference["audio"])
    ref_dst=dataset/"reference"/"singleclip-reference.wav"; shutil.copyfile(ref_src,ref_dst)
    ordered=sorted((r for r in rows if r["sample_id"]!=reference["sample_id"]),key=lambda r:hashlib.sha256(("singleclip-v3b-split\0"+r["sample_id"]).encode()).hexdigest())
    heldout=ordered[:2]; validation=ordered[2:5] if a.fullstyle_v4 else ordered[2:4]; train=ordered[5:] if a.fullstyle_v4 else ordered[4:]
    def clean(row: dict[str,Any]) -> dict[str,Any]: return {"sample_id":row["sample_id"],"audio":row["audio"],"audio_sha256":row["audio_sha256"],"audio_bytes":row["audio_bytes"],"duration_ms":row["duration_ms"],"text":row["text"],"text_sha256":row["text_sha256"],"style":"expressive", "segment_index":row["segment_index"]}
    scope="private_local_fullstyle_v4" if a.fullstyle_v4 else "private_local_experimental_awaiting_future_packs"
    manifest={"schema":"mentorflow.single-clip-fullstyle-v4-training-manifest.v1" if a.fullstyle_v4 else "mentorflow.single-clip-experimental-training-manifest.v1","generation_id":a.generation_id,"candidate_id":"tsubaki_singleclip_fullstyle_v4" if a.fullstyle_v4 else "tsubaki_singleclip_incremental","display_name":"椿（全风格 v4）" if a.fullstyle_v4 else "椿（单段实验）","scope":scope,"label_policy":"whisper_small_raw_nfc_whitespace_only" if a.fullstyle_v4 else "strict_consensus_small_raw","style_retention_priority":bool(a.fullstyle_v4),"base_model":{"model_id":"Qwen/Qwen3-TTS-12Hz-0.6B-Base","fresh_start_required":True,"old_checkpoint_forbidden":True},"reference":{**clean(reference),"audio":"reference/singleclip-reference.wav","speaker_cosine_to_centroid":score[reference["sample_id"]]},"train":[clean(x) for x in train],"validation":[clean(x) for x in validation],"heldout":[clean(x) for x in heldout],"splits":{"train":len(train),"validation":len(validation),"heldout":len(heldout),"reference":1},"source_consensus_receipt_sha256":sha(curated_receipt),"speaker_audit_receipt_sha256":sha(audit_receipt)}
    write_new(dataset/"TRAINING_MANIFEST.json",pretty(manifest)); shutil.copyfile(curated_receipt,dataset/"evidence"/curated_receipt.name); shutil.copyfile(audit_receipt,dataset/"evidence"/audit_receipt.name)
    files=[pin(dataset,p) for p in sorted(dataset.rglob("*")) if p.is_file()]
    payload={"schema":"mentorflow.single-clip-fullstyle-v4-private-admission-payload.v1" if a.fullstyle_v4 else "mentorflow.single-clip-experimental-private-admission-payload.v1","generation_id":a.generation_id,"created_at_utc":datetime.now(timezone.utc).isoformat(),"scope":scope,"source_mp3_sha256":"9661a4600c7c1a110417408eb8ddc48beafdef9bb6e8a0aea6a6725bf2c58775","consensus_receipt_sha256":sha(curated_receipt),"speaker_audit_receipt_sha256":sha(audit_receipt),"dataset_files":files,"dataset_tree_sha256":hashlib.sha256(b"".join(f"{x['path']}\0{x['bytes']}\0{x['sha256']}\n".encode() for x in files)).hexdigest(),"splits":manifest["splits"],"fresh_base_required":True,"old_dataset_reused":False,"old_checkpoint_reused":False,"network_used":False,"public_release_eligible":False}
    payload_path=dataset/"ADMISSION_PAYLOAD.json"; write_new(payload_path,pretty(payload))
    key=trust_stage/"admission_ed25519"; subprocess.run([str(a.ssh_keygen),"-q","-t","ed25519","-N","","-C",a.generation_id,"-f",str(key)],check=True)
    subprocess.run([str(a.ssh_keygen),"-Y","sign","-f",str(key),"-n","mentorflow-private-voice",str(payload_path)],check=True,capture_output=True)
    sig=Path(str(payload_path)+".sig"); public=key.with_suffix(".pub"); allowed=trust_stage/"allowed_signers"
    pub_text=public.read_text(encoding="utf-8").strip(); write_new(allowed,("mentorflow "+" ".join(pub_text.split()[:2])+"\n").encode())
    with payload_path.open("rb") as inp:
        verify=subprocess.run([str(a.ssh_keygen),"-Y","verify","-f",str(allowed),"-I","mentorflow","-n","mentorflow-private-voice","-s",str(sig)],stdin=inp,capture_output=True,text=False)
    if verify.returncode!=0: raise RuntimeError("Ed25519 admission signature verification failed")
    receipt={"schema":"mentorflow.single-clip-fullstyle-v4-private-admission.v1" if a.fullstyle_v4 else "mentorflow.single-clip-experimental-private-admission.v1","status":"signed_private_fullstyle_v4_admission" if a.fullstyle_v4 else "signed_private_experimental_admission","generation_id":a.generation_id,"payload_sha256":sha(payload_path),"signature_sha256":sha(sig),"public_key_sha256":sha(public),"signature":"OpenSSH Ed25519 namespace mentorflow-private-voice","signature_verified":True,"dataset_tree_sha256":payload["dataset_tree_sha256"],"splits":manifest["splits"],"reference":manifest["reference"],"fresh_base_required":True,"awaiting_future_user_packs":False if a.fullstyle_v4 else True,"network_used":False}
    write_new(stage/"SIGNED_PRIVATE_ADMISSION_RECEIPT.json",pretty(receipt)); (a.training_root/"generations").mkdir(parents=True,exist_ok=True); (a.training_root/"private-trust").mkdir(parents=True,exist_ok=True)
    os.rename(stage,final); os.rename(trust_stage,trust_final); print(json.dumps({**receipt,"dataset":str(final),"trust":str(trust_final)},ensure_ascii=False,indent=2))


def parser() -> argparse.ArgumentParser:
    p=argparse.ArgumentParser(); p.add_argument("--curated",type=Path,required=True); p.add_argument("--audit",type=Path,required=True); p.add_argument("--training-root",type=Path,required=True); p.add_argument("--generation-id",required=True); p.add_argument("--fullstyle-v4",action="store_true"); p.add_argument("--ssh-keygen",type=Path,default=Path(r"C:\Windows\System32\OpenSSH\ssh-keygen.exe")); return p
if __name__=="__main__": run(parser().parse_args())
