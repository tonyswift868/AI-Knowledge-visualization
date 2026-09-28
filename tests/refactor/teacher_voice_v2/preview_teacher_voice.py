"""Opt-in real Qt + GPT-SoVITS + Phrolova 1.0.4 check; never collected by pytest.

Uses only a local test account, no account server. Pass a built --dist and a D:
--output. First sound is measured at QAudioSink consumption, not a microphone.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import statistics
import sys
import time

os.environ.setdefault("QSG_RHI_BACKEND", "opengl")
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from PySide6.QtCore import QUrl
from PySide6.QtMultimedia import QMediaDevices
from PySide6.QtWidgets import QApplication
from refactor.learning.app import window as module


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    module.WEB_ROOT = args.dist.resolve()
    report = {"console": [], "events": [], "screenshots": [], "checks": {}, "errors": [],
              "measurement": "Qt device-consumed PCM / no acoustic loopback"}
    class Page(module._AppPage):
        def javaScriptConsoleMessage(self, level, message, line, source):
            report["console"].append({"level": level.name, "message": message, "line": line, "source": source})
            if level.name == "ErrorMessageLevel": print("JS ERROR", message, flush=True)
    module._AppPage = Page
    app = QApplication.instance() or QApplication([])
    win = module.LearningWindow("朗读验收 · 本地账号", "LEARNER", "本地检查", account_id="mfa1_" + "7" * 64,
                                data_root=args.output / "data")
    def receive(raw):
        message = json.loads(raw)
        if message.get("name", "").startswith("speech."):
            report["events"].append({"at": time.perf_counter(), "name": message["name"], "data": message["data"]})
            if message["name"] == "speech.error": report["errors"].append(message["data"])
    win.bridge.event.connect(receive)
    def pump(seconds):
        until = time.perf_counter() + seconds
        while time.perf_counter() < until:
            app.processEvents(); time.sleep(.003)
    def js(expression):
        values = []
        win.page.runJavaScript("JSON.stringify((" + expression + "))", values.append)
        end = time.perf_counter() + 5
        while not values and time.perf_counter() < end: pump(.01)
        if not values: raise TimeoutError("JavaScript callback")
        return json.loads(values[0]) if values[0] else None
    def wait(predicate, timeout=20):
        until = time.perf_counter() + timeout
        log_at = time.perf_counter() + 10
        while time.perf_counter() < until:
            if predicate(): return
            if report["errors"]: raise AssertionError(report["errors"])
            if time.perf_counter() >= log_at:
                print("Waiting", win.speech._state, js("({state:window.__mfTeacherPreview?.voice()?.state, hidden:document.hidden})"), flush=True)
                log_at = time.perf_counter() + 10
            pump(.02)
        raise TimeoutError("Voice preview timed out: " + str(js("document.body.innerText")))
    def shot(name):
        assert win.grab().save(str(args.output / name))
        report["screenshots"].append(name)
    def click(name):
        js("document.querySelector('[data-voice=" + name + "]').click()")
    state = "window.__mfTeacherPreview?.voice()"
    stage = "window.__mfTeacherPreview?.snapshot()"
    starts = []
    runtime = None
    try:
        url = QUrl.fromLocalFile(str(args.dist / "index.html"))
        url.setFragment("/dev/teacher")
        win.resize(1480, 1080); win.show()
        launched = time.perf_counter()
        win.view.setUrl(url)
        print("Waiting for real model + background warm-up", flush=True)
        wait(lambda: js("document.querySelector('[data-teacher-status=ready]') !== null"), 40)
        wait(lambda: js(state + "?.state === 'ready'"), 230)
        print("Warm-up ready", flush=True)
        report["cold_page_to_ready_seconds"] = time.perf_counter() - launched
        service = win.speech.service
        runtime = service.runtime
        report["engine_health"] = runtime.health()
        report["voice_manifest"] = str(runtime.config.voice().manifest)
        report["audio_device"] = QMediaDevices.defaultAudioOutput().description()
        report["runtime_directory"] = str(runtime.run_dir)
        report["engine_pid"] = runtime.pid
        service.sentence_started.connect(lambda index, text: starts.append({"at": time.perf_counter(), "index": index, "text": text}))
        print("Ready after", report["cold_page_to_ready_seconds"], "seconds", flush=True)
        win.showNormal(); win.raise_(); win.activateWindow()
        wait(lambda: js("!document.hidden"), 5)
        shot("01-voice-ready.png")

        begun = time.perf_counter()
        click("speak")
        wait(lambda: bool(starts), 60)
        report["first_device_audio_seconds"] = starts[0]["at"] - begun
        wait(lambda: js(stage + ".parameters.ParamMouthOpenY > .08"), 10)
        report["checks"]["audible_mouth"] = {"voice": js(state), "stage": js(stage),
            "processed_seconds": service._sink.processedUSecs() / 1e6}
        assert js("Array.from(document.querySelectorAll('[data-mouth]')).every(button => button.disabled)")
        shot("02-reading-mouth.png")
        wait(lambda: len(starts) >= 2, 30)
        report["checks"]["next_sentence"] = js(state)
        shot("03-next-sentence.png")
        before_stop = time.perf_counter()
        click("stop")
        wait(lambda: service.state == "stopped", 3)
        report["stop_seconds"] = time.perf_counter() - before_stop
        wait(lambda: js(stage + ".parameters.ParamMouthOpenY === 0"), 3)
        count = len(starts)
        pump(.7)
        report["checks"]["stop_zero"] = js(stage)
        assert len(starts) == count and service._sink is None
        assert js(state + ".sentence === null")
        shot("04-stopped.png")
        report["paragraph_metrics"] = service.last_metrics
        report["starts"] = list(starts)

        # Native speed control keeps the same process; test at a different speed
        # and confirm the real sentence completes without replaying the old text.
        prior_pid = runtime.pid
        win.bridge.call(json.dumps({"id": "benchmark-speed", "method": "speech.settings", "params": {"speed": 1.25, "volume": .6}}))
        pump(.04)
        starts.clear()
        win.bridge.call(json.dumps({"id": "benchmark-sentence", "method": "speech.speak", "params": {
            "owner": "benchmark", "utterance_id": "speed-trial", "text": "这个概念其实和你熟悉的游戏存档很像。"}}))
        trial_at = time.perf_counter()
        wait(lambda: bool(starts), 30)
        report["speed_trial_first_audio_seconds"] = starts[0]["at"] - trial_at
        wait(lambda: service.state == "idle", 30)
        report["speed_trial_metrics"] = service.last_metrics
        report["checks"]["resident_after_speed_change"] = runtime.pid == prior_pid
        assert runtime.pid == prior_pid

        # Complete the full preview paragraph too. Do not capture PNGs while it
        # plays: synchronous PNG encoding would distort timer cadence measurements.
        starts.clear()
        full_at = time.perf_counter()
        click("speak")
        wait(lambda: bool(starts), 30)
        wait(lambda: js(state + ".sentence?.state === 'started'"), 3)
        full_id = js(state + ".sentence?.utterance_id")
        wait(lambda: service.state == "idle", 60)
        wait(lambda: js(state + ".sentence?.state === 'finished'"), 3)
        assert len(starts) == 4 and runtime.pid == prior_pid
        wait(lambda: js(stage + ".parameters.ParamMouthOpenY === 0"), 3)
        full_events = [e for e in report["events"] if e["data"].get("utterance_id") == full_id]
        timings = [e["data"] for e in full_events if e["name"] == "speech.sentence"]
        assert [(e["index"], e["state"]) for e in timings] == [
            (i, phase) for i in range(4) for phase in ("started", "finished")]
        samples = [e for e in full_events if e["name"] == "speech.mouth"]
        cadence = [b["at"] - a["at"] for a, b in zip(samples, samples[1:]) if a["data"]["level"] > 0 and b["data"]["level"] > 0]
        assert cadence and samples[-1]["data"]["level"] == 0
        report["full_paragraph"] = {"first_device_audio_seconds": starts[0]["at"] - full_at,
            "elapsed_seconds": time.perf_counter() - full_at, "sentence_count": len(starts),
            "metrics": service.last_metrics, "timings": timings, "mouth_samples": len(samples),
            "mouth_median_interval_seconds": statistics.median(cadence),
            "mouth_p95_interval_seconds": sorted(cadence)[int((len(cadence) - 1) * .95)],
            "mouth_max": max(e["data"]["level"] for e in samples), "mouth_final": samples[-1]["data"]["level"]}
        wait(lambda: js(state + ".state === 'idle'"), 3)
        pump(.25)  # WebEngine composites the new React state after JS callbacks.
        report["checks"]["natural_finish"] = {"voice_state": js(state + ".state"), "stage": js(stage)}
        shot("05-paragraph-finished.png")
        assert not [m for m in report["console"] if m["level"] == "ErrorMessageLevel"]
        assert not report["errors"]
        report["status"] = "passed"
    except Exception as exc:
        report["status"], report["failure"] = "failed", str(exc)
        shot("failure.png")
    finally:
        win.close(); pump(.3)
        report["owned_process_closed"] = runtime is None or runtime.pid is None
        (args.output / "voice-preview-results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in {"events", "console", "checks"}}, ensure_ascii=False, indent=2), flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
