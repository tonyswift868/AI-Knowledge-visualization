"""Actual React hook and Qt Chromium input; frame-by-frame takeover regression."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

REPO=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(REPO))
NODE='D:/MentorFlow/.local/node/node.exe'


def build139(root,*,before=False):
    root.mkdir(parents=True,exist_ok=True)
    web=REPO/'refactor/learning/web'
    hook=web/'src/useRoundScroll.ts'
    if before:
        from tests.refactor.align133.test_align133_browser import source133
        hook=source133('b1357353','useRoundScroll.ts',root)
    entry=Path(__file__).with_name('browser139.tsx')
    (root/'entry.tsx').write_text('import '+json.dumps(entry.as_posix()),encoding='utf-8')
    (root/'index.html').write_text('<style>body{margin:0;padding:40px}.probe139::-webkit-scrollbar{width:14px}</style><div id="root"></div><script type="module" src="/entry.tsx"></script>',encoding='utf-8')
    (root/'vite.config.mjs').write_text('import base from '+json.dumps((web/'vite.config.mjs').as_uri())+';export default {...base,root:'+json.dumps(root.as_posix())+',resolve:{alias:[{find:"./hook139",replacement:'+json.dumps(hook.as_posix())+'}]},build:{outDir:"dist",emptyOutDir:true}}',encoding='utf-8')
    env=dict(os.environ,MENTORFLOW_WEB_DEPS='D:/MentorFlow/.local/web-deps')
    built=subprocess.run([NODE,'D:/MentorFlow/.local/web-deps/node_modules/vite/bin/vite.js','build','--config',str(root/'vite.config.mjs')],env=env,capture_output=True,text=True,encoding='utf-8',timeout=90)
    assert built.returncode==0,built.stdout+built.stderr


def drive139(root,*,probe=False,program_only=False):
    with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    env=dict(os.environ,QT_QPA_PLATFORM='offscreen',QTWEBENGINE_REMOTE_DEBUGGING=f'127.0.0.1:{port}',
        QTWEBENGINE_CHROMIUM_FLAGS='--disable-gpu --remote-allow-origins=*',MF_CDP_ENDPOINT=f'http://127.0.0.1:{port}',PYTHONDONTWRITEBYTECODE='1')
    with (root/'qt139.log').open('w',encoding='utf-8') as log:
        child=subprocess.Popen([sys.executable,'-B',str(Path(__file__)),'--host',str(root)],env=env,stdout=log,stderr=log)
        try:
            result=subprocess.run([NODE,str(Path(__file__).with_name('browser139.mjs')),str(root),*(['--probe'] if probe else []),*(['--program-only'] if program_only else [])],env=env,capture_output=True,text=True,encoding='utf-8',timeout=120)
            assert result.returncode==0,result.stdout+result.stderr
        finally:(root/'done').touch();child.wait(timeout=15)
    return json.loads((root/'result139.json').read_text(encoding='utf-8'))


def test_scroll139_recap_and_regular_stream_keep_user_control_and_next_round(tmp_path):
    build139(tmp_path);result=drive139(tmp_path)
    assert result['ok'],result


def test_scroll139_programmatic_scroll_events_do_not_take_control(tmp_path):
    build139(tmp_path);result=drive139(tmp_path,program_only=True)
    assert result['ok'],result


if __name__=='__main__':
    from tests.refactor.skip127.test_skip127_browser import host127
    host127(Path(sys.argv[2]))
