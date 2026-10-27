"""Real NewPlan/PlanDetail React Flow regression using Qt Chromium."""
import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import pytest


def _make_graph_modules(raw):
    source = raw["material_structure"]["modules"]
    modules = []
    for index, item in enumerate(source):
        modules.append({**item,
            "summary": item.get("summary") or f"学习{item['title']}中的关键概念。",
            "chapters": item.get("chapters") or [item["title"]],
            "refs": item.get("refs") or [f"s1.{index + 1}"],
            "prerequisites": [source[index - 1]["key"]] if index else [],
            "minutes": [20, 35],
            "estimated_points": [1, 2], "estimated_point_count": 1,
            "estimated_question_count": 1 if index % 3 else 0,
            "has_material_questions": index % 3 != 0,
            "points": [{"title": f"{item['title']}知识点", "summary": "能说明本模块的主要设计与依据。",
                        "citations": [{"chunk_id": f"c{index + 1}", "title": "草稿资料", "url": None,
                                      "locator": f"第 {index + 1} 节", "quote": "依据草稿资料逐字保留的测试引用。"}]}]})
    return modules


def _draft(raw, phase, modules):
    result = json.loads(json.dumps(raw, ensure_ascii=False))
    result["phase"] = phase
    result["busy"] = None
    result["generation"] = {}
    result["messages"] = [message for message in result.get("messages", [])
                          if message.get("kind") != "material_preview" and message.get("kind") != "outline"]
    if phase == "material_preview":
        result["messages"].append({"id": "graph198-structure", "role": "ai", "kind": "material_preview", "text": "已按资料整理结构。", "version": 1})
        result["material_structure"]["modules"] = modules
        result["material_structure"]["version"] = 1
        result["material_structure"].setdefault("missing", [])
        result["material_structure"]["ranges"] = {
            "points": [14, 28], "learning": [112, 224], "module_check": [0, 40], "final_check": [0, 20],
            "input_tokens": [5000, 12000], "total": [112, 284], "daily": [40, 80], "cap": 300,
            "over_cap": False, "suggested_end": None, "estimated_question_count": 10, "modules_with_questions": 9,
        }
    else:
        result["messages"].append({"id": "graph198-outline", "role": "ai", "kind": "outline", "text": "完整知识地图。", "version": 1})
        result["outline"] = {"version": 1, "stages": [{"title": "基础阶段", "modules": modules}],
                             "ranges": {"total": [400, 500], "daily": [40, 60], "cap": 300,
                                        "over_cap": False, "suggested_end": None}}
    return result


def _data(cases):
    draft_path = os.environ.get("GRAPH198_DRAFT")
    if draft_path is not None:
        source = Path(draft_path)
        if not source.is_file():
            raise FileNotFoundError(f"GRAPH198_DRAFT does not exist: {source}")
        source_bytes = source.read_bytes()
        raw = json.loads(source_bytes.decode("utf-8"))
        if raw.get("phase") != "material_preview" or len(raw.get("material_structure", {}).get("modules", [])) != 14:
            raise AssertionError("graph198 copied user draft must be a 14-module material_preview draft")
    else:
        source_bytes = b""
        # Deterministic equivalent fixture for other developer machines.
        source_modules = [{"key": f"m{i + 1}", "title": f"模块{i + 1:02}｜示例知识主题", "summary": "理解资料里的主要概念和设计取舍。",
                           "refs": [f"s1.{i + 1}"], "chapters": [f"第 {i + 1} 章"]} for i in range(14)]
        raw = {"version": 1, "plan_id": "graph198-test", "phase": "material_preview", "name": "14 模块图谱测试",
               "goal": "按资料理解 14 个模块", "form": None,
               "messages": [{"id": "graph198-structure", "role": "ai", "kind": "material_preview",
                             "text": "已按资料整理结构。", "version": 1}],
               "sources": [], "outline": None,
               "generation": {}, "material_structure": {"version": 1, "modules": source_modules, "missing": []}}
    raw_snapshot = json.loads(json.dumps(raw, ensure_ascii=False))
    modules = _make_graph_modules(raw)
    # The material-preview step must render precisely the copied draft. The
    # deterministic fallback above is already a valid structure-step draft.
    drafts = {"structure": raw, "outline": _draft(raw, "outline", modules)}
    detail_modules = [{"id": m["key"], "title": m["title"], "summary": m["summary"], "prerequisites": m["prerequisites"],
                       "question_count": 1 if i % 3 else 0, "passed_questions": 0, "learning_minutes": 24, "check_minutes": 8,
                       "state": "untested", "points": [{"id": f"p{i + 1}", "title": m["points"][0]["title"],
                           "summary": m["points"][0]["summary"], "minutes": 8, "citations": m["points"][0]["citations"]}]}
                      for i, m in enumerate(modules)]
    detail = {"plan": {"id": raw["plan_id"], "title": raw.get("name", "14 模块测试"), "goal": raw.get("goal", "测试目标"),
                        "state": "active", "window": None, "total_minutes": 448},
              "stages": [{"id": "s1", "title": "全部模块", "modules": detail_modules}], "sources": [],
              "daily": {"full_day": 42, "cap": 300, "ok": True, "suggested_end": None}, "point_links_status": "none"}
    return {"defaultPhase": cases, "drafts": drafts, "detail": detail,
            "moduleTitles": [m["title"] for m in modules], "cases": [cases],
            "structureDraftSha256": hashlib.sha256(source_bytes).hexdigest() if source_bytes else None,
            "structureDraftUnmodified": drafts["structure"] == raw_snapshot}


