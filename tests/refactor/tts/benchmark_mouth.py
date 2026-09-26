"""Opt-in real-device mouth probe using an explicitly generated PCM test tone.

This is signal/playback verification, not a model-generated teacher response.
The engine performance test remains benchmark_local.py.
"""
import argparse
from array import array
import json
import math
import os
from pathlib import Path
import sys
import time
import statistics

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from PySide6.QtMultimedia import QMediaDevices
from refactor.learning.voice.tts.runtime import SpeechAudio
from refactor.learning.voice.tts.service import TtsService
from refactor.learning.voice.tts.playback import PlaybackMeter
from benchmark_local import spin

RATE = 32000
SECTIONS = ((.2, 0), (.4, .18), (.2, 0), (.4, .04), (.2, 0), (.4, .4), (.2, 0))


class ToneRuntime:
    def __init__(self, audio):
        self.audio = audio

    def synthesize(self, text, cancel=None):
        return self.audio

    def cancel(self):
        pass

    def close(self):
        pass


def measure(output):
    pcm = b"".join(array("h", (round(32767 * amplitude * math.sin(2 * math.pi * 220 * i / RATE))
                             for i in range(round(duration * RATE)))).tobytes()
                   for duration, amplitude in SECTIONS)
    audio = SpeechAudio(pcm, RATE, 0)
    (output / "mouth-tone-silence.wav").write_bytes(audio.wav_bytes())
    app = QApplication.instance() or QApplication([])
    service = TtsService(runtime=ToneRuntime(audio))
    report = {"measurement": "generated PCM / actual QAudioSink consumed position; no acoustic loopback",
              "audio_device": QMediaDevices.defaultAudioOutput().description(), "samples": [], "timings": [], "errors": []}
    cpu, first = [], []
    update = service._update_mouth
    def measured(played):
        before = time.thread_time_ns()
        update(played)
        cpu.append(time.thread_time_ns() - before)
    service._update_mouth = measured
    service.sentence_started.connect(lambda *_: first.append(time.perf_counter()))
    service.sentence_timing.connect(report["timings"].append)
    service.error.connect(lambda *error: report["errors"].append(error))
    def mouth(value):
        report["samples"].append({"wall": time.perf_counter(), "level": value,
            "device_seconds": service._sink.processedUSecs() / 1e6 if service._sink else None})
    service.mouth_level.connect(mouth)
    try:
        start = time.perf_counter()
        service.speak("这是显式生成的音频设备测试音。")
        spin(app, lambda: service.state in {"idle", "error"}, 10)
        assert not report["errors"]
        report["first_device_audio_seconds"] = first[0] - start
        report["meter_cpu_seconds"] = sum(cpu) / 1e9
        report["meter_cpu_percent_of_one_core"] = report["meter_cpu_seconds"] / audio.duration_seconds * 100
        values = [sample for sample in report["samples"] if sample["device_seconds"] is not None]
        intervals = [1000 * (b["wall"] - a["wall"]) for a, b in zip(values, values[1:])]
        report["frame_interval_ms"] = {"mean": statistics.mean(intervals), "max": max(intervals)}
        # Windows thread CPU clocks can quantize short calls to zero. Amplify
        # 5,000 identical 50 ms windows to obtain a measurable CPU budget.
        meter = PlaybackMeter(RATE)
        window = pcm[round(RATE * .3) * 2:round(RATE * .35) * 2]
        before_cpu, before_wall = time.thread_time(), time.perf_counter()
        for index in range(5000):
            meter.append(index * 1600, window)
            meter.sample((index + 1) * 1600)
        report["amplified_meter_5000_frames"] = {
            "cpu_seconds": time.thread_time() - before_cpu,
            "wall_seconds": time.perf_counter() - before_wall,
            "equivalent_audio_seconds": 250,
        }
        # Exclude one RMS window around section edges; compare against known PCM.
        begin = 0
        checks = []
        for duration, amplitude in SECTIONS:
            end = begin + duration
            selected = [sample["level"] for sample in values if begin + .065 < sample["device_seconds"] < end - .015]
            assert selected
            assert all(0 <= v <= 1 for v in selected)
            assert (max(selected) == 0) if amplitude == 0 else (min(selected) > 0)
            checks.append({"from": begin, "to": end, "input_amplitude": amplitude,
                           "min_level": min(selected), "max_level": max(selected)})
            begin = end
        report["section_checks"] = checks
        # Play again and stop in the first tone, while future PCM is queued.
        service.speak("这句用于中途停止。")
        spin(app, lambda: service._mouth_value > 0, 5)
        stop = time.perf_counter()
        service.stop()
        report["stop_call_seconds"] = time.perf_counter() - stop
        assert report["samples"][-1]["level"] == 0
        report["stop_zero_seconds"] = report["samples"][-1]["wall"] - stop
        count = len(report["samples"])
        until = time.perf_counter() + .25
        spin(app, lambda: time.perf_counter() >= until, 1)
        report["late_mouth_events"] = len(report["samples"]) - count
        assert report["late_mouth_events"] == 0
        assert report["timings"][-1]["state"] == "interrupted"
        report["status"] = "completed"
    except Exception as exc:
        report["status"] = "failed"
        report["errors"].append(str(exc))
    finally:
        service.close()
        service.deleteLater()
        app.processEvents()
        (output / "mouth-measurements.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    report = measure(args.output)
    print(json.dumps({k: v for k, v in report.items() if k not in {"samples", "timings"}}, ensure_ascii=False, indent=2))
    sys.exit(0 if report["status"] == "completed" else 1)
