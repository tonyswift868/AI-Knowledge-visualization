"""Real AccountProfilePanel -> QWebChannel -> local HTTPS profile server flow."""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parents[3]
NODE = Path("D:/MentorFlow/.local/node/node.exe")
DEPS = Path("D:/MentorFlow/.local/web-deps")
WEB = REPO / "refactor/learning/web"
sys.path.insert(0, str(REPO))
PROFILE_TESTS = Path(__file__).resolve().parents[1] / "profile229"
if str(PROFILE_TESTS) not in sys.path:
    sys.path.insert(0, str(PROFILE_TESTS))
pytest_plugins = ["support_profile229"]

from support_profile229 import new_account229  # noqa: E402


def _build(root: Path):
    (root / "entry.tsx").write_text("import " + json.dumps((root / "profile242_web_entry.tsx").as_posix()), encoding="utf-8")
    (root / "profile242_web_entry.tsx").write_text(_entry_with_paths(), encoding="utf-8")
    (root / "index.html").write_text(
        '<script src="qrc:///qtwebchannel/qwebchannel.js"></script><div id="root"></div>'
        '<script type="module" src="/entry.tsx"></script>', encoding="utf-8")
    vite = f'''import base from {json.dumps((WEB / "vite.config.mjs").as_uri())};
export default {{...base,root:{json.dumps(root.as_posix())},build:{{...base.build,outDir:"dist",emptyOutDir:true}}}};'''
    (root / "vite.config.mjs").write_text(vite, encoding="utf-8")
    env = dict(os.environ, MENTORFLOW_WEB_DEPS=DEPS.as_posix())
    result = subprocess.run([str(NODE), str(DEPS / "node_modules/vite/bin/vite.js"), "build",
                             "--config", str(root / "vite.config.mjs")], env=env,
                            capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr


_ENTRY = r'''import * as React from 'react'
import {createRoot} from 'react-dom/client'
import {connect} from 'REF_BRIDGE'
import {AccountMenu} from 'REF_APP'
import {AccountProfilePanel} from 'REF_PANEL'
import {useApp} from 'REF_STORE'
import 'REF_THEME'
const account={name:'login-name',nickname:'',role_label:'学习者',settings_available:true,offline:false,account_id:'profile242-web-account'}
function Harness(){return <div className="shell" style={{display:'grid',gridTemplateColumns:'256px minmax(0,1fr)',height:'100vh',overflow:'hidden'}}>
  <aside className="sidebar" style={{width:256,minWidth:0,position:'relative',boxSizing:'border-box'}}><AccountMenu/></aside>
  <main className="main" style={{minWidth:0,minHeight:0,display:'flex',flexDirection:'column'}}>
    <header className="topbar"><span>账号资料 HTTPS 集成验证</span></header>
    <div className="content" style={{minWidth:0,overflowY:'auto',overflowX:'hidden'}}>
      <div className="page" style={{maxWidth:1180,width:'100%'}}><div className="fade-in"><AccountProfilePanel/></div></div>
    </div>
  </main>
</div>}
async function start(){(window as any).__profileConnected=await connect();useApp.getState().set({account,route:'settings',settingsTab:'account',settings:{theme:'sakura',font_scale:1,reduce_motion:false} as never});createRoot(document.getElementById('root')!).render(<Harness/>);(window as any).__profile242Ready=true}
void start()
'''


def _entry_with_paths():
    return (_ENTRY.replace("REF_BRIDGE", (WEB / "src/bridge.ts").as_posix())
            .replace("REF_APP", (WEB / "src/App.tsx").as_posix())
            .replace("REF_PANEL", (WEB / "src/profile/AccountProfilePanel.tsx").as_posix())
            .replace("REF_STORE", (WEB / "src/store.ts").as_posix())
            .replace("REF_THEME", (WEB / "src/theme.css").as_posix()))


def _host(root: Path, config_path: Path):
    from PySide6.QtCore import QTimer, QUrl
    from PySide6.QtWidgets import QApplication, QFileDialog
    from PySide6.QtWebChannel import QWebChannel
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from sqlalchemy import create_engine, text
    from types import SimpleNamespace

    from refactor.desktop.account_profile import AccountProfileClient
    from refactor.learning.app import account_profile as profile_bridge
    from refactor.learning.app.bridge import AppBridge
    config = json.loads(config_path.read_text(encoding="utf-8"))
    artifacts = root / "server"
    artifacts.mkdir(parents=True, exist_ok=True)
    engine = create_engine(config["db_url"])
    tls_ca = config["ca"]
    monkeypatch_qt = QFileDialog.__dict__["getOpenFileName"]
    QFileDialog.getOpenFileName = staticmethod(lambda *_args: (str(root / "selected-avatar.png"), "PNG"))
    try:
        app = QApplication([])
        view = QWebEngineView()
        profile = QWebEngineProfile("profile242-web-https", view)
        profile.setPersistentStoragePath(str(root / "web-data"))
        profile.setCachePath(str(root / "web-cache"))
        page = QWebEnginePage(profile, view)
        view.setPage(page)
        page.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        channel = QWebChannel(page)
        bridge = AppBridge(page)
        bridge.event.connect(lambda raw: (root / "wire.jsonl").open("a", encoding="utf-8").write(raw + "\n"))
        account_id = config["account_id"]
        session = SimpleNamespace(identity=SimpleNamespace(account_id=account_id), access_token=config["token"])
        client = AccountProfileClient(account_id=account_id, data_root=root / "cache",
            session_provider=lambda: session, api_base_url=config["base_url"], ca_bundle=tls_ca)
        window = SimpleNamespace(services=SimpleNamespace(account_profile=client, _closed=False, _avatar_preview=None),
                                 _session_offline=False, _personal_profile={})
        window.account_info = lambda: {**client.snapshot(), "name": "login-name", "account_id": account_id}
        profile_bridge.register(bridge, window)

        def instrument(method):
            entry = bridge._handlers[method]
            original = entry.function
            def wrapped(params, context):
                with (root / "calls.jsonl").open("a", encoding="utf-8") as output:
                    output.write(json.dumps({"method": method, "stage": "start"}) + "\n")
                result = original(params, context)
                with engine.connect() as connection:
                    row = connection.execute(text("SELECT nickname,signature,avatar_bytes,version FROM account_profiles WHERE account_scope=:scope"),
                                             {"scope": config["scope"]}).mappings().first()
                with (root / "writes.jsonl").open("a", encoding="utf-8") as output:
                    output.write(json.dumps({"method": method, "nickname": row["nickname"] if row else "",
                        "signature": row["signature"] if row else "", "avatar_bytes": bool(row["avatar_bytes"]) if row else False,
                        "version": row["version"] if row else 0, "crop": params.get("crop"),
                        "response_avatar": bool(result.get("avatar_url")) if isinstance(result, dict) else False},
                        ensure_ascii=False) + "\n")
                return result
            bridge._handlers[method] = type(entry)(wrapped, entry.ui, entry.method)
        instrument("account.profile_save_avatar")
        instrument("account.profile_save")
        instrument("account.profile_delete_avatar")
        instrument("account.profile_get")

        from PIL import Image
        image = Image.new("RGB", (320, 200), (180, 50, 80))
        image.paste((40, 120, 210), (160, 0, 320, 200))
        image.save(root / "selected-avatar.png", format="PNG")
        channel.registerObject("mf", bridge)
        page.setWebChannel(channel)
        view.resize(1180, 860)
        view.show()
        view.setUrl(QUrl.fromLocalFile(str(root / "dist/index.html")))
        timer = QTimer()
        def tick():
            if (root / "offline").exists() and session.access_token:
                session.access_token = ""
            if (root / "done").exists():
                app.quit()
        timer.timeout.connect(tick)
        timer.start(50)
        app.exec()
        with engine.connect() as connection:
            row = connection.execute(text("SELECT nickname,signature,avatar_bytes,version FROM account_profiles WHERE account_scope=:scope"),
                                     {"scope": config["scope"]}).mappings().first()
            (root / "final.json").write_text(json.dumps({"nickname": row["nickname"] if row else "",
                "signature": row["signature"] if row else "", "avatar_bytes": bool(row["avatar_bytes"]) if row else False,
                "version": row["version"] if row else 0}, ensure_ascii=False), encoding="utf-8")
        bridge.close()
        client.close()
        view.close()
    finally:
        QFileDialog.getOpenFileName = monkeypatch_qt
        engine.dispose()


_DRIVER = r'''import fs from 'node:fs';import path from 'node:path';
const root=path.resolve(process.argv[2]),endpoint=process.env.MF_CDP_ENDPOINT;
const sleep=(ms=100)=>new Promise(r=>setTimeout(r,ms));
for(let i=0;i<150;i++){try{const p=await(await fetch(endpoint+'/json/list')).json();if(p.some(x=>x.type==='page'&&x.url.startsWith('file:')))break}catch{}await sleep(100)}
const target=(await(await fetch(endpoint+'/json/list')).json()).find(x=>x.type==='page'&&x.url.startsWith('file:'));
if(!target)throw Error('WebEngine account profile page unavailable');
const ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j});let id=0;const pending=new Map();
ws.onmessage=e=>{const x=JSON.parse(e.data);if(x.id){const p=pending.get(x.id);pending.delete(x.id);x.error?p.reject(Error(JSON.stringify(x.error))):p.resolve(x.result)}};
const cdp=(method,params={})=>new Promise((resolve,reject)=>{const n=++id;pending.set(n,{resolve,reject});ws.send(JSON.stringify({id:n,method,params}))});
async function js(expression){const r=await cdp('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value}
async function until(expression){for(let i=0;i<300;i++){if(await js(expression))return;await sleep()}throw Error('timeout: '+expression)}
async function click(label){const p=await js(`(()=>{const e=[...document.querySelectorAll('button')].find(x=>x.textContent.trim()===${JSON.stringify(label)});if(!e)throw Error('button missing: '+${JSON.stringify(label)}+'; body='+document.body.innerText);const r=e.getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()`);await cdp('Input.dispatchMouseEvent',{type:'mousePressed',...p,button:'left',buttons:1,clickCount:1});await cdp('Input.dispatchMouseEvent',{type:'mouseReleased',...p,button:'left',buttons:0,clickCount:1});await sleep(160)}
await cdp('Page.enable');await cdp('Runtime.enable');await until('window.__profile242Ready===true&&!!document.querySelector("[aria-label=个人资料]")');
const startup=await js('({connected:window.__profileConnected,transport:!!window.qt?.webChannelTransport,channel:typeof window.QWebChannel,body:document.body.innerText})');if(!startup.connected)throw Error('QWebChannel did not connect: '+JSON.stringify(startup));
await until('[...document.querySelectorAll("button")].some(b=>b.textContent.trim()==="选择头像")');
await click('选择头像');await until('!!document.querySelector("[role=dialog] .reactEasyCrop_Image")');await until('document.querySelector(".reactEasyCrop_Image")?.naturalWidth===320&&document.querySelector(".reactEasyCrop_Image")?.naturalHeight===200');
const cropArea=await js(`(()=>{const r=document.querySelector('.reactEasyCrop_CropArea').getBoundingClientRect();return{width:r.width,height:r.height}})()`);if(cropArea.width<150||cropArea.height<150||Math.abs(cropArea.width-cropArea.height)>2)throw Error('crop selection must be a visible circle: '+JSON.stringify(cropArea));
const slider=await js(`(()=>{const e=document.querySelector('[aria-label="缩放照片"]'),r=e.getBoundingClientRect();return{x:r.x+r.width*.75,y:r.y+r.height/2}})()`);await cdp('Input.dispatchMouseEvent',{type:'mousePressed',...slider,button:'left',buttons:1});await cdp('Input.dispatchMouseEvent',{type:'mouseMoved',x:slider.x+20,y:slider.y,button:'left',buttons:1});await cdp('Input.dispatchMouseEvent',{type:'mouseReleased',x:slider.x+20,y:slider.y,button:'left',buttons:0});await sleep(300);
const cropValue=await js('document.querySelector("[aria-label=缩放照片]").value');if(Number(cropValue)<=1)throw Error('cropper zoom slider did not move');
await click('保存头像');await until('!document.querySelector("[role=dialog]")&&!!document.querySelector(".mf-profile-avatar img")&&!!document.querySelector(".account-card img")');
await until('document.querySelector("[role=status].mf-profile-saved")?.textContent.includes("头像已保存")');
const avatarState=await js(`({profile:!!document.querySelector('.mf-profile-avatar img'),profileSrc:document.querySelector('.mf-profile-avatar img')?.src,card:!!document.querySelector('.account-card img'),cardSrc:document.querySelector('.account-card img')?.src,round:getComputedStyle(document.querySelector('.account-card .avatar')).borderRadius,version:document.querySelector('[data-testid="profile226"]')?.textContent})`);if(!avatarState.profile||!avatarState.card||avatarState.round!=='50%'||!avatarState.profileSrc?.startsWith('data:image/jpeg;base64,')||!avatarState.cardSrc?.startsWith('data:image/jpeg;base64,'))throw Error('non-empty server avatar response did not update circular sidebar image');
function setValue(sel,value,tag){return js(`(()=>{const e=document.querySelector(${JSON.stringify(sel)});const p=Object.getOwnPropertyDescriptor(${tag}.prototype,'value').set;p.call(e,${JSON.stringify(value)});e.dispatchEvent(new Event('input',{bubbles:true}))})()`)}
await setValue('input[aria-label="昵称"]','HTTPS昵称','HTMLInputElement');await setValue('textarea[aria-label="个性签名"]','本机HTTPS签名','HTMLTextAreaElement');await click('保存个人资料');await until('document.querySelector("[role=status].mf-profile-saved")?.textContent.includes("已保存")');
await click('删除头像');await until('!document.querySelector(".mf-profile-avatar img")&&!document.querySelector(".account-card img")');const letter=await js('document.querySelector(".account-card .avatar")?.textContent.trim()');if(letter!=='L')throw Error('delete avatar did not restore login-name initial: '+letter);
fs.writeFileSync(path.join(root,'offline'),'1');await sleep(250);await click('重新读取');await until('document.querySelector("button")&&[...document.querySelectorAll("button")].some(b=>b.textContent.trim()==="选择头像"&&b.disabled)');
const offline=await js(`({choose:[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='选择头像').disabled,nickname:document.querySelector('input[aria-label="昵称"]').disabled,signature:document.querySelector('textarea[aria-label="个性签名"]').disabled})`);if(!offline.choose||!offline.nickname||!offline.signature)throw Error('offline editing controls remained enabled');
fs.writeFileSync(path.join(root,'ui-result.json'),JSON.stringify({cropZoom:cropValue,avatarState,letter,offline}));fs.writeFileSync(path.join(root,'done'),'1');ws.close();
'''


def test_profile242_real_webengine_https_editor_to_server(tmp_path, profile_env229):
    root = Path(tmp_path) / "web-https-e2e"
    root.mkdir(parents=True, exist_ok=True)
    _build(root)
    driver = root / "drive.mjs"
    driver.write_text(_DRIVER, encoding="utf-8")
    actor = new_account229(profile_env229, "webengine")
    config = {
        "base_url": profile_env229["endpoints"]["java"]["base_url"],
        "ca": str(profile_env229["tls"]["ca"]),
        # The observer reads the disposable fixture DB outside the HTTP actor transaction.
        "db_url": profile_env229["seed_url"],
        "account_id": actor["account_id"], "scope": actor["scope"],
        "token": actor["headers"]["Authorization"].removeprefix("Bearer "),
    }
    config_path = root / "host-config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    temp = root / "tmp"
    temp.mkdir(exist_ok=True)
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QTWEBENGINE_REMOTE_DEBUGGING=f"127.0.0.1:{port}",
               QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu --remote-allow-origins=*", PYTHONDONTWRITEBYTECODE="1",
               TMP=str(temp), TEMP=str(temp), MF_CDP_ENDPOINT=f"http://127.0.0.1:{port}")
    log = (root / "host.log").open("w", encoding="utf-8")
    child = subprocess.Popen([sys.executable, "-B", str(Path(__file__)), "--host", str(root), str(config_path)],
                             env=env, stdout=log, stderr=log)
    try:
        result = subprocess.run([str(NODE), str(driver), str(root)], env=env, capture_output=True,
                                text=True, encoding="utf-8", timeout=100)
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        (root / "done").touch()
        child.wait(timeout=20)
        log.close()
    if child.returncode != 0:
        raise AssertionError((root / "host.log").read_text(encoding="utf-8", errors="replace"))
    browser = json.loads((root / "ui-result.json").read_text(encoding="utf-8"))
    writes = [json.loads(line) for line in (root / "writes.jsonl").read_text(encoding="utf-8").splitlines()]
    assert browser["avatarState"]["profile"] and browser["avatarState"]["card"]
    assert 1 < float(browser["cropZoom"]) <= 3 and browser["avatarState"]["round"] == "50%"
    assert browser["letter"] == "L" and all(browser["offline"].values())
    avatar_write = next(row for row in writes if row["method"] == "account.profile_save_avatar")
    text_write = next(row for row in writes if row["method"] == "account.profile_save")
    delete_write = next(row for row in writes if row["method"] == "account.profile_delete_avatar")
    assert avatar_write["avatar_bytes"] is True
    assert avatar_write["response_avatar"] is True and avatar_write["crop"]
    assert avatar_write["crop"]["size"] > 0
    assert text_write["nickname"] == "HTTPS昵称" and text_write["signature"] == "本机HTTPS签名"
    assert delete_write["avatar_bytes"] is False


if __name__ == "__main__" and len(sys.argv) > 2 and sys.argv[1] == "--host":
    _host(Path(sys.argv[2]), Path(sys.argv[3]))
