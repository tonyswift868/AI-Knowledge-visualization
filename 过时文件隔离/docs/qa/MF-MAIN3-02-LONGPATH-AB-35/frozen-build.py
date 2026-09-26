import os,sys,subprocess,zipfile,json,hashlib,time
from pathlib import Path
R=Path('E:\\Codex\\Data\\worktrees\\08ac\\MentorFlow'); O=Path(__file__).parent; S=O/"source"; PY=Path('E:\\Codex\\Data\\worktrees\\2735\\MentorFlow\\.venv\\Scripts\\python.exe')
sha=subprocess.check_output(["git","rev-parse","HEAD"],cwd=R,text=True).strip(); tree=subprocess.check_output(["git","rev-parse","HEAD^{tree}"],cwd=R,text=True).strip()
assert not subprocess.check_output(["git","status","--porcelain"],cwd=R,text=True).strip()
assert not S.exists()
archive=O/"source.zip"; subprocess.run(["git","archive",sha,"--format=zip","-o",str(archive)],cwd=R,check=True)
with zipfile.ZipFile(archive) as z:z.extractall(S)
m=dict(source_sha=sha,source_tree=tree,artifact=str(archive),artifact_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),build_id="MF-02-FROZEN-35-"+sha[:8],commands=[])
(O/"source-manifest.json").write_text(json.dumps(m,indent=2))
env={k:v for k,v in os.environ.items() if not k.startswith(("MENTORFLOW_","PYTHONPATH","QT_"))}; env.update(PYTHONUTF8="1",PYTHONDONTWRITEBYTECODE="1",TEMP=str(O),TMP=str(O),npm_config_cache=str(O/"cache/npm"),PYINSTALLER_CONFIG_DIR=str(O/"pyinstaller-cache"))
def run(label,cmd,cwd):
 print(label,flush=True); m["commands"].append(dict(label=label,argv=cmd)); (O/"build-state.json").write_text(json.dumps(m,indent=2))
 with (O/(label+".log")).open("wb") as log:r=subprocess.run(cmd,cwd=cwd,env=env,stdout=log,stderr=subprocess.STDOUT)
 m[label+"_exit"]=r.returncode; (O/"build-state.json").write_text(json.dumps(m,indent=2)); assert r.returncode==0,label
run("frontend-install",["C:/Program Files/nodejs/npm.cmd","ci","--no-audit","--no-fund"],S/"frontend")
run("frontend-build",["C:/Program Files/nodejs/npm.cmd","run","build"],S/"frontend")
run("lifecycle-render",[str(PY),"scripts/render_account_lifecycle_ui.py","--keycloak",str(Path((O.parent.parent/"uat-root.txt").read_text())/"keycloak-26.7.3"),"--jdk","E:/Codex/Temp/mf-e109-sec04-tools-20260912/jdk-21.0.12.1+1"],S)
run("icon",[str(PY),"scripts/build_desktop_icon.py"],S)
run("pyinstaller",[str(PY),"-m","PyInstaller","--noconfirm","--distpath",str(O/"dist"),"--workpath",str(O/"work"),"MentorFlow.spec"],S)
print("BUILD_COMPLETE",flush=True)
