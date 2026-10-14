"""Fixed browser replay across explicit source revisions, no checkout mutation."""
import json,sys
from pathlib import Path
REPO=Path(__file__).resolve().parents[3];sys.path.insert(0,str(REPO))
from tests.refactor.align133.test_align133_browser import build133,drive133

def compare133(root,refs):
    rows=[]
    for ref in refs:
        target=root/ref.replace('^','-parent')
        try:
            build133(target,ref);drive133(target,probe=True,ordinary=True)
            result=json.loads((target/'result133.json').read_text(encoding='utf-8'))
            row=dict(ref=ref,result=result)
        except Exception as error:row=dict(ref=ref,error=str(error))
        rows.append(row);print(json.dumps(row,ensure_ascii=False),flush=True)
        (root/'comparison133.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')

if __name__=='__main__':compare133(Path(sys.argv[1]),sys.argv[2:])
