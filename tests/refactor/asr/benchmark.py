"""Explicit local benchmark; microphone capture is opt-in via --microphone.

Run with the repository on PYTHONPATH. Results print as JSON; audio is not saved.
The microphone latency is estimated from the first VAD speech sample and the
arrival time of the first captured PCM block, not from the click on Start.
"""
import argparse
import json
from pathlib import Path
import time

import numpy as np
from PySide6.QtCore import QCoreApplication, QTimer

from refactor.learning.voice.asr.engine import AsrEngine
from refactor.learning.voice.asr.model_manager import ModelManager
from refactor.learning.voice.asr.recorder import AudioRecorder
from refactor.learning.voice.asr.service import AsrService
from test_real_asr import FileRecorder, read_pcm


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--microphone", action="store_true")
    parser.add_argument("--seconds", type=float, default=12)
    args = parser.parse_args()
    app = QCoreApplication.instance() or QCoreApplication([])
    manager = ModelManager(args.data_root)
    if not manager.status()["installed"]:
        raise SystemExit("Test model missing; this benchmark will not download.")
    metrics = {"mode": "microphone" if args.microphone else "paced_local_TTS"}
    if not args.microphone:
        engine = AsrEngine(manager.path)
        engine.feed(read_pcm())
        metrics["five_second_text"] = engine.finish().result(timeout=30)
        engine.wait_closed()
        metrics["model_load_seconds"] = engine.load_seconds
        metrics["five_second_decode_seconds"] = sum(engine.decode_seconds)
    state = {"engine": None, "recorder": None, "pcm_start": None, "samples": 0, "peak": 0.0}
    partials, finals, errors = [], [], []
    started = time.monotonic()

    def observe(pcm):
        if state["pcm_start"] is None:
            state["pcm_start"] = time.monotonic() - len(pcm) / 32000
        samples = np.frombuffer(pcm, dtype="<i2").astype(float) / 32768
        state["samples"] += len(samples)
        state["peak"] = max(state["peak"], float(np.max(np.abs(samples), initial=0)))

    def recorder_factory(parent):
        recorder = AudioRecorder(parent) if args.microphone else FileRecorder(parent)
        recorder.audio_ready.connect(observe)
        state["recorder"] = recorder
        return recorder

    def engine_factory(path):
        state["engine"] = AsrEngine(path)
        return state["engine"]

    service = AsrService(args.data_root, model_manager=manager, recorder_factory=recorder_factory, engine_factory=engine_factory)
    def partial(text):
        if text:
            partials.append((time.monotonic(), text))
            if args.microphone:
                print("PARTIAL: " + text, flush=True)
    service.partial_text.connect(partial)
    service.final_text.connect(finals.append)
    service.error.connect(lambda *error: errors.append(error))
    try:
        service.start()
        if args.microphone and service.state == "recording":
            print("MIC_RECORDING_STARTED: 请说：今天我们学习物理，牛顿第一定律。", flush=True)
            QTimer.singleShot(int(args.seconds * 1000), service.stop)
        deadline = time.monotonic() + args.seconds + 20
        while not finals and not errors and time.monotonic() < deadline:
            app.processEvents()
            if not args.microphone and state["recorder"].offset >= len(state["recorder"].raw):
                service.stop()
            time.sleep(.005)
        engine = state["engine"]
        metrics.update({"captured_seconds": state["samples"] / 16000, "peak": state["peak"],
                        "partial_updates": len(partials), "partials": [text for _, text in partials],
                        "final": finals, "errors": errors,
                        "model_load_seconds": engine.load_seconds if engine else None})
        if partials:
            metrics["first_text_from_start_seconds"] = partials[0][0] - started
            if engine.first_speech_sample is not None and state["pcm_start"] is not None:
                onset = state["pcm_start"] + engine.first_speech_sample / 16000
                metrics["first_text_from_vad_speech_seconds"] = partials[0][0] - onset
        print(json.dumps(metrics, ensure_ascii=False, indent=2), flush=True)
    finally:
        service.dispose()
        if state["engine"]:
            state["engine"].wait_closed()


if __name__ == "__main__":
    main()
