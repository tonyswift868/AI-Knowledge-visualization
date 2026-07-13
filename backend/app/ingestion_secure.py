from __future__ import annotations
import hashlib, json, os, re, uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from . import db
from .core_security import validate_allowed_root, walk_safe_files

SUPPORTED={'.pdf','.docx','.md','.markdown','.txt','.html','.htm','.py','.java','.js','.ts','.tsx','.jsx','.go','.rs','.c','.h','.cpp','.hpp','.cs','.kt','.kts','.swift','.php','.rb','.sh','.ps1','.sql','.json','.yaml','.yml','.toml','.xml','.csv','.ipynb'}
def new_id(prefix): return prefix+'_'+uuid.uuid4().hex[:14]
def sha_bytes(data): return hashlib.sha256(data).hexdigest()
def norm(text): return re.sub(r'\s+',' ',text).strip().lower()
def parse_file(path:Path):
    suffix=path.suffix.lower()
    if suffix=='.pdf':
        import fitz
        doc=fitz.open(path); pages=[p.get_text('text') for p in doc]
        if sum(len(x.strip()) for x in pages)<max(40,len(pages)*15): return [],'needs_ocr',len(pages)
        return [({'text':x,'page_start':i+1,'page_end':i+1,'heading':''}) for i,x in enumerate(pages) if x.strip()],'ready',len(pages)
    if suffix=='.docx':
        from docx import Document
        text='\n'.join(p.text for p in Document(path).paragraphs if p.text.strip()); return [{'text':text,'page_start':None,'page_end':None,'heading':''}],'ready',0
    if suffix == '.ipynb':
        notebook=json.loads(path.read_text(encoding='utf-8'))
        parts=[]
        for index, cell in enumerate(notebook.get('cells', []), start=1):
            cell_type=cell.get('cell_type','code')
            source=cell.get('source','')
            raw_cell=''.join(source) if isinstance(source,list) else str(source)
            if raw_cell.strip(): parts.append({'text':raw_cell,'page_start':None,'page_end':None,'heading':f'{cell_type} cell {index}'})
        return parts,'ready',0
    raw=_decode_text(path.read_bytes())
    if suffix in {'.html','.htm'}:
        from bs4 import BeautifulSoup
        raw=BeautifulSoup(raw,'html.parser').get_text('\n')
    heading=next((x.lstrip('# ').strip() for x in raw.splitlines() if x.startswith('#')),'')
    return [{'text':raw,'page_start':None,'page_end':None,'heading':heading}],'ready',0

def _decode_text(data:bytes):
    for encoding in ('utf-8-sig','utf-8','utf-16','gb18030'):
        try:return data.decode(encoding)
        except UnicodeDecodeError:continue
    return data.decode('utf-8',errors='replace')
def chunks(parts,source_id,size=900):
    output=[]; ordinal=1
    for part in parts:
        text=part['text'].strip()
        for block in [x.strip() for x in re.split(r'\n{2,}',text) if x.strip()]:
            output.append({'id':new_id('chunk'),'source_id':source_id,'ordinal':ordinal,'page_start':part.get('page_start'),'page_end':part.get('page_end'),'heading':part.get('heading',''),'text':block,'normalized_text':norm(block),'content_hash':sha_bytes(block.encode())});ordinal+=1
    return output
def create_preview(raw_path,allowed_root):
    root=validate_allowed_root(allowed_root); candidate=Path(raw_path).expanduser().resolve()
    if candidate!=root and not _inside(candidate,root): raise ValueError('路径必须位于允许根目录内')
    files,rejected=walk_safe_files(candidate,root,SUPPORTED)
    if not files: raise ValueError('没有可导入的支持格式文件')
    preview_id=new_id('preview'); expires=datetime.now(timezone.utc)+timedelta(minutes=10)
    with db.connection() as conn: conn.execute('INSERT INTO local_previews VALUES (?,?,?,?,?)',(preview_id,str(root),json.dumps(files,ensure_ascii=False),expires.isoformat(),db.now()))
    return {'preview_id':preview_id,'expires_at':expires.isoformat(),'root':str(root),'files':files,'rejected':rejected}
def _inside(path,root):
    try:path.relative_to(root);return True
    except ValueError:return False
def consume_preview(preview_id,selected_ids):
    if not selected_ids: raise ValueError('必须选择至少一个文件')
    row=db.row('local_previews','id',preview_id)
    if not row: raise ValueError('Preview 不存在或已过期')
    if datetime.fromisoformat(row['expires_at'])<datetime.now(timezone.utc): raise ValueError('Preview 已过期')
    entries=json.loads(row['entries_json']); by_id={x['id']:x for x in entries}
    if any(x not in by_id for x in selected_ids): raise ValueError('selected_file_ids 不属于当前 Preview')
    selected=[by_id[x] for x in selected_ids]
    for item in selected:
        path=Path(item['path']); root=Path(row['root']).resolve()
        if not _inside(path.resolve(),root) or not path.exists(): raise ValueError('Preview 文件已移动或逃逸允许目录')
        if path.stat().st_size!=item['size'] or int(path.stat().st_mtime)!=int(datetime.fromisoformat(item['mtime']).timestamp()) or sha_bytes(path.read_bytes())!=item.get('hash'): raise ValueError('Preview 文件已发生变化，请重新预览')
    return selected
