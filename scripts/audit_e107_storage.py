"""Read-only storage inventory. Does not infer deletion authority from names."""
import json,os,stat,time
from pathlib import Path

ARCHIVE=Path('D:/MentorFlow-UAT-Evidence-Archive')

def scan(root):
    row=dict(path=str(root),size_bytes=0,file_count=0,reparse=[],errors=[],large_apps=[],top_bytes={},evidence=[],contains_source=False)
    todo=[root]
    while todo:
        directory=todo.pop()
        try:entries=list(os.scandir(directory))
        except OSError as e:row['errors'].append([str(directory),str(e)]);continue
        for entry in entries:
            path=Path(entry.path)
            try:s=entry.stat(follow_symlinks=False)
            except OSError as e:row['errors'].append([str(path),str(e)]);continue
            if getattr(s,'st_file_attributes',0)&0x400:row['reparse'].append(str(path));continue
            if stat.S_ISDIR(s.st_mode):
                if entry.name=='.git':row['contains_source']=True;continue
                todo.append(path);continue
            row['size_bytes']+=s.st_size;row['file_count']+=1
            rel=path.relative_to(root);top=rel.parts[0]
            row['top_bytes'][top]=row['top_bytes'].get(top,0)+s.st_size
            if entry.name=='MentorFlow.exe' and s.st_size>20000000:
                manifest=path.parent/'SELF-CHECK/package-manifest.json'
                identity=path.parent/'SELF-CHECK/source-identity.json'
                info=dict(path=str(path.parent),exe_bytes=s.st_size,manifest=manifest.is_file(),identity=None)
                if identity.is_file():
                    try:info['identity']=json.loads(identity.read_bytes())
                    except (ValueError,OSError):pass
                row['large_apps'].append(info)
            if len(rel.parts)<4 and path.suffix.lower() in ('.json','.md','.txt','.log','.jsonl') and s.st_size<8*1024**2:
                if not any(p in ('_internal','App','dist','target','base') for p in rel.parts[:-1]):row['evidence'].append(str(path))
    return row

def main():
    roots=[Path('D:/'+name) for name in ('MF12A','MF12B','MF12C','MF13B','MF14W','MFU7','MFU8','MFUAC')]
    roots+=list(Path('D:/CodexScratch/MentorFlow').glob('MF-E107-*'))
    roots+=list(Path('E:/Codex/Temp').glob('MF*'))
    roots+=list(Path('E:/Downloads').glob('*MentorFlow*'))
    roots=[p for p in roots if p.is_dir()]
    rows=[]
    for root in roots:
        row=scan(root);rows.append(row)
        print(json.dumps(dict(path=row['path'],bytes=row['size_bytes'],apps=len(row['large_apps']),errors=len(row['errors'])),ensure_ascii=False),flush=True)
    ARCHIVE.mkdir(exist_ok=True)
    path=ARCHIVE/('storage-before-'+str(time.time_ns())+'.json')
    path.write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    print('INVENTORY='+str(path),flush=True)

if __name__=='__main__':main()
