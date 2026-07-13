from __future__ import annotations
import os
from pathlib import Path
FORBIDDEN={'windows','system32','program files','programdata','appdata','.git','.venv','venv','node_modules','secrets','credentials'}
FORBIDDEN_FILES={'.env','.npmrc','.pypirc','id_rsa','id_ed25519','credentials','secrets'}
def forbidden(name):
    low=name.lower(); return low in FORBIDDEN or low.startswith('.') or low in FORBIDDEN_FILES or low.endswith(('.key','.pem','.p12'))
def validate_allowed_root(raw):
    root=Path(raw).expanduser().resolve()
    if not root.exists() or not root.is_dir(): raise ValueError('允许的根目录不存在')
    if root.parent==root or root.anchor==root.as_posix(): raise ValueError('拒绝盘符根目录')
    parts_lower={p.lower() for p in root.parts}
    is_temp='temp' in parts_lower and 'appdata' in parts_lower
    if (root==Path.home() or Path.home() in root.parents) and not is_temp: raise ValueError('拒绝整个用户主目录')
    if any(forbidden(p) for p in root.parts if p.lower()!='appdata') or ('appdata' in {p.lower() for p in root.parts} and 'temp' not in {p.lower() for p in root.parts}): raise ValueError('拒绝系统、依赖或敏感目录')
    return root
def walk_safe_files(candidate,root,supported,max_files=50,max_bytes=5_000_000,max_total=50_000_000,max_depth=5):
    candidate=Path(candidate); accepted=[]; rejected=[]; total=0
    def walk(folder,depth):
        nonlocal total
        if depth>max_depth:return
        try: entries=list(os.scandir(folder))
        except OSError as e: rejected.append({'path':str(folder),'reason':str(e)});return
        for entry in entries:
            p=Path(entry.path)
            is_reparse=entry.is_symlink()
            try: is_reparse=is_reparse or bool(getattr(entry.stat(follow_symlinks=False),'st_file_attributes',0) & 0x400)
            except OSError: pass
            if is_reparse:rejected.append({'path':str(p),'reason':'拒绝 symlink/junction'});continue
            if forbidden(entry.name):rejected.append({'path':str(p),'reason':'敏感或隐藏文件/目录'});continue
            try:
                if entry.is_dir(follow_symlinks=False):walk(p,depth+1);continue
                if not entry.is_file(follow_symlinks=False):continue
                if p.suffix.lower() not in supported:rejected.append({'path':str(p),'reason':'不支持的文件类型'});continue
                stat=entry.stat(follow_symlinks=False)
                if stat.st_size>max_bytes:rejected.append({'path':str(p),'reason':'超过单文件大小限制'});continue
                if len(accepted)>=max_files or total+stat.st_size>max_total:rejected.append({'path':str(p),'reason':'超过数量或总大小限制'});continue
                import hashlib
                digest=hashlib.sha256(p.read_bytes()).hexdigest()
                accepted.append({'id':'file_'+os.urandom(7).hex(),'path':str(p.resolve()),'relative_path':str(p.resolve().relative_to(root)),'size':stat.st_size,'mtime':datetime_iso(stat.st_mtime),'hash':digest});total+=stat.st_size
            except OSError as e:rejected.append({'path':str(p),'reason':str(e)})
    if candidate.is_file():walk(candidate.parent,max_depth);accepted=[x for x in accepted if x['path']==str(candidate.resolve())]
    else:walk(candidate,0)
    return accepted,rejected
def datetime_iso(ts):
    from datetime import datetime,timezone
    return datetime.fromtimestamp(ts,timezone.utc).isoformat()