def build(root: Path, case: str):
    root.mkdir(parents=True, exist_ok=True)
    repo = Path(__file__).resolve().parents[3]
    here = Path(__file__).parent
    deps = Path(r"D:\MentorFlow\.local\web-deps")
    node = Path(r"D:\MentorFlow\.local\node\node.exe")
    data = _data(case)
    (root / "graph198-data.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    (root / "index.html").write_text('<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style id="graph198-init-hide">.chat-scroll,.plan-graph[data-testid="detail-map"]{display:none!important}</style><div id="root"></div><script type="module" src="/entry.tsx"></script>', encoding="utf-8")
    (root / "entry.tsx").write_text(
        'import ' + json.dumps((repo / "refactor/learning/web/src/theme.css").as_posix()) + ";\n"
        'import data from "./graph198-data.json"; (window as any).graph198Data=data;\n'
        'await import(' + json.dumps((here / "graph198_browser.tsx").as_posix()) + ");\n", encoding="utf-8")
    (root / "vite.config.mjs").write_text(
        "import base from " + json.dumps((repo / "refactor/learning/web/vite.config.mjs").as_uri())
        + ";export default {...base,root:" + json.dumps(root.as_posix())
        + ',resolve:{alias:[{find:"../bridge",replacement:' + json.dumps((here / "graph198_bridge.ts").as_posix())
        + '},{find:"./bridge",replacement:' + json.dumps((here / "graph198_bridge.ts").as_posix())
        + '}]},build:{outDir:"dist",emptyOutDir:true,target:"esnext"}}', encoding="utf-8")
    env = dict(os.environ, MENTORFLOW_WEB_DEPS=deps.as_posix(), TEMP=root.as_posix(), TMP=root.as_posix())
    env["PATH"] = str(Path(r"D:\MentorFlow\.local\node")) + os.pathsep + env.get("PATH", "")
    result = subprocess.run([str(node), str(deps / "node_modules/vite/bin/vite.js"), "build", "--config", str(root / "vite.config.mjs")],
                            env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr


def browser(root: Path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --no-sandbox")
    from PySide6.QtCore import QUrl
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineWidgets import QWebEngineView
    import time

    app = QApplication([])
    view = QWebEngineView()
    view.resize(1280, 900)
    view.show()
    load_results = []
    view.loadFinished.connect(load_results.append)
    view.setUrl(QUrl.fromLocalFile(str(root / "dist/index.html")))
    artifacts = Path(os.environ.get("GRAPH198_ARTIFACTS", str(root / "artifacts")))
    artifacts.mkdir(parents=True, exist_ok=True)

    def pump():
        app.processEvents()

    def js(code):
        values = []
        view.page().runJavaScript(code, values.append)
        end = time.monotonic() + 15
        while not values and time.monotonic() < end:
            pump()
            time.sleep(.004)
        assert values, f"JavaScript callback timed out: {code}"
        return values[0]

    end = time.monotonic() + 60
    while not load_results and time.monotonic() < end:
        pump()
    assert load_results, "Qt WebEngine page load did not finish"
    assert load_results[-1], "Qt WebEngine page load failed"
    while not js("!!window.graph198Result"):
        name = js("window.graph198Capture || null")
        if name and js("window.graph198CaptureAck !== " + json.dumps(name)):
            path = artifacts / f"{name}.png"
            assert view.grab().save(str(path)), f"could not save {path}"
            js("window.graph198CaptureAck=" + json.dumps(name) + "; true")
        assert time.monotonic() < end, "graph198 browser scenarios timed out"
        pump()
        time.sleep(.008)
    raw = js("(()=>{try{return JSON.stringify(window.graph198Result)}catch(e){return 'SERIALIZE_ERROR:'+String(e.stack||e)}})()")
    assert isinstance(raw, str) and raw.startswith("{") , f"could not serialize browser result: {raw!r}"
    result = json.loads(raw)
    case = os.environ.get("GRAPH198_CASE", "unknown")
    (artifacts / f"browser-result-{case}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    view.close()
    app.processEvents()
    print(json.dumps(result, ensure_ascii=False))
    assert result.get("ok"), result


@pytest.mark.parametrize("case", ["structure", "outline", "detail"])
def test_graph198_real_newplan_and_detail_graphs(case, tmp_path):
    build(tmp_path, case)
    artifacts = Path(os.environ.get("GRAPH198_ARTIFACTS", str(tmp_path / "artifacts")))
    artifacts.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, GRAPH198_ARTIFACTS=artifacts.as_posix(), TEMP=tmp_path.as_posix(), TMP=tmp_path.as_posix(),
               PYTHONIOENCODING="utf-8", PYTHONUTF8="1", GRAPH198_CASE=case)
    env["PATH"] = str(Path(r"D:\MentorFlow\.local\node")) + os.pathsep + env.get("PATH", "")
    run = subprocess.run([sys.executable, "-B", str(Path(__file__)), str(tmp_path)], env=env,
                         capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=100)
    assert run.returncode == 0, run.stdout + run.stderr
    evidence = json.loads((artifacts / f"browser-result-{case}.json").read_text(encoding="utf-8"))
    assert evidence["ok"]
    if case == "structure" and evidence["structureDraftSha256"]:
        assert evidence["structureDraftUnmodified"]


def test_graph198_fallback_has_a_renderable_structure_without_private_temp_data(monkeypatch, tmp_path):
    monkeypatch.delenv("GRAPH198_DRAFT", raising=False)
    monkeypatch.chdir(tmp_path)
    data = _data("structure")
    draft = data["drafts"]["structure"]
    assert draft["phase"] == "material_preview"
    assert len(draft["material_structure"]["modules"]) == 14
    assert any(message["kind"] == "material_preview" for message in draft["messages"])
    assert data["structureDraftSha256"] is None
    assert data["structureDraftUnmodified"]


def test_graph198_explicit_draft_is_read_as_is_and_bad_path_fails(monkeypatch, tmp_path):
    modules = [{"key": f"m{i + 1}", "title": f"模块{i + 1}", "summary": "本模块的学习目标。",
                "chapters": [f"第{i + 1}章"], "refs": [f"s1.{i + 1}"], "prerequisites": []}
               for i in range(14)]
    raw = {"version": 1, "plan_id": "copied", "phase": "material_preview", "name": "copy",
           "goal": "test", "form": None,
           "messages": [{"id": "copied-structure", "role": "ai", "kind": "material_preview",
                         "text": "原始结构内容", "version": 1}],
           "sources": [], "outline": None, "generation": {},
           "material_structure": {"version": 1, "modules": modules, "missing": [],
                                  "ranges": {"estimated_question_count": 0, "modules_with_questions": 0}}}
    source = tmp_path / "copied-draft.json"
    source.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    original = source.read_bytes()
    monkeypatch.setenv("GRAPH198_DRAFT", str(source))
    data = _data("structure")
    assert source.read_bytes() == original
    assert data["drafts"]["structure"] == raw
    assert data["structureDraftSha256"] == hashlib.sha256(original).hexdigest()
    assert data["structureDraftUnmodified"]
    assert any(message["kind"] == "material_preview" for message in data["drafts"]["structure"]["messages"])

    monkeypatch.setenv("GRAPH198_DRAFT", str(tmp_path / "missing.json"))
    with pytest.raises(FileNotFoundError, match="GRAPH198_DRAFT does not exist"):
        _data("structure")


if __name__ == "__main__":
    browser(Path(sys.argv[1]))
