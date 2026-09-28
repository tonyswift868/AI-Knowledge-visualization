"""Opt-in real bridge/inference/device comparison; no remote classroom model.

python -B tests/refactor/tts/benchmark_stream.py --output <D: temp directory>
PCM and device-clock traces are retained so gap/latency claims can be checked.
"""
from array import array
import argparse
import bisect
import json
import math
import os
from pathlib import Path
import sys
import time

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from PySide6.QtMultimedia import QMediaDevices
from refactor.learning.app.bridge import AppBridge
from refactor.learning.app.speech import TeacherSpeech
from refactor.learning.voice.tts.runtime import SpeechAudio, TtsRuntime
from refactor.learning.voice.tts.service import TtsService
from refactor.learning.voice.tts.text_prep import split_sentences

TEXT = (
    "我们先来看一个生活中的例子，理解电脑为什么需要内存。"
    "你可以把内存想成一张书桌，正在使用的资料就放在桌上。"
    "硬盘则像旁边的柜子，暂时不用的资料会放回柜子里保存。"
    "CPU 就像坐在桌前工作的人，它每次处理任务都需要先找到资料。"
    "如果桌面足够大，就能同时摆开几份资料，切换任务也会更加方便。"
    "如果桌面已经放满，就要来回收拾，这种等待会让操作显得缓慢。"
    "不过桌子变大并不代表做事的人一定更快，两者解决的是不同问题。"
    "我们判断电脑性能时，需要同时考虑计算速度和正在处理的任务。"
    "例如打开很多浏览器页面时，增加内存可能比单纯更换硬盘更有效。"
    "现在请你想一想，关闭暂时不用的程序，相当于怎样整理这张书桌？"
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--modes", nargs="+", default=["whole", "stream", "stream", "whole"])
    args = parser.parse_args()
    output = args.output.resolve()
    if output.drive.upper() != "D:":
        raise ValueError("Benchmark artifacts must stay on D:")
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    bridge = AppBridge()
    replies, errors, starts, timings, trace, inference, opens = {}, [], [], [], [], [], []
    counter = 0
    def receive(raw):
        value = json.loads(raw)
        if value.get("type") == "response": replies[value["id"]] = value
        if value.get("name") == "speech.error": errors.append(value["data"])
    bridge.event.connect(receive)
    def pump():
        app.processEvents()
        time.sleep(.001)
        if errors: raise RuntimeError(errors)
    def wait(predicate, timeout=240):
        until = time.perf_counter() + timeout
        while not predicate():
            pump()
            if time.perf_counter() > until: raise TimeoutError("Real voice comparison timed out")
    def call(method, **params):
        nonlocal counter
        counter += 1
        key = str(counter)
        bridge.call(json.dumps({"id": key, "method": "speech." + method, "params": params}))
        wait(lambda: key in replies)
        response = replies.pop(key)
        if not response["ok"]: raise RuntimeError(response)
        return response["result"]
    class MeasuredRuntime(TtsRuntime):
        def stream_synthesize(self, text, on_audio, *, cancel=None):
            record = {"text": text, "begin": time.perf_counter(), "chunks": [], "pcm": bytearray()}
            inference.append(record)
            def audio(chunk):
                record["chunks"].append({"at": time.perf_counter(), "frames": len(chunk.pcm) // 2})
                record["rate"] = chunk.sample_rate
                record["pcm"].extend(chunk.pcm)
                on_audio(chunk)
            try: return super().stream_synthesize(text, audio, cancel=cancel)
            finally: record["end"] = time.perf_counter()
    def factory(config, parent):
        service = TtsService(parent, runtime=MeasuredRuntime(config))
        service.sentence_started.connect(lambda i, text: starts.append({"index": i, "text": text, "at": time.perf_counter()}))
        service.sentence_timing.connect(lambda t: timings.append({**t, "at": time.perf_counter()}))
        old_update, old_open = service._update_mouth, service._ensure_output
        def update(played):
            trace.append([time.perf_counter(), played])
            old_update(played)
        def opening(rate):
            if service._sink is not None: return old_open(rate)
            begin = time.perf_counter()
            old_open(rate)
            opens.append({"begin": begin, "end": time.perf_counter()})
        service._update_mouth, service._ensure_output = update, opening
        return service
    voice = TeacherSpeech(bridge, output / "data", service_factory=factory)
    report = {"text": TEXT, "characters": len(TEXT), "input": "4 characters every 80 ms (50 characters/s)",
              "measurement": "real QAudioSink consumed PCM, 10 ms RMS > .003 for audible regions; no acoustic loopback",
              "audio_device": QMediaDevices.defaultAudioOutput().description(), "trials": []}
    runtime = None
    try:
        before = time.perf_counter()
        call("warm_up", owner="benchmark")
        wait(lambda: voice._state in {"ready", "disabled", "error"})
        if voice._state != "ready": raise RuntimeError("Voice not ready: " + voice._state)
        report["cold_to_ready_seconds"] = time.perf_counter() - before
        runtime = voice.service.runtime
        report["runtime_directory"] = str(runtime.run_dir)
        report["health"] = runtime.health()
        print("Warm ready", round(report["cold_to_ready_seconds"], 3), flush=True)
        for number, mode in enumerate(args.modes, 1):
            starts.clear(); timings.clear(); trace.clear(); inference.clear(); opens.clear()
            utterance = f"{mode}-{number}"
            before = time.perf_counter()
            complete = before
            if mode == "whole":
                call("speak", owner="benchmark", utterance_id=utterance, text=TEXT)
            else:
                call("stream_start", owner="benchmark", utterance_id=utterance)
                due = time.perf_counter()
                first_end = TEXT.index("。") + 1
                for position in range(0, len(TEXT), 4):
                    wait(lambda: time.perf_counter() >= due)
                    if position < first_end <= position + 4: complete = time.perf_counter()
                    call("stream_append", owner="benchmark", utterance_id=utterance, text=TEXT[position:position + 4])
                    due += .08
                call("stream_end", owner="benchmark", utterance_id=utterance)
            input_end = time.perf_counter()
            wait(lambda: len(starts) == len(split_sentences(TEXT)) and voice.service.state == "idle")
            end = time.perf_counter()
            frames = [item[1] for item in trace]
            def wall(frame):
                i = bisect.bisect_left(frames, max(1, frame))
                if i >= len(trace): return trace[-1][0]
                if not i: return trace[i][0]
                a, b = trace[i - 1], trace[i]
                return a[0] + (b[0] - a[0]) * (frame - a[1]) / (b[1] - a[1])
            offset, regions, details, pcm = 0, [], [], bytearray()
            for i, rec in enumerate(inference):
                samples = array("h", rec["pcm"])
                rate = rec["rate"]
                window = rate // 100
                active = [j for j in range(0, len(samples), window)
                          if math.sqrt(sum(v * v for v in samples[j:j + window]) / len(samples[j:j + window])) / 32768 > .003]
                assert active, rec["text"]
                regions.append([wall(offset + active[0]), wall(offset + min(active[-1] + window, len(samples)))])
                details.append({**{k: v for k, v in rec.items() if k != "pcm"}, "audio_seconds": len(samples) / rate,
                                "leading_silence": active[0] / rate, "trailing_silence": (len(samples) - min(active[-1] + window, len(samples))) / rate})
                offset += len(samples)
                pcm.extend(rec["pcm"])
            (output / f"{utterance}.wav").write_bytes(SpeechAudio(bytes(pcm), rate, 0).wav_bytes())
            trial = {"mode": mode, "start": before, "first_sentence_complete": complete,
                     "first_device_seconds": starts[0]["at"] - complete, "first_audible_seconds": regions[0][0] - complete,
                     "first_pcm_seconds": details[0]["chunks"][0]["at"] - complete,
                     "input_seconds": input_end - before, "total_seconds": end - before,
                     "playback_span_seconds": end - starts[0]["at"],
                     "sentence_gaps_seconds": [max(0, b[0] - a[1]) for a, b in zip(regions, regions[1:])],
                     "extra_boundary_gap_seconds": [max(0, b["at"] - a["at"] - details[i]["audio_seconds"])
                          for i, (a, b) in enumerate(zip(starts, starts[1:]))],
                     "inference": details, "device_trace": list(trace), "starts": list(starts),
                     "timings": list(timings), "device_open": list(opens), "wav": f"{utterance}.wav"}
            report["trials"].append(trial)
            (output / "stream-measurements.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({k: v for k, v in trial.items() if k not in {"inference", "device_trace", "starts", "timings"}}, ensure_ascii=False), flush=True)
        starts.clear()
        call("stream_start", owner="benchmark", utterance_id="stop")
        call("stream_append", owner="benchmark", utterance_id="stop", text=TEXT)
        wait(lambda: bool(starts))
        before = time.perf_counter()
        call("stop", owner="benchmark", utterance_id="stop")
        report["stop_bridge_seconds"] = time.perf_counter() - before
        count = len(starts)
        until = time.perf_counter() + 1
        wait(lambda: time.perf_counter() >= until)
        report["starts_after_stop"] = len(starts) - count
        report["mouth_after_stop"] = voice.service._mouth_value
        report["status"] = "completed"
    except Exception as exc:
        report["status"], report["error"] = "failed", repr(exc)
        print(report["error"], flush=True)
    finally:
        voice.dispose(); bridge.close(); app.processEvents()
        report["owned_engine_closed"] = runtime.pid is None if runtime else True
        (output / "stream-measurements.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["status"] == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
