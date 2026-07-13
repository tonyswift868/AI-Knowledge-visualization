from __future__ import annotations
import sys,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from fastapi.testclient import TestClient
from app.main import app
def main():
    with TestClient(app) as c:
        assert c.post('/api/providers/test',json={'provider':'fake','api_key':'secret'}).status_code==200
        assert c.post('/api/providers/test',json={'provider':'openai-compatible','base_url':'http://x','model':'m','api_key':'secret'}).status_code in {502,504}
        root=Path(tempfile.mkdtemp()); (root/'x.md').write_text('x',encoding='utf8')
        assert c.post('/api/local-sources/preview',json={'path':'C:\\','allowed_root':'C:\\'}).status_code==400
        preview=c.post('/api/local-sources/preview',json={'path':str(root),'allowed_root':str(root)}); assert preview.status_code==200
        wid=c.post('/api/workspaces',json={'title':'security'}).json()['id']
        assert c.post(f'/api/workspaces/{wid}/sources/local-path',json={'preview_id':preview.json()['preview_id'],'selected_file_ids':[]}).status_code==400
        assert 'secret' not in preview.text
        print('SECURITY SMOKE PASSED: root rejection, preview confirmation, key redaction path')
if __name__=='__main__': main()
