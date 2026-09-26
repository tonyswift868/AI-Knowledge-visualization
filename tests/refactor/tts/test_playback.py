from array import array
import math

import pytest

from refactor.learning.voice.tts.playback import PlaybackMeter
from refactor.learning.voice.tts.runtime import SpeechAudio
from refactor.learning.voice.tts.service import TtsService
from test_service import FakeRuntime, FakeSink, spin


def tone(frames, amplitude=.2, rate=32000):
    return array("h", (int(32767 * amplitude * math.sin(2 * math.pi * 200 * i / rate))
                       for i in range(frames))).tobytes()


def test_meter_consumed_window_smooth_attack_release_silence_and_bounded_storage():
    meter = PlaybackMeter(32000)
    for i, pcm in enumerate((tone(1600), tone(1600), tone(1600, .03), b"\0\0" * 1600)):
        meter.append(i * 1600, pcm)
    assert meter.sample(0) == 0  # Future synthesized PCM is not currently audible.
    first = meter.sample(1600)
    second = meter.sample(3200)
    third = meter.sample(4800)
    assert 0 < first < second < 1 and 0 < third < second
    assert meter.sample(6400) == 0 and len(meter.chunks) == 1
    assert meter.sample(6400) == 0


def test_meter_spanning_chunk_boundaries_and_stalled_device():
    pcm = tone(3200)
    split, whole = PlaybackMeter(32000), PlaybackMeter(32000)
    split.append(0, pcm[:666])
    split.append(333, pcm[666:])
    whole.append(0, pcm)
    assert split.sample(1600) == pytest.approx(whole.sample(1600))
    assert split.sample(1600) == 0  # Underflow: clock has not consumed more samples.
    assert split.sample(3200) > 0


def test_mouth_waits_for_device_position_and_stop_is_synchronous(qt_app):
    class ToneRuntime(FakeRuntime):
        def synthesize(self, text, cancel=None):
            return SpeechAudio(tone(3200) + b"\0\0" * 3200, 32000, .01)
    service = TtsService(runtime=ToneRuntime(), sink_factory=FakeSink)
    levels, timings = [], []
    service.mouth_level.connect(levels.append)
    service.sentence_timing.connect(timings.append)
    try:
        service.speak("测量声音。下一句话。")
        spin(qt_app, lambda: len(service.last_metrics) == 2)
        service._mouth_due = 0
        service._pump()
        assert levels == [0] and not timings
        sink = service._sink
        sink.played = 3200  # 50 ms consumed, not the two synthesized sentences.
        service._mouth_due = 0
        service._pump()
        assert levels[-1] > 0 and timings[-1]["state"] == "started"
        assert timings[-1]["start_seconds"] == 0 and timings[-1]["end_seconds"] is None
        token = timings[-1]["utterance_id"]
        service.stop()
        assert levels[-1] == 0 and sink.reset_count == 1
        assert timings[-1] == {"utterance_id": token, "index": 0, "text": "测量声音。",
                               "state": "interrupted", "start_seconds": 0, "end_seconds": .05}
        count = len(levels), len(timings)
        service._pump()
        assert (len(levels), len(timings)) == count
    finally:
        service.close()


def test_silence_and_ordered_sentence_edges_follow_consumption(qt_app):
    class ToneRuntime(FakeRuntime):
        def synthesize(self, text, cancel=None):
            return SpeechAudio(tone(1600) + b"\0\0" * 1600, 32000, .01)
    service = TtsService(runtime=ToneRuntime(), sink_factory=FakeSink)
    levels, timings = [], []
    service.mouth_level.connect(levels.append)
    service.sentence_timing.connect(timings.append)
    try:
        service.speak("第一句。第二句。")
        spin(qt_app, lambda: len(service.last_metrics) == 2)
        sink = service._sink
        sink.played = 3200
        service._mouth_due = 0
        service._pump()
        assert levels[-1] > 0
        sink.played = 6400
        service._mouth_due = 0
        service._pump()
        assert levels[-1] == 0
        assert [(x["index"], x["state"]) for x in timings] == [(0, "started"), (0, "finished")]
        sink.played = 12800
        service._mouth_due = 0
        spin(qt_app, lambda: service.state == "idle")
        assert [(x["index"], x["state"]) for x in timings] == [
            (0, "started"), (0, "finished"), (1, "started"), (1, "finished")]
        assert [x["end_seconds"] for x in timings if x["state"] == "finished"] == [.1, .2]
        assert levels[-1] == 0
    finally:
        service.close()


@pytest.mark.parametrize("signal", ["mouth_level", "sentence_timing"])
def test_stop_from_new_signals_drops_old_callbacks_and_can_speak_again(qt_app, signal):
    class ToneRuntime(FakeRuntime):
        def synthesize(self, text, cancel=None):
            return SpeechAudio(tone(3200), 32000, .01)
    service = TtsService(runtime=ToneRuntime(), sink_factory=FakeSink)
    timings = []
    service.sentence_timing.connect(timings.append)
    def interrupt(value):
        if signal == "mouth_level" and value > 0 or signal == "sentence_timing" and value["state"] == "started":
            service.stop()
    getattr(service, signal).connect(interrupt)
    try:
        service.speak("旧的一句。不能播放的下一句。")
        spin(qt_app, lambda: len(service.last_metrics) == 2)
        service._sink.played = 3200
        service._mouth_due = 0
        service._pump()
        assert service.state == "stopped" and service._mouth_value == 0
        assert [x["state"] for x in timings] == ["started", "interrupted"]
        getattr(service, signal).disconnect(interrupt)
        service.speak("新的一句。")
        spin(qt_app, lambda: len(service.last_metrics) == 1)
        service._sink.played = 6400
        spin(qt_app, lambda: service.state == "idle")
        assert timings[-1]["utterance_id"] != timings[0]["utterance_id"]
        assert timings[-1]["state"] == "finished"
    finally:
        service.close()
