from pathlib import Path
import shutil,subprocess,json,hashlib
O=Path(__file__).parent;S=O/"source";P=O/"dist/MentorFlow";PY=Path("E:/Codex/Data/worktrees/2735/MentorFlow/.venv/Scripts/python.exe")
assert (P/"MentorFlow.exe").is_file() and (P/"MentorFlowSelfCheck.exe").is_file()
for src,dst in [("docs/distribution-readme.txt","使用说明.txt"),("scripts/start_ui_review.ps1","启动UI评审.ps1"),("scripts/start_ui_review.cmd","启动UI评审.cmd"),("mentorflow-version.json","mentorflow-version.json")]:shutil.copyfile(S/src,P/dst)
Q=P/"SELF-CHECK";Q.mkdir(exist_ok=True)
(P/"MentorFlowSelfCheck.exe").rename(Q/"MentorFlowSelfCheck.exe")
for name in ["verify_windows_package.py","test_storage_policy.py","mentorflow-check.bat","mentorflow-update.bat"]:shutil.copyfile(S/"scripts"/name,Q/name)
for args in [["build-manifest","--package-root",str(P)],["verify","--package-root",str(P),"--no-data-check","--no-smoke"]]:
 with (O/(args[0]+".log")).open("wb") as log:r=subprocess.run([str(PY),str(S/"scripts/verify_windows_package.py"),*args],stdout=log,stderr=subprocess.STDOUT,cwd=S)
 assert r.returncode==0,args[0]
manifest=json.loads((O/"source-manifest.json").read_text())
report={k:manifest[k] for k in ["source_sha","source_tree","build_id"]}
report.update(artifact=str(P/"MentorFlow.exe"),artifact_sha256=hashlib.sha256((P/"MentorFlow.exe").read_bytes()).hexdigest(),selfcheck_sha256=hashlib.sha256((Q/"MentorFlowSelfCheck.exe").read_bytes()).hexdigest(),package_manifest_sha256=hashlib.sha256((Q/"package-manifest.json").read_bytes()).hexdigest(),spec_sha256=hashlib.sha256((S/"MentorFlow.spec").read_bytes()).hexdigest(),generated_icon_sha256=hashlib.sha256((S/"release/native/MentorFlow.ico").read_bytes()).hexdigest(),package_verifier="PASS",forbidden_icu_dest_count=sum(1 for p in (P/"_internal").glob("icuuc.dll")),scope="TEST_ONLY_NOT_WORKBENCH_CURRENT",reproducibility="EXACT_SOURCE_AND_COMMANDS_RECORDED; BITWISE_REBUILD_NOT_TESTED")
assert report["forbidden_icu_dest_count"]==0
(O/"candidate.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
print(json.dumps(report))
