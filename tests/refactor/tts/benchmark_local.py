"""Manual, opt-in real inference/playback check; not collected by pytest.

Run with the shared client Python from the repo root, optionally --samples.
Writes metrics and generated WAVs only beneath the specified --output path.
No existing service is reused or stopped. Latency is measured when Qt reports
that the output device has consumed PCM, not from an acoustic microphone trace.
"""
from dataclasses import replace
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from refactor.learning.voice.tts.config import TtsConfig
from refactor.learning.voice.tts.runtime import SpeechAudio, TtsRuntime

SAMPLE_TEXTS = ("我们先来看一个生活中的例子。", "这个概念其实和你熟悉的游戏存档很像。", "你刚才的回答方向是对的，只差最后一步。")
PARAGRAPH = """我们先来看一个生活中的例子。你可以把电脑里的内存想成一张书桌，正在使用的资料放在桌上，暂时不用的资料放进柜子。CPU 就像正在处理这些资料的人，需要先找到资料，再按步骤完成任务。下面用一段代码表示这个过程：
```python
for item in data:
    process(item)
```
这里不需要记住每个符号，先理解逐项处理的意思。接下来想一想，如果桌面空间不够，你会怎样安排这些资料？"""


def gpu_memory():
    try:
        return subprocess.check_output(["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                                       text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def spin(app, condition, timeout):
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        app.processEvents()
        if condition():
            return
        time.sleep(.002)
    raise TimeoutError("Qt playback benchmark timed out")


def playback(config, output):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from PySide6.QtMultimedia import QMediaDevices
    from refactor.learning.voice.tts.service import TtsService
    app = QApplication.instance() or QApplication([])
    service = TtsService(config=config)
    report = {"measurement": "QAudioSink processedUSecs / no acoustic loopback", "gpu_before_mib": gpu_memory(),
              "audio_device": QMediaDevices.defaultAudioOutput().description(), "voice": config.voice_id, "trials": [], "errors": []}
    starts = []
    service.error.connect(lambda code, message: report["errors"].append({"code": code, "message": message}))
    service.sentence_started.connect(lambda index, text: starts.append({"index": index, "text": text, "at": time.perf_counter()}))
    try:
        start = time.perf_counter()
        service.warm_up()
        spin(app, lambda: service.state in {"ready", "error"}, config.startup_timeout + 10)
        if report["errors"]:
            report["failed_start_seconds"] = time.perf_counter() - start
            report["status"] = "blocked_before_warmup"
            return report
        report["cold_to_ready_seconds"] = time.perf_counter() - start
        report["health"] = service.runtime.health()
        report["gpu_ready_mib"] = gpu_memory()
        for text in SAMPLE_TEXTS:
            starts.clear()
            start = time.perf_counter()
            service.speak(text)
            spin(app, lambda: bool(starts) or service.state == "error", 90)
            if service.state == "error":
                break
            trial = {"text": text, "first_device_audio_seconds": starts[0]["at"] - start}
            spin(app, lambda: service.state in {"idle", "error"}, 45)
            trial["sentences"] = list(service.last_metrics)
            report["trials"].append(trial)
        # End-to-end ~150-Chinese-character paragraph including code and acronym.
        starts.clear()
        start = time.perf_counter()
        service.speak(PARAGRAPH)
        spin(app, lambda: any("这里有一段代码" in item["text"] for item in starts) or service.state == "error", 90)
        if service.state != "error":
            # Interrupt while the code hint is still playing, after the acronym
            # sentence has traversed the real synthesis and device pipeline.
            limit = time.perf_counter() + .25
            spin(app, lambda: time.perf_counter() >= limit, 1)
            stop = time.perf_counter()
            service.stop()
            report["stop_call_seconds"] = time.perf_counter() - stop
            count = len(starts)
            limit = time.perf_counter() + 1.5
            spin(app, lambda: time.perf_counter() >= limit, 2)
            report["stale_starts_after_stop"] = len(starts) - count
            report["paragraph_started"] = [{**item, "at": item["at"] - start} for item in starts]
            report["paragraph_metrics"] = list(service.last_metrics)
            durations = {item["index"]: item["audio_seconds"] for item in service.last_metrics}
            report["sentence_gap_seconds"] = [max(0, right["at"] - left["at"] - durations[left["index"]])
                for left, right in zip(starts, starts[1:])]
        report["health_after_playback"] = service.runtime.health()
        report["engine_pid"] = service.runtime.pid
        report["runtime_directory"] = str(service.runtime.run_dir)
        report["status"] = "completed" if not report["errors"] else "failed"
    except Exception as exc:
        report["errors"].append({"code": "BENCHMARK_FAILED", "message": str(exc)})
        report["status"] = "failed"
    finally:
        service.close()
        report["owned_process_closed"] = service.runtime.pid is None
        service.deleteLater()
        app.processEvents()
    return report


def samples(config, output):
    results = []
    for voice in config.voices():
        chosen = replace(config, voice_id=voice.pack_id, voice_version=voice.version, style=voice.style)
        result = {"voice": voice.pack_id, "version": voice.version, "mode": "streaming-32-tokens", "samples": []}
        try:
            with TtsRuntime(chosen) as runtime:
                start = time.perf_counter()
                runtime.warm_up()
                result["cold_to_ready_seconds"] = time.perf_counter() - start
                for index, text in enumerate(SAMPLE_TEXTS, 1):
                    start = time.perf_counter()
                    chunks, first = [], []
                    def receive(audio):
                        if audio.pcm and not first:
                            first.append(time.perf_counter() - start)
                        chunks.append(audio)
                    runtime.stream_synthesize(text, receive)
                    audio = SpeechAudio(b"".join(chunk.pcm for chunk in chunks), chunks[0].sample_rate,
                                        sum(chunk.synthesis_seconds for chunk in chunks))
                    filename = f"{voice.pack_id}_{voice.version}_{index:02d}.wav"
                    (output / filename).write_bytes(audio.wav_bytes())
                    result["samples"].append({"file": filename, "text": text, "wall_seconds": time.perf_counter() - start,
                        "first_pcm_seconds": first[0],
                        "synthesis_seconds": audio.synthesis_seconds, "audio_seconds": audio.duration_seconds,
                        "rtf": audio.synthesis_seconds / audio.duration_seconds})
                result["health"] = runtime.health()
        except Exception as exc:
            result["error"] = str(exc)
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        (output / "sample-metrics.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path(r"D:\MentorFlow-office\classroom\temp\tts-samples"))
    parser.add_argument("--samples", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    config = TtsConfig.from_env()
    report = playback(config, args.output)
    (args.output / "playback-metrics.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    succeeded = report.get("status") == "completed"
    if args.samples:
        results = samples(config, args.output)
        succeeded = succeeded and all(len(item["samples"]) == 3 for item in results)
    sys.exit(0 if succeeded else 1)
