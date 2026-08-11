#!/usr/bin/env python3
"""Build and verify a private, portable AutoDL Qwen3-TTS training bundle.

The builder reads already-admitted local materials, copies only the cloud
training payload, rewrites every dataset path to POSIX-relative form, and
commits a new immutable generation.  It never copies credentials, trust
registries, failed-run evidence, or base-model weights.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import uuid
import wave
import gzip
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping


EXPECTED_DATASET_GENERATION = "tsubaki-local-private-neutral-20260808t021233z-v1"
EXPECTED_PREPARED_SHA256 = "e072c64809516a89328e60a693e8e8cd45f68eb8162e48644e9999f1abdc939f"
EXPECTED_PREPARED_ROWS = 324
EXPECTED_REFERENCE_SHA256 = "b140a46550f738434074e4df5e3e735b38ea386ebf11bee57a9e222d9514bd45"
UPSTREAM_COMMIT = "022e286b98fbec7e1e916cb940cdf532cd9f488e"
MODEL_REPOSITORY = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
MODEL_REVISION = "5d83992436eae1d760afd27aff78a71d676296fc"
TEXT_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


class BundleError(RuntimeError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pretty(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _plain_file(path: Path, label: str) -> Path:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise BundleError(f"{label} missing: {path}") from exc
    attrs = int(getattr(info, "st_file_attributes", 0))
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or attrs & 0x400:
        raise BundleError(f"{label} is not a plain file: {path}")
    if int(info.st_nlink) != 1:
        raise BundleError(f"{label} is hardlinked: {path}")
    return path


def _read_json(path: Path, label: str) -> Mapping[str, Any]:
    value = json.loads(_plain_file(path, label).read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise BundleError(f"{label} must be a JSON object")
    return value


def _safe_relative(value: str) -> PurePosixPath:
    if not value or "\\" in value or re.match(r"^[A-Za-z]:", value) or value.startswith("/"):
        raise BundleError(f"unsafe bundle path: {value!r}")
    path = PurePosixPath(value)
    if any(part in ("", ".", "..") for part in path.parts):
        raise BundleError(f"unsafe bundle path: {value!r}")
    return path


def _write_new(path: Path, data: bytes, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    if executable:
        path.chmod(0o755)


def _copy_new(source: Path, target: Path) -> None:
    _plain_file(source, "source payload")
    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as reader, target.open("xb") as writer:
        shutil.copyfileobj(reader, writer, 1024 * 1024)
        writer.flush()
        os.fsync(writer.fileno())


def _jsonl_rows(path: Path) -> Iterable[tuple[int, Mapping[str, Any]]]:
    with _plain_file(path, "prepared JSONL").open("rb") as handle:
        for line_number, raw in enumerate(handle, 1):
            if not raw.endswith(b"\n") or len(raw) > 2 * 1024 * 1024:
                raise BundleError(f"prepared line {line_number} has invalid framing/size")
            value = json.loads(raw.decode("utf-8"))
            if not isinstance(value, Mapping):
                raise BundleError(f"prepared line {line_number} is not an object")
            yield line_number, value


VERIFY_BUNDLE = r'''#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, os, re, stat, sys, wave
from pathlib import Path, PurePosixPath

class VerificationError(RuntimeError): pass
def sha(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda:f.read(1024*1024),b""): h.update(block)
    return h.hexdigest()
def safe_rel(value):
    if not value or "\\" in value or value.startswith("/") or re.match(r"^[A-Za-z]:",value): raise VerificationError("unsafe path: "+repr(value))
    p=PurePosixPath(value)
    if any(x in ("",".","..") for x in p.parts): raise VerificationError("unsafe path: "+repr(value))
    return p
def plain(path):
    s=path.lstat(); attrs=int(getattr(s,"st_file_attributes",0))
    if not stat.S_ISREG(s.st_mode) or stat.S_ISLNK(s.st_mode) or attrs & 0x400 or s.st_nlink != 1: raise VerificationError("not a plain single-link file: "+str(path))
def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--bundle-root",default=str(Path(__file__).resolve().parent)); args=ap.parse_args()
    root=Path(args.bundle_root).resolve(); mf=root/"metadata"/"BUNDLE_MANIFEST.json"; plain(mf)
    manifest=json.loads(mf.read_text("utf-8")); expected={}
    for item in manifest["files"]:
        rel=item["path"]; safe_rel(rel)
        if rel in expected: raise VerificationError("duplicate inventory path: "+rel)
        expected[rel]=item
    seen=set()
    for p in root.rglob("*"):
        rel=p.relative_to(root).as_posix()
        s=p.lstat(); attrs=int(getattr(s,"st_file_attributes",0))
        if stat.S_ISLNK(s.st_mode) or attrs & 0x400: raise VerificationError("link/reparse forbidden: "+rel)
        if p.is_file() and rel != "metadata/BUNDLE_MANIFEST.json": seen.add(rel)
    if seen != set(expected): raise VerificationError("inventory mismatch missing=%r extra=%r"%(sorted(set(expected)-seen),sorted(seen-set(expected))))
    for rel,item in expected.items():
        p=root.joinpath(*PurePosixPath(rel).parts); plain(p)
        if p.stat().st_size != item["bytes"] or sha(p) != item["sha256"]: raise VerificationError("hash/size mismatch: "+rel)
    source=json.loads((root/"pinned_upstream"/"SOURCE_RECEIPT.json").read_text("utf-8"))
    if source.get("commit")!="022e286b98fbec7e1e916cb940cdf532cd9f488e" or manifest["upstream"].get("model_revision")!="5d83992436eae1d760afd27aff78a71d676296fc": raise VerificationError("upstream commit/revision pin mismatch")
    source_files={x["path"]:x for x in source.get("files",[]) if isinstance(x,dict)}
    for bundled,official in (("pinned_upstream/LICENSE","LICENSE"),("pinned_upstream/dataset.py","finetuning/dataset.py"),("pinned_upstream/sft_12hz.py","finetuning/sft_12hz.py")):
        item=source_files.get(official)
        if not item or sha(root/bundled)!=item.get("sha256") or (root/bundled).stat().st_size!=item.get("bytes"): raise VerificationError("pinned source receipt mismatch: "+official)
    train=root/"data"/"train_with_codes.jsonl"; rows=0; relation=hashlib.sha256()
    with train.open("rb") as f:
        for rows,raw in enumerate(f,1):
            if not raw.endswith(b"\n") or len(raw)>2*1024*1024: raise VerificationError("invalid JSONL framing")
            row=json.loads(raw); apath=safe_rel(row["audio"]); rpath=safe_rel(row["ref_audio"])
            if not str(apath).startswith("data/audio/") or str(rpath)!="data/reference/tsubaki-reference.wav": raise VerificationError("dataset path contract failed")
            codes=row["audio_codes"]
            if not codes or any(not isinstance(frame,list) or len(frame)!=16 or any(type(x) is not int or x<0 or x>2047 for x in frame) for frame in codes): raise VerificationError("audio code contract failed")
            audio=root.joinpath(*apath.parts); ref=root.joinpath(*rpath.parts); plain(audio); plain(ref)
            aid=audio.stem
            if sha(audio)!=aid: raise VerificationError("content-addressed audio mismatch")
            text_sha=hashlib.sha256(row["text"].encode("utf-8")).hexdigest()
            txt=root/"data"/"text"/(aid+".txt"); plain(txt)
            if txt.read_text("utf-8")!=row["text"]+"\n": raise VerificationError("transcript mismatch")
            relation.update(json.dumps({"audio_sha256":aid,"audio_codes_sha256":hashlib.sha256(json.dumps(codes,separators=(",",":"),ensure_ascii=False).encode()).hexdigest(),"row":rows,"text_sha256":text_sha},sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()+b"\n")
            with wave.open(str(audio),"rb") as w:
                if w.getnchannels()!=1 or w.getsampwidth()!=2 or w.getframerate()!=24000: raise VerificationError("WAV contract failed")
    if rows != manifest["dataset"]["train_rows"] or relation.hexdigest()!=manifest["dataset"]["train_relation_sha256"]: raise VerificationError("train relation mismatch")
    forbidden=re.compile(rb"(?:[A-Za-z]:(?:\\|/(?!/))|BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY|(?:api[_-]?key|secret)\s*[:=]\s*[A-Za-z0-9_-]{16,})",re.I)
    for rel in expected:
        if rel.endswith(".wav"): continue
        p=root.joinpath(*PurePosixPath(rel).parts)
        if p.stat().st_size<=4*1024*1024 and forbidden.search(p.read_bytes()): raise VerificationError("credential or host-absolute path marker: "+rel)
    print(json.dumps({"status":"PASS","files":len(expected),"train_rows":rows,"bundle_manifest_sha256":sha(mf),"train_relation_sha256":relation.hexdigest()},sort_keys=True))
if __name__=="__main__":
    try: main()
    except Exception as e: print(json.dumps({"status":"BLOCK","error":type(e).__name__,"message":str(e)},sort_keys=True),file=sys.stderr); raise SystemExit(1)
'''


CLOUD_TRAIN = r'''#!/usr/bin/env python3
"""Bounded Linux derivative of official Qwen3-TTS sft_12hz.py at pinned commit."""
import argparse, json, math, os, random, shutil
from pathlib import Path
import numpy as np, torch
from accelerate import Accelerator
from safetensors.torch import save_file
from torch.optim import AdamW
from torch.utils.data import DataLoader
from transformers import AutoConfig
from qwen_tts.inference.qwen3_tts_model import Qwen3TTSModel
from pinned_upstream.dataset import TTSDataset

def main():
    p=argparse.ArgumentParser(); p.add_argument("--model",required=True); p.add_argument("--train-jsonl",required=True); p.add_argument("--output",required=True)
    p.add_argument("--max-optimizer-steps",type=int,default=20); p.add_argument("--epochs",type=int,default=10); p.add_argument("--save-checkpoint",action="store_true")
    p.add_argument("--speaker-name",default="tsubaki_private"); a=p.parse_args()
    if a.max_optimizer_steps<1 or a.epochs<1: raise SystemExit("invalid step/epoch count")
    random.seed(20260808); np.random.seed(20260808); torch.manual_seed(20260808); torch.cuda.manual_seed_all(20260808)
    torch.backends.cuda.matmul.allow_tf32=False; torch.backends.cudnn.allow_tf32=False
    accelerator=Accelerator(gradient_accumulation_steps=8,mixed_precision="bf16")
    q=Qwen3TTSModel.from_pretrained(a.model,dtype=torch.bfloat16,attn_implementation="sdpa",local_files_only=True)
    config=AutoConfig.from_pretrained(a.model,local_files_only=True)
    rows=[json.loads(x) for x in Path(a.train_jsonl).read_text("utf-8").splitlines() if x]
    ds=TTSDataset(rows,q.processor,config); dl=DataLoader(ds,batch_size=1,shuffle=True,num_workers=0,collate_fn=ds.collate_fn)
    q.model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant":False}); q.model.config.use_cache=False
    opt=AdamW(q.model.parameters(),lr=2e-6,weight_decay=.01,foreach=False)
    model,opt,dl=accelerator.prepare(q.model,opt,dl); model.train(); speaker=None; completed=0; micro=0; last_loss=None
    for epoch in range(a.epochs):
      for b in dl:
       with accelerator.accumulate(model):
        ids=b["input_ids"]; codec=b["codec_ids"]; cm=b["codec_mask"]
        spk=model.speaker_encoder(b["ref_mels"].to(model.device,dtype=model.dtype)).detach(); speaker=spk if speaker is None else speaker
        te=model.talker.text_projection(model.talker.get_text_embeddings()(ids[:,:,0]))*b["text_embedding_mask"]
        ce=model.talker.get_input_embeddings()(ids[:,:,1])*b["codec_embedding_mask"]; ce[:,6,:]=spk
        embeds=te+ce
        for i in range(1,16): embeds += model.talker.code_predictor.get_input_embeddings()[i-1](codec[:,:,i])*cm.unsqueeze(-1)
        out=model.talker(inputs_embeds=embeds[:,:-1,:],attention_mask=b["attention_mask"][:,:-1],labels=b["codec_0_labels"][:,1:],output_hidden_states=True)
        hidden=out.hidden_states[0][-1]; _,sub=model.talker.forward_sub_talker_finetune(codec[cm],hidden[cm[:,:-1]])
        loss=out.loss+0.3*sub
        if not torch.isfinite(loss): raise RuntimeError("non-finite loss")
        accelerator.backward(loss)
        if accelerator.sync_gradients: accelerator.clip_grad_norm_(model.parameters(),1.0)
        opt.step(); opt.zero_grad(); micro+=1; last_loss=float(loss.detach().cpu())
        if accelerator.sync_gradients: completed+=1; accelerator.print(json.dumps({"optimizer_step":completed,"loss":last_loss}))
       if completed>=a.max_optimizer_steps: break
      if completed>=a.max_optimizer_steps: break
    if completed!=a.max_optimizer_steps: raise RuntimeError("requested optimizer steps not completed")
    outdir=Path(a.output); outdir.mkdir(parents=True,exist_ok=False)
    receipt={"schema":"mentorflow.autodl-private-training-result.v1","optimizer_steps":completed,"micro_steps":micro,"last_loss":last_loss,"checkpoint_written":bool(a.save_checkpoint),"model_repository":"Qwen/Qwen3-TTS-12Hz-0.6B-Base"}
    if a.save_checkpoint and accelerator.is_main_process:
      ck=outdir/"checkpoint"; shutil.copytree(a.model,ck)
      state={k:v.detach().cpu() for k,v in accelerator.unwrap_model(model).state_dict().items() if not k.startswith("speaker_encoder")}
      weight=state["talker.model.codec_embedding.weight"]; state["talker.model.codec_embedding.weight"][3000]=speaker[0].detach().to(weight)
      cfg=json.loads((ck/"config.json").read_text("utf-8")); cfg["tts_model_type"]="custom_voice"; cfg["talker_config"]["spk_id"]={a.speaker_name:3000}; cfg["talker_config"]["spk_is_dialect"]={a.speaker_name:False}; (ck/"config.json").write_text(json.dumps(cfg,ensure_ascii=False,indent=2)+"\n","utf-8")
      save_file(state,str(ck/"model.safetensors"))
    if accelerator.is_main_process: (outdir/"TRAINING_RESULT.json").write_text(json.dumps(receipt,sort_keys=True,indent=2)+"\n","utf-8")
if __name__=="__main__": main()
'''


PREFLIGHT = r'''#!/usr/bin/env python3
import json, subprocess, sys
def main():
    ram_kib=int(next(x.split()[1] for x in open("/proc/meminfo") if x.startswith("MemTotal:")))
    avail_kib=int(next(x.split()[1] for x in open("/proc/meminfo") if x.startswith("MemAvailable:")))
    line=subprocess.check_output(["nvidia-smi","--query-gpu=memory.total,memory.free,temperature.gpu","--format=csv,noheader,nounits"],text=True).splitlines()
    if len(line)!=1: raise SystemExit("exactly one visible GPU is required")
    total,free,temp=map(int,[x.strip() for x in line[0].split(",")]); result={"gpu_total_mib":total,"gpu_free_mib":free,"gpu_temp_c":temp,"ram_total_mib":ram_kib//1024,"ram_available_mib":avail_kib//1024}
    ok=total>=24000 and free>=20480 and temp<=65 and ram_kib//1024>=32000 and avail_kib//1024>=16384
    result["status"]="PASS" if ok else "BLOCK"; print(json.dumps(result,sort_keys=True)); raise SystemExit(0 if ok else 1)
if __name__=="__main__": main()
'''


BOOTSTRAP = r'''#!/usr/bin/env bash
set -euo pipefail
BUNDLE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$BUNDLE_ROOT"
test "$(uname -s)" = Linux
python3 verify_bundle.py
python3 preflight.py
bundle_id="$(basename "$BUNDLE_ROOT")"
RUNTIME_ROOT="${RUNTIME_ROOT:-/root/autodl-tmp/tsubaki-runtime/$bundle_id}"
case "$RUNTIME_ROOT" in /*) ;; *) echo 'RUNTIME_ROOT must be absolute' >&2; exit 2;; esac
test "$RUNTIME_ROOT" != "$BUNDLE_ROOT"
mkdir -p "$RUNTIME_ROOT"
python3 -m venv "$RUNTIME_ROOT/venv"
. "$RUNTIME_ROOT/venv/bin/activate"
python -m pip install --upgrade 'pip==25.1.1' 'setuptools==80.9.0' 'wheel==0.45.1'
python -m pip install --index-url https://download.pytorch.org/whl/cu128 'torch==2.8.0' 'torchaudio==2.8.0'
python -m pip install -r requirements.lock
mkdir -p "$RUNTIME_ROOT/model" "$RUNTIME_ROOT/cache/huggingface"
export HF_HOME="$RUNTIME_ROOT/cache/huggingface"
export MODEL_ROOT="$RUNTIME_ROOT/model/base"
python -c 'import os; from huggingface_hub import snapshot_download; snapshot_download(repo_id="Qwen/Qwen3-TTS-12Hz-0.6B-Base",revision="5d83992436eae1d760afd27aff78a71d676296fc",local_dir=os.environ["MODEL_ROOT"])'
python - <<'PY'
import hashlib,json,os
from pathlib import Path
p=Path(os.environ['MODEL_ROOT'])/'config.json'; assert p.is_file()
cfg=json.loads(p.read_text('utf-8')); assert cfg['talker_config']['hidden_size']==1024 and cfg['talker_config']['text_hidden_size']==2048
print('BOOTSTRAP_PASS',hashlib.sha256(p.read_bytes()).hexdigest())
PY
printf '%s\n' "$RUNTIME_ROOT" > "$RUNTIME_ROOT/RUNTIME_ROOT.txt"
'''


RUN_ONE = r'''#!/usr/bin/env bash
set -euo pipefail
BUNDLE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"; cd "$BUNDLE_ROOT"
python3 verify_bundle.py
bundle_id="$(basename "$BUNDLE_ROOT")"; RUNTIME_ROOT="${RUNTIME_ROOT:-/root/autodl-tmp/tsubaki-runtime/$bundle_id}"
. "$RUNTIME_ROOT/venv/bin/activate"
python preflight.py
test -d "$RUNTIME_ROOT/model/base"
mkdir -p "$RUNTIME_ROOT/outputs"; test ! -e "$RUNTIME_ROOT/outputs/one-step"
python cloud_train.py --model "$RUNTIME_ROOT/model/base" --train-jsonl data/train_with_codes.jsonl --output "$RUNTIME_ROOT/outputs/one-step" --max-optimizer-steps 1 --epochs 1
'''


RUN_TRAIN = r'''#!/usr/bin/env bash
set -euo pipefail
BUNDLE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"; cd "$BUNDLE_ROOT"
python3 verify_bundle.py
bundle_id="$(basename "$BUNDLE_ROOT")"; RUNTIME_ROOT="${RUNTIME_ROOT:-/root/autodl-tmp/tsubaki-runtime/$bundle_id}"
. "$RUNTIME_ROOT/venv/bin/activate"
python preflight.py
test -f "$RUNTIME_ROOT/outputs/one-step/TRAINING_RESULT.json" || { echo 'Run ./run_one_step.sh first' >&2; exit 2; }
phase="${1:-20}"
case "$phase" in
  20) out="$RUNTIME_ROOT/outputs/twenty-step"; steps=20; epochs=2; save= ;;
  formal) test -f "$RUNTIME_ROOT/outputs/twenty-step/TRAINING_RESULT.json" || { echo 'Run ./run_train.sh 20 first' >&2; exit 2; }; out="$RUNTIME_ROOT/outputs/formal"; steps="${FORMAL_STEPS:-1000}"; epochs="${FORMAL_EPOCHS:-10}"; save=--save-checkpoint ;;
  *) echo 'usage: ./run_train.sh [20|formal]' >&2; exit 2 ;;
esac
test ! -e "$out"
python cloud_train.py --model "$RUNTIME_ROOT/model/base" --train-jsonl data/train_with_codes.jsonl --output "$out" --max-optimizer-steps "$steps" --epochs "$epochs" $save
'''


README = '''# 椿 private AutoDL training bundle

This immutable bundle is for a user-controlled **private cloud-processing job only**. It does not grant public release, commercial synthesis, voice impersonation publication, dataset redistribution, reference-audio redistribution, or trained-weight redistribution rights. AutoDL and storage/network providers are processors chosen by the user; keep the instance private, do not expose notebooks or ports publicly, and delete cloud copies after retrieving the private result.

The bundle contains 324 admitted training WAVs, matching UTF-8 transcripts, one reference WAV/transcript, prepared 16-codebook targets, and hash-only validation/heldout manifests. It contains no base weights, private signing key, QA trust registry, failed-run evidence, API key, or Windows path.

Hardware gates are enforced at every stage: one visible NVIDIA GPU reporting at least 24000 MiB total and 20480 MiB free, temperature at most 65 C, at least 32000 MiB RAM total and 16384 MiB available. These thresholds admit nominal 24 GB GPU / 32 GB RAM AutoDL instances without assuming that firmware reports the full binary capacity.

Shortest safe sequence after uploading and extracting into a private AutoDL data volume. Runtime dependencies, model cache, base model and outputs are written outside the immutable bundle under `/root/autodl-tmp/tsubaki-runtime/<bundle-id>` by default; set an absolute `RUNTIME_ROOT` to use another private data volume.

```bash
chmod +x bootstrap_autodl.sh run_one_step.sh run_train.sh
./bootstrap_autodl.sh
./run_one_step.sh
./run_train.sh 20
# Review both receipts and losses before formal training:
FORMAL_STEPS=1000 FORMAL_EPOCHS=10 ./run_train.sh formal
```

`verify_bundle.py` is stdlib-only and must pass before any download or training. Bootstrap downloads the public official base model once at the pinned revision; the base weights are deliberately not bundled.
'''


RIGHTS = '''# Rights and processor boundary

- Scope: private processing for the requesting user only; non-exportable.
- Identity: declared third-party character voice; biometric identity was not verified; original-voice ownership was not claimed.
- AutoDL/cloud host: a user-selected data processor, not a recipient of publication or redistribution rights.
- Allowed here: private training and private inference under the user's explicit instruction.
- Not granted: public/commercial release, public endpoints, dataset/reference redistribution, trained-weight distribution, or misleading attribution/impersonation.
- Operational duty: use a private instance, restrict account access, do not place secrets in the bundle, and remove cloud data when the private job is complete.
- Any broader use requires a new, separately verified rights/admission decision.
'''


def _inventory(root: Path) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda p: p.relative_to(root).as_posix()):
        if path.is_dir():
            continue
        rel = path.relative_to(root).as_posix()
        if rel == "metadata/BUNDLE_MANIFEST.json":
            continue
        _safe_relative(rel)
        _plain_file(path, "bundle payload")
        values.append({"path": rel, "bytes": path.stat().st_size, "sha256": _sha_file(path)})
    return values


def _create_and_verify_archive(bundle: Path, output_root: Path) -> Path:
    archive = Path(str(bundle) + ".tar.gz")
    if archive.exists():
        raise BundleError("refusing to overwrite an existing archive")
    with archive.open("xb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as tar:
                root_info = tarfile.TarInfo(bundle.name)
                root_info.type = tarfile.DIRTYPE
                root_info.mode = 0o755
                root_info.mtime = root_info.uid = root_info.gid = 0
                tar.addfile(root_info)
                for path in sorted(bundle.rglob("*"), key=lambda p: p.relative_to(bundle).as_posix()):
                    rel = path.relative_to(bundle).as_posix()
                    _safe_relative(rel)
                    info = path.lstat()
                    attrs = int(getattr(info, "st_file_attributes", 0))
                    if stat.S_ISLNK(info.st_mode) or attrs & 0x400:
                        raise BundleError(f"link/reparse forbidden in archive input: {rel}")
                    member = tarfile.TarInfo(f"{bundle.name}/{rel}")
                    member.mtime = member.uid = member.gid = 0
                    member.uname = member.gname = ""
                    if path.is_dir():
                        member.type = tarfile.DIRTYPE
                        member.mode = 0o755
                        tar.addfile(member)
                    elif path.is_file() and int(info.st_nlink) == 1:
                        member.type = tarfile.REGTYPE
                        member.size = info.st_size
                        member.mode = 0o755 if info.st_mode & stat.S_IXUSR else 0o644
                        with path.open("rb") as source:
                            tar.addfile(member, source)
                    else:
                        raise BundleError(f"non-plain archive input: {rel}")
    expected_files = {f"{bundle.name}/{path.relative_to(bundle).as_posix()}" for path in bundle.rglob("*") if path.is_file()}
    seen_files: set[str] = set()
    seen_folded: set[str] = set()
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            value = member.name
            _safe_relative(value)
            folded = value.casefold()
            if folded in seen_folded:
                raise BundleError(f"archive member collision: {value}")
            seen_folded.add(folded)
            if member.issym() or member.islnk() or member.isdev() or not (member.isdir() or member.isfile()):
                raise BundleError(f"unsafe archive member type: {value}")
            if member.isfile():
                seen_files.add(value)
        if seen_files != expected_files:
            raise BundleError("archive file member set mismatch")
    extraction_parent = output_root / f".archive-verify-{uuid.uuid4().hex}"
    extraction_parent.mkdir()
    try:
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(extraction_parent, filter="data")
        extracted = extraction_parent / bundle.name
        subprocess.run([sys.executable, str(extracted / "verify_bundle.py"), "--bundle-root", str(extracted)], check=True)
    finally:
        resolved = extraction_parent.resolve()
        if resolved.parent != output_root or not resolved.name.startswith(".archive-verify-"):
            raise BundleError("temporary extraction cleanup boundary failed")
        shutil.rmtree(resolved)
    return archive


def build(args: argparse.Namespace) -> tuple[Path, Path]:
    prepared_root = Path(args.prepared_root).resolve()
    dataset_root = Path(args.dataset_root).resolve()
    source_root = Path(args.source_root).resolve()
    output_root = Path(args.output_root).resolve()
    if output_root != Path(r"E:\Downloads\椿.training\cloud-bundles").resolve():
        raise BundleError("output root must be the fixed cloud-bundles directory")
    output_root.mkdir(parents=True, exist_ok=True)
    receipt = _read_json(prepared_root / "PREPARED_RECEIPT.json", "prepared receipt")
    prepared = prepared_root / "train_with_codes.jsonl"
    if receipt.get("status") != "completed" or receipt.get("dataset", {}).get("generation_id") != EXPECTED_DATASET_GENERATION:
        raise BundleError("prepared receipt admission/generation mismatch")
    if _sha_file(prepared) != EXPECTED_PREPARED_SHA256 or receipt.get("prepared", {}).get("rows") != EXPECTED_PREPARED_ROWS:
        raise BundleError("prepared v3 hash/count mismatch")
    admission = _read_json(dataset_root.parent / "LOCAL_PRIVATE_ADMISSION_RECEIPT.json", "admission receipt")
    manifest = _read_json(dataset_root / "TRAINING_MANIFEST.json", "training manifest")
    source_receipt = _read_json(source_root / "SOURCE_RECEIPT.json", "source receipt")
    if admission.get("scope") != "private_local_only" or source_receipt.get("commit") != UPSTREAM_COMMIT:
        raise BundleError("admission/source pin mismatch")
    train_items = manifest.get("train")
    if not isinstance(train_items, list) or len(train_items) != EXPECTED_PREPARED_ROWS:
        raise BundleError("training manifest count mismatch")
    indexed = {str(item["sha256"]): item for item in train_items}
    ref_source = dataset_root / "reference" / "tsubaki-reference.wav"
    if _sha_file(ref_source) != EXPECTED_REFERENCE_SHA256:
        raise BundleError("reference hash mismatch")
    generation = f"tsubaki-autodl-v3-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    final = output_root / generation
    partial = output_root / f".{generation}.partial-{uuid.uuid4().hex}"
    if final.exists() or partial.exists():
        raise BundleError("refusing to overwrite an existing generation")
    partial.mkdir()
    try:
        _copy_new(ref_source, partial / "data/reference/tsubaki-reference.wav")
        ref_text = str(manifest.get("reference", {}).get("text") or "")
        if not ref_text:
            ref_text_path = dataset_root / "reference" / "tsubaki-reference.txt"
            ref_text = _plain_file(ref_text_path, "reference transcript").read_text("utf-8").rstrip("\r\n")
        _write_new(partial / "data/reference/tsubaki-reference.txt", (ref_text + "\n").encode("utf-8"))
        relation = hashlib.sha256()
        output_lines: list[bytes] = []
        seen: set[str] = set()
        for line_number, row in _jsonl_rows(prepared):
            source_audio = Path(str(row.get("audio")))
            audio_sha = _sha_file(source_audio)
            item = indexed.get(audio_sha)
            if item is None or audio_sha in seen or str(row.get("text")) != str(item.get("text")):
                raise BundleError(f"prepared row {line_number} does not bind to admitted train item")
            seen.add(audio_sha)
            if source_audio.resolve() != (dataset_root / str(item["audio"])).resolve():
                raise BundleError(f"prepared row {line_number} audio path mismatch")
            codes = row.get("audio_codes")
            if not isinstance(codes, list) or not codes or any(not isinstance(frame, list) or len(frame) != 16 or any(type(value) is not int or not 0 <= value <= 2047 for value in frame) for frame in codes):
                raise BundleError(f"prepared row {line_number} code contract mismatch")
            _copy_new(source_audio, partial / f"data/audio/{audio_sha}.wav")
            text = str(row["text"])
            _write_new(partial / f"data/text/{audio_sha}.txt", (text + "\n").encode("utf-8"))
            portable = {"audio": f"data/audio/{audio_sha}.wav", "ref_audio": "data/reference/tsubaki-reference.wav", "text": text, "audio_codes": codes}
            output_lines.append(_canonical(portable) + b"\n")
            relation.update(_canonical({"audio_sha256": audio_sha, "audio_codes_sha256": _sha_bytes(_canonical(codes)), "row": line_number, "text_sha256": _sha_bytes(text.encode("utf-8"))}) + b"\n")
        if len(seen) != EXPECTED_PREPARED_ROWS:
            raise BundleError("prepared train set is incomplete")
        _write_new(partial / "data/train_with_codes.jsonl", b"".join(output_lines))
        for split in ("validation", "heldout"):
            lines = []
            for item in manifest.get(split, []):
                lines.append(_canonical({"audio_sha256": item["sha256"], "bytes": item["bytes"], "duration_ms": item["duration_ms"], "sample_id": item["sample_id"], "style": item["style"], "text": item["text"], "text_sha256": item["text_sha256"], "payload_in_bundle": False}) + b"\n")
            _write_new(partial / f"data/{split}_manifest.jsonl", b"".join(lines))
        for name in ("dataset.py", "sft_12hz.py"):
            _copy_new(source_root / "finetuning" / name, partial / "pinned_upstream" / name)
        _write_new(partial / "pinned_upstream/__init__.py", b"")
        _copy_new(source_root / "LICENSE", partial / "pinned_upstream/LICENSE")
        _write_new(partial / "pinned_upstream/SOURCE_RECEIPT.json", _pretty(source_receipt))
        requirements = "\n".join(["accelerate==1.12.0", "einops==0.8.1", "huggingface-hub==0.36.0", "librosa==0.11.0", "numpy==2.2.6", "onnxruntime==1.22.1", "qwen-tts==0.1.1", "safetensors==0.6.2", "soundfile==0.13.1", "sox==1.5.0", "tensorboard==2.20.0", "transformers==4.57.3"]) + "\n"
        _write_new(partial / "requirements.lock", requirements.encode())
        _write_new(partial / "verify_bundle.py", VERIFY_BUNDLE.encode(), True)
        _write_new(partial / "preflight.py", PREFLIGHT.encode(), True)
        _write_new(partial / "cloud_train.py", CLOUD_TRAIN.encode(), True)
        _write_new(partial / "bootstrap_autodl.sh", BOOTSTRAP.encode(), True)
        _write_new(partial / "run_one_step.sh", RUN_ONE.encode(), True)
        _write_new(partial / "run_train.sh", RUN_TRAIN.encode(), True)
        _write_new(partial / "README.md", README.encode("utf-8"))
        _write_new(partial / "RIGHTS_BOUNDARY.md", RIGHTS.encode("utf-8"))
        sanitized = {"schema": "mentorflow.autodl-private-admission-summary.v1", "dataset_generation_id": EXPECTED_DATASET_GENERATION, "scope": "private_cloud_processing_only", "original_admission_scope": admission["scope"], "dataset_tree_sha256": admission["dataset_tree_sha256"], "dataset_manifest_sha256": admission["dataset_manifest_sha256"], "prepared_sha256": EXPECTED_PREPARED_SHA256, "train_samples": 324, "validation_samples": 19, "heldout_samples": 19, "reference_sha256": EXPECTED_REFERENCE_SHA256, "public_or_commercial_release": False, "weight_distribution": False, "reference_audio_distribution": False, "registry_or_private_key_in_bundle": False}
        _write_new(partial / "metadata/ADMISSION_SUMMARY.json", _pretty(sanitized))
        bundle_manifest = {"schema": "mentorflow.qwen3-tts-autodl-private-bundle.v1", "generation_id": generation, "created_at_utc": datetime.now(timezone.utc).isoformat(), "immutable": True, "dataset": {"generation_id": EXPECTED_DATASET_GENERATION, "train_rows": 324, "validation_rows": 19, "heldout_rows": 19, "prepared_source_sha256": EXPECTED_PREPARED_SHA256, "train_relation_sha256": relation.hexdigest(), "reference_sha256": EXPECTED_REFERENCE_SHA256}, "upstream": {"repository": "https://github.com/QwenLM/Qwen3-TTS", "commit": UPSTREAM_COMMIT, "model_repository": MODEL_REPOSITORY, "model_revision": MODEL_REVISION, "base_weights_bundled": False}, "hardware_gate": {"gpu_total_mib_minimum": 24000, "gpu_free_mib_minimum": 20480, "gpu_temperature_c_maximum": 65, "ram_total_mib_minimum": 32000, "ram_available_mib_minimum": 16384}, "rights": {"scope": "private_cloud_processing_only", "non_exportable": True, "public_release": False, "commercial_synthesis": False, "weight_distribution": False}, "files": _inventory(partial)}
        _write_new(partial / "metadata/BUNDLE_MANIFEST.json", _pretty(bundle_manifest))
        subprocess.run([sys.executable, str(partial / "verify_bundle.py"), "--bundle-root", str(partial)], check=True)
        os.replace(partial, final)
        archive = _create_and_verify_archive(final, output_root)
        return final, archive
    except Exception:
        # Preserve partial evidence for inspection; never erase an ambiguous build.
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared-root", default=r"E:\Downloads\椿.training\prepared-generations\tsubaki-local-private-neutral-20260808t021233z-v1-cpu-codes-v3\final")
    parser.add_argument("--dataset-root", default=r"E:\Downloads\椿.training\generations\tsubaki-local-private-neutral-20260808t021233z-v1\dataset")
    parser.add_argument("--source-root", default=r"E:\Kristina_Runtime\sources\Qwen3-TTS")
    parser.add_argument("--output-root", default=r"E:\Downloads\椿.training\cloud-bundles")
    parser.add_argument("--verify-only")
    args = parser.parse_args()
    try:
        if args.verify_only:
            root = Path(args.verify_only).resolve()
            subprocess.run([sys.executable, str(root / "verify_bundle.py"), "--bundle-root", str(root)], check=True)
            return 0
        result, archive = build(args)
        print(json.dumps({"status": "PASS", "bundle": str(result), "archive": str(archive), "archive_sha256": _sha_file(archive)}, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "BLOCK", "error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
