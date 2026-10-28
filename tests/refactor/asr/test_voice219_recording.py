from concurrent.futures import CancelledError
from threading import Event
import sys

import numpy as np
import pytest

from refactor.learning.voice.asr import engine as engine_module
from refactor.learning.voice.asr.engine import AsrEngine, SAMPLE_RATE
from refactor.learning.voice.asr.errors import AsrError


class Backend219:
    window_size = 320
    first_speech_sample = 0

    def __init__(self, *_):
        self.speech_ranges = [(0, 2 * SAMPLE_RATE), (21 * SAMPLE_RATE, 23 * SAMPLE_RATE),
                              (39 * SAMPLE_RATE, 41 * SAMPLE_RATE)]
        self.decoded_sizes = []

    def accept(self, samples):
        return []

    def flush(self):
        return []

    def partial_audio(self):
        return np.empty(0, dtype=np.float32)

    def decode(self, samples):
        self.decoded_sizes.append(len(samples))
        return "整段识别"


def test_voice219_consumed_pcm_does_not_accumulate_beyond_queue_limit(tmp_path):
    engine = AsrEngine(tmp_path, backend_factory=lambda *_: Backend219())
    try:
        assert engine.ready.wait(2)
        # Let the worker consume 55 seconds with an empty queue between feeds.
        # Inspect the retained recording, not the queue: BUG-169 lived there.
        for seconds in (15, 15, 15, 10):
            engine.feed(bytes(seconds * SAMPLE_RATE * 2))
            with engine._condition:
                assert engine._condition.wait_for(lambda: engine._buffered == 0, timeout=2)
        with engine._condition:
            frame = sys._current_frames()[engine.worker_thread_id]
            while frame.f_code.co_name != '_run':
                frame = frame.f_back
                assert frame is not None
            retained = frame.f_locals['recording']
            pcm_bytes = sum(len(block) for block in retained) if isinstance(retained, list) else 0
            del frame, retained
            assert pcm_bytes <= engine_module.MAX_BUFFERED_BYTES, (
                f'consumed PCM retained in RAM: {pcm_bytes} > {engine_module.MAX_BUFFERED_BYTES}')
    finally:
        engine.cancel()
        assert engine.wait_closed()


def test_voice219_final_pass_reads_bounded_complete_chunks_and_closes_spool(tmp_path, monkeypatch):
    backend = Backend219()
    monkeypatch.setattr(
        engine_module, "_whole_chunks",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("full recording materialized")),
    )
    original_temporary_file = engine_module.tempfile.TemporaryFile
    opened = []

    def track_temporary_file(*args, **kwargs):
        raw = original_temporary_file(*args, **kwargs)

        class RecordingSpy:
            def __init__(self):
                self.read_sizes = []

            def __getattr__(self, name):
                return getattr(raw, name)

            @property
            def closed(self):
                return raw.closed

            def read(self, size=-1):
                self.read_sizes.append(size)
                return raw.read(size)

        file = RecordingSpy()
        opened.append(file)
        return file

    monkeypatch.setattr(engine_module.tempfile, "TemporaryFile", track_temporary_file)
    engine = AsrEngine(tmp_path, backend_factory=lambda *_: backend, partial_interval=.05)
    assert engine.ready.wait(2)
    # 55 seconds exceeds the 25-second decode block, but no feed exceeds the
    # existing 30-second pending-queue limit.
    pcm = np.full(55 * SAMPLE_RATE, 1000, dtype="<i2").tobytes()
    for offset in range(0, len(pcm), 15 * SAMPLE_RATE * 2):
        engine.feed(pcm[offset:offset + 15 * SAMPLE_RATE * 2])
        assert engine._condition.acquire(timeout=2)
        try:
            assert engine._condition.wait_for(lambda: engine._buffered == 0, timeout=2)
        finally:
            engine._condition.release()
    assert engine.finish().result(5) == "整段识别 整段识别 整段识别"
    assert engine.wait_closed()
    assert len(backend.decoded_sizes) == 3
    assert max(backend.decoded_sizes) <= 25 * SAMPLE_RATE
    assert sum(backend.decoded_sizes) == 55 * SAMPLE_RATE
    assert engine.whole_outcome == "whole"
    assert len(opened) == 1 and opened[0].closed
    assert max(opened[0].read_sizes) <= 25 * SAMPLE_RATE * 2


