"""Opt-in real Qt/WebGL preview, screenshots and lifecycle checks. No server.

Build web into --dist first. All output belongs to --output on D:; this harness
never binds a real account, changes saved asset settings, or downloads resources.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time

os.environ.setdefault("QSG_RHI_BACKEND", "opengl")
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QApplication
from refactor.learning.app import window as module
from refactor.learning.app.teacher_assets import TeacherAssetCatalog


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    module.WEB_ROOT = args.dist.resolve()
    report = {"console": [], "checks": {}, "screenshots": [], "renderer": "QSG_RHI_BACKEND=opengl / real QWebEngineView"}

    class Page(module._AppPage):
        def javaScriptConsoleMessage(self, level, message, line, source):
            report["console"].append({"level": level.name, "message": message, "line": line, "source": source})
            if level.name == "ErrorMessageLevel":
                print("JS ERROR:", message, flush=True)

    module._AppPage = Page
    app = QApplication.instance() or QApplication([])
    win = module.LearningWindow("组件检查 · 本地账号", "LEARNER", "离线检查", account_id="mfa1_" + "6" * 64,
                                data_root=args.output / "data")

    def pump(seconds):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            app.processEvents()
            time.sleep(.005)

    def js(expression):
        result = []
        win.page.runJavaScript("JSON.stringify((" + expression + "))", lambda value: result.append(value))
        until = time.monotonic() + 5
        while not result and time.monotonic() < until:
            pump(.01)
        if not result:
            raise TimeoutError("JavaScript callback did not return")
        return json.loads(result[0]) if result[0] else None

    def wait(expression, timeout=40):
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            if js(expression):
                return
            if any(item["level"] == "ErrorMessageLevel" for item in report["console"]):
                raise AssertionError("browser console error: " + report["console"][-1]["message"])
            pump(.05)
        raise AssertionError("preview condition failed: " + expression + " / " + str(js("document.body.innerText")))

    def click(selector):
        js("document.querySelector(" + json.dumps(selector) + ").click()")

    def shot(name):
        pump(.1)
        assert win.grab().save(str(args.output / name))
        report["screenshots"].append(name)
        print("PREVIEW", time.strftime("%H:%M:%S"), name, flush=True)

    snap = "window.__mfTeacherPreview?.snapshot()"
    try:
        url = QUrl.fromLocalFile(str(args.dist / "index.html"))
        url.setFragment("/dev/teacher")
        win.resize(1480, 980)
        win.show()
        win.view.setUrl(url)
        wait("document.querySelector('[data-teacher-status=ready]') !== null")
        pump(3)
        # Measure an uninterrupted interval: saving PNGs stalls the UI thread.
        before = js(snap)
        started = time.monotonic()
        readings = []
        for _ in range(8):
            pump(1)
            readings.append(js(snap))
        elapsed = time.monotonic() - started
        report["checks"]["steady_fps"] = {
            "seconds": elapsed, "average": (readings[-1]["frames"] - before["frames"]) / elapsed,
            "samples": [reading["fps"] for reading in readings],
        }
        report["checks"]["idle"] = js(snap)
        assert js(snap + ".parameters.ParamAngleX === 0 && " + snap + ".parameters.ParamAngleY === 0")
        assert js(snap + ".parameters.ParamMouthOpenY === 0")
        shot("01-idle.png")

        click('[data-mouth="open"]')
        pump(.35)
        report["checks"]["mouth_open"] = js(snap)
        assert js(snap + ".parameters.ParamMouthOpenY > .7")
        shot("02-mouth-open.png")
        click('[data-mouth="closed"]')
        pump(.1)
        assert js(snap + ".parameters.ParamMouthOpenY === 0")
        report["checks"]["mouth_closed"] = True

        for index, name in enumerate(("nod", "shake", "think", "happy"), 3):
            click(f'[data-reaction="{name}"]')
            pump(.32 if name in {"nod", "shake"} else .65)
            report["checks"][name] = js(snap)
            assert report["checks"][name]["reaction"] == name
            shot(f"{index:02d}-{name}.png")
            wait(snap + ".reaction === null", 5)
            assert js(snap + ".parameters.ParamAngleX === 0 && " + snap + ".parameters.ParamAngleY === 0")

        # Feed the imperative API exactly as a 20 Hz mouth_level consumer would.
        for _ in range(12):
            js("window.__mfTeacherPreview.mouthSample(.65)")
            pump(.05)
        assert js(snap + ".parameters.ParamMouthOpenY > .6")
        js("window.__mfTeacherPreview.mouthSample(0)")
        pump(.04)
        assert js(snap + ".parameters.ParamMouthOpenY === 0")
        js("window.__mfTeacherPreview.mouthSample(.7)")
        pump(.4)
        assert js(snap + ".parameters.ParamMouthOpenY === 0")
        js("window.__mfTeacherPreview.mouthSample(2)")
        pump(.15)
        assert js(snap + ".parameters.ParamMouthOpenY <= 1 && " + snap + ".parameters.ParamMouthOpenY > .9")
        js("window.__mfTeacherPreview.mouthSample(-1)")
        pump(.04)
        assert js(snap + ".parameters.ParamMouthOpenY === 0")
        report["checks"]["stream_stop_and_stale_timeout"] = True

        for toggle in ("pause", "visible"):
            click(f'[data-toggle="{toggle}"]')
            pump(.15)
            before = js(snap)
            pump(.6)
            after = js(snap)
            assert before["frames"] == after["frames"] and not after["active"]
            report["checks"][toggle] = {"before": before["frames"], "after": after["frames"]}
            click(f'[data-toggle="{toggle}"]')
            wait(snap + ".active === true", 3)

        # Actual window visibility must stop rendering as well as the explicit prop.
        print("PREVIEW", time.strftime("%H:%M:%S"), "minimize/restore", flush=True)
        win.showMinimized()
        pump(.7)
        before = js(snap)
        pump(.5)
        after = js(snap)
        assert not after["active"] and before["frames"] == after["frames"]
        report["checks"]["minimized"] = {"before": before["frames"], "after": after["frames"]}
        win.showNormal()
        pump(.7)
        print("PREVIEW", time.strftime("%H:%M:%S"), "fullscreen", flush=True)
        win.showFullScreen()
        pump(1)
        report["checks"]["fullscreen"] = js(snap)
        shot("07-fullscreen.png")
        win.showNormal()
        pump(.5)
        print("PREVIEW", time.strftime("%H:%M:%S"), "resize", flush=True)
        win.resize(1100, 860)
        pump(.6)
        report["checks"]["resized"] = js(snap)
        assert report["checks"]["resized"]["width"] != report["checks"]["idle"]["width"]
        shot("09-resized.png")
        win.resize(1480, 980)
        pump(.5)

        click('[data-toggle="mount"]')
        pump(.3)
        assert js("document.querySelectorAll('.teacher-stage canvas').length") == 0
        click('[data-toggle="mount"]')
        wait("document.querySelector('[data-teacher-status=ready]') !== null")
        pump(.5)
        assert js("document.querySelectorAll('.teacher-stage canvas').length") == 1
        report["checks"]["unmount_reload"] = True
        report["reactions"] = js("window.__mfTeacherPreview.events")

        # Inject a genuinely absent model directory in this test window only.
        click('[data-toggle="mount"]')
        pump(.2)
        win.teacher_assets.catalog = TeacherAssetCatalog(args.output / "missing-model")
        click('[data-toggle="mount"]')
        wait("document.querySelector('[data-teacher-status=missing]') !== null")
        assert js("document.body.innerText.includes('老师形象未安装') && document.body.innerText.includes('选择已安装的模型')")
        assert js("[...document.querySelectorAll('[data-reaction]')].every(button => button.disabled)")
        shot("08-missing-model.png")
        report["checks"]["missing_model"] = True
        assert not [m for m in report["console"] if m["level"] == "ErrorMessageLevel"]
        report["status"] = "passed"
    except Exception as exc:
        report["status"], report["failure"] = "failed", str(exc)
        shot("failure.png")
    finally:
        win.close()
        pump(.2)
        (args.output / "preview-results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in {"console", "reactions"}}, ensure_ascii=False, indent=2), flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
