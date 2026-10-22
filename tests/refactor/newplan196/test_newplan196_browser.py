"""Exercise real React pages in isolated Qt Chromium with a local fake bridge."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

REPO=Path(__file__).resolve().parents[3]
DEPS=Path("D:/MentorFlow/.local/web-deps")
NODE=Path("D:/MentorFlow/.local/node/node.exe")

def build_fixture(root:Path):
    if not NODE.is_file(): pytest.skip("shared node runtime unavailable")
    root.mkdir(parents=True,exist_ok=True)
    here=Path(__file__).parent
    web=REPO/"refactor/learning/web"
    (root/"index.html").write_text('<div id="root"></div><script type="module" src="/entry.tsx"></script>',encoding="utf-8")
    (root/"entry.tsx").write_text('import '+json.dumps((here/"browser196.tsx").as_posix()),encoding="utf-8")
    config='''import path from "node:path"
import {pathToFileURL} from "node:url"
const deps=DEPS
const {default:react}=await import(pathToFileURL(path.join(deps,"node_modules/@vitejs/plugin-react/dist/index.js")))
export default {root:ROOT,base:"./",resolve:{alias:[{find:"../bridge",replacement:BRIDGE}]},plugins:[react(),{name:"deps",enforce:"pre",async resolveId(source,importer,options){if(!importer||source.startsWith(".")||source.startsWith("/")||source.includes(":")||importer.includes("node_modules"))return null;return this.resolve(source,path.join(deps,"package.json"),{...options,skipSelf:true})}}],build:{outDir:"dist",emptyOutDir:true,target:"esnext"}}
'''.replace("DEPS",json.dumps(DEPS.as_posix())).replace("ROOT",json.dumps(root.as_posix())).replace("BRIDGE",json.dumps((here/"bridge196.ts").as_posix()))
    (root/"vite.config.mjs").write_text(config,encoding="utf-8")
    env=dict(os.environ,MENTORFLOW_WEB_DEPS=DEPS.as_posix())
    result=subprocess.run([str(NODE),str(DEPS/"node_modules/vite/bin/vite.js"),"build","--config",str(root/"vite.config.mjs")],env=env,capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=90)
    assert result.returncode==0,result.stdout+result.stderr

def run_qt_browser(root:Path):
    os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS","--disable-gpu")
    from PySide6.QtCore import QUrl
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineCore import QWebEnginePage,QWebEngineProfile,QWebEngineUrlRequestInterceptor
    from PySide6.QtWebEngineWidgets import QWebEngineView
    class Offline(QWebEngineUrlRequestInterceptor):
        def interceptRequest(self,info):
            if info.requestUrl().scheme() in ("http","https"): info.block(True)
    app=QApplication.instance() or QApplication([])
    view=QWebEngineView();profile=QWebEngineProfile(view);page=QWebEnginePage(profile,view)
    blocker=Offline(page);profile.setUrlRequestInterceptor(blocker);view.setPage(page);view.resize(1280,900);view.show()
    def pump(seconds=.05):
        end=time.monotonic()+seconds
        while time.monotonic()<end: app.processEvents();time.sleep(.005)
    def evaluate(script):
        values=[];page.runJavaScript(script,values.append);end=time.monotonic()+3
        while not values and time.monotonic()<end:pump()
        assert values,"Qt Chromium JavaScript evaluation timed out"
        return values[0]
    view.setUrl(QUrl.fromLocalFile(str(root/"dist/index.html")))
    result=None;end=time.monotonic()+35
    while time.monotonic()<end:
        pump()
        raw=evaluate("JSON.stringify(window.plan196Result || null)")
        if raw and raw!="null": result=json.loads(raw);break
    result=result or {"ok":False,"error":"browser did not publish test result"}
    (root/"browser-result.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    if result.get("ok"):
        screenshot=root/"new-plan-ready.png"
        assert view.grab().save(str(screenshot)),"could not save Qt Chromium screenshot"
    view.close();page.deleteLater();pump()
    assert result.get("ok"),result
    return result

def test_newplan196_stale_pointer_archive_and_error_classification(tmp_path):
    build_fixture(tmp_path)
    result=run_qt_browser(tmp_path)
    assert "stale NewPlan pointer recovers to a fresh form after DRAFT_NOT_FOUND" in result["checks"]
    assert "Plans page archive control followed by its NewPlan control opens a fresh form" in result["checks"]
    assert "valid draft continuation still loads its selected ID" in result["checks"]
    assert (tmp_path/"new-plan-ready.png").is_file()