def test_voice219_recording_write_error_closes_spool(tmp_path, monkeypatch):
    original_temporary_file = engine_module.tempfile.TemporaryFile
    opened = []

    def failing_temporary_file(*args, **kwargs):
        raw = original_temporary_file(*args, **kwargs)

        class FailedWriteSpool:
            def __getattr__(self, name):
                return getattr(raw, name)

            @property
            def closed(self):
                return raw.closed

            def write(self, _data):
                raise OSError("synthetic spool failure")

        spool = FailedWriteSpool()
        opened.append(spool)
        return spool

    monkeypatch.setattr(engine_module.tempfile, "TemporaryFile", failing_temporary_file)
    engine = AsrEngine(tmp_path, backend_factory=lambda *_: Backend219(), partial_interval=.05)
    assert engine.ready.wait(2)
    engine.feed(np.ones(SAMPLE_RATE, dtype="<i2").tobytes())
    future = engine.finish()
    with pytest.raises(AsrError) as failure:
        future.result(3)
    assert failure.value.code == "ASR_ENGINE_FAILED"
    assert engine.wait_closed() and len(opened) == 1 and opened[0].closed


def test_voice219_cancel_closes_spool_without_late_chunk_reads(tmp_path, monkeypatch):
    entered, release, decoded = Event(), Event(), Event()
    backend = Backend219()
    calls = []

    def slow_decode(samples):
        calls.append(len(samples))
        entered.set()
        release.wait(2)
        decoded.set()
        return "迟到识别"

    backend.decode = slow_decode
    original_temporary_file = engine_module.tempfile.TemporaryFile
    opened = []

    def track_temporary_file(*args, **kwargs):
        file = original_temporary_file(*args, **kwargs)
        opened.append(file)
        return file

    monkeypatch.setattr(engine_module.tempfile, "TemporaryFile", track_temporary_file)
    engine = AsrEngine(tmp_path, backend_factory=lambda *_: backend, partial_interval=.05)
    assert engine.ready.wait(2)
    pcm = np.full(55 * SAMPLE_RATE, 1000, dtype="<i2").tobytes()
    for offset in range(0, len(pcm), 15 * SAMPLE_RATE * 2):
        engine.feed(pcm[offset:offset + 15 * SAMPLE_RATE * 2])
        with engine._condition:
            assert engine._condition.wait_for(lambda: engine._buffered == 0, timeout=2)
    future = engine.finish()
    try:
        assert entered.wait(3)
        engine.cancel()
        with pytest.raises(CancelledError):
            future.result(2)
        assert engine.wait_closed()
        assert opened[0].closed
    finally:
        release.set()
        engine.cancel()
    assert engine.wait_closed()
    assert decoded.wait(2)
    assert calls == [engine_module.SAMPLE_RATE * 11 + engine_module.SAMPLE_RATE // 2]


def test_voice219_chunk_ranges_cover_recording_without_large_arrays():
    ranges = engine_module._whole_chunk_ranges(
        55 * SAMPLE_RATE,
        [(0, 2 * SAMPLE_RATE), (21 * SAMPLE_RATE, 23 * SAMPLE_RATE),
         (39 * SAMPLE_RATE, 41 * SAMPLE_RATE)],
    )
    assert [round((end - start) / SAMPLE_RATE, 2) for start, end in ranges] == [11.5, 19.5, 24.0]
    assert ranges[0][0] == 0 and ranges[-1][1] == 55 * SAMPLE_RATE
    assert all(left[1] == right[0] for left, right in zip(ranges, ranges[1:]))
