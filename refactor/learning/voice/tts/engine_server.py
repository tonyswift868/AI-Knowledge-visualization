"""Python 3.9 child entrypoint; no imports from the client's Python environment.

Uses the installed TTS inference class directly: api_v2 exposes mutable weight
and restart endpoints, writes its config and flushes GPU caches after each
sentence. This narrow resident API fixes one selected MF voice, warms it before
ready, preserves allocations, and serves only authenticated loopback requests.
Engine code/model paths are read-only; writable state lives beside --config.
"""
import argparse
import base64
import gc
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import json
import math
import os
from pathlib import Path
from queue import Queue
import sys
import threading
import time
import traceback
import wave


def _readonly_guard(writable):
    """Defense against Python dependencies writing caches beside engine assets."""
    writable = os.path.normcase(os.path.realpath(writable))
    def allowed(path):
        if isinstance(path, int) or path is None:
            return
        value = os.fsdecode(path)
        if value.lower() in {"nul", os.devnull.lower()}:
            return
        resolved = os.path.normcase(os.path.realpath(value))
        try:
            inside = os.path.commonpath((writable, resolved)) == writable
        except ValueError:  # Different Windows drives.
            inside = False
        if not inside:
            raise PermissionError("TTS dependency attempted a write outside its runtime directory: " + value)
    def audit(event, args):
        if event == "open":
            path, mode, flags = args
            if (mode and any(c in mode for c in "wax+")) or flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
                allowed(path)
        elif event in {"os.mkdir", "os.remove", "os.rmdir", "os.chmod", "os.utime", "os.truncate"}:
            allowed(args[0])
        elif event in {"os.rename", "os.replace", "os.link", "os.symlink"}:
            allowed(args[0]); allowed(args[1])
        elif event == "socket.connect":
            address = args[1]
            if isinstance(address, tuple) and address[0] not in {"127.0.0.1", "::1"}:
                raise PermissionError("TTS inference is offline; automatic downloads are disabled")
    sys.addaudithook(audit)


def _load_source(name, path, substitutions):
    """Small in-memory compatibility shims, never edits the installed engine."""
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    source = Path(path).read_text(encoding="utf-8")
    for old, new in substitutions:
        if old not in source:
            raise RuntimeError("Unsupported engine layout: " + name)
        source = source.replace(old, new)
    sys.modules[name] = module
    exec(compile(source, str(path), "exec"), module.__dict__)
    return module


def _use_bundled_language_model():
    # This official package's LangSegmenter replaces the default detector and
    # asks for its large online model. Bind the already shipped, hashed model
    # after importing TTS, so every segmentation request remains offline.
    import fast_langdetect.infer as detector
    model = Path(detector.__file__).parent / "resources/lid.176.ftz"
    if not model.is_file():
        raise RuntimeError("Bundled language model is missing: " + str(model))
    detector._default_detector = detector.LangDetector(detector.LangDetectConfig(
        custom_model_path=str(model), allow_fallback=False))


def _make_pipeline(config):
    engine = Path(config["engine_root"])
    assets = config["base_assets"]
    for path in (engine, engine / "GPT_SoVITS", engine / "GPT_SoVITS/eres2net"):
        sys.path.insert(0, str(path))
    # Upstream relpath(__file__) cannot cross Windows drives (E: engine / D: cwd).
    _load_source("tools.i18n.i18n", engine / "tools/i18n/i18n.py",
                 [("os.path.dirname(os.path.relpath(__file__))", "os.path.dirname(__file__)")])
    print("Importing torch", flush=True)
    import torch
    torch.set_num_threads(config["cpu_threads"])
    torch.set_num_interop_threads(2)
    if config["device"] == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA unavailable; CPU fallback was not requested")
    import nltk
    print("Loading text preprocessing", flush=True)
    def offline_download(*args, **kwargs):
        raise RuntimeError("NLTK data missing from engine runtime; no download attempted")
    nltk.download = offline_download
    # Chinese G2PW has a hardcoded relative model directory; bind existing assets.
    import text.g2pw as g2pw
    original = g2pw.G2PWPinyin.__init__
    # G2PW is used sentence by sentence. Do not retain its largest temporary CPU
    # buffers for the entire time that the teacher voice is resident.
    adapted = _load_source("text.g2pw.onnx_api", engine / "GPT_SoVITS/text/g2pw/onnx_api.py", [
        ("sess_options = onnxruntime.SessionOptions()",
         "sess_options = onnxruntime.SessionOptions()\n        sess_options.enable_cpu_mem_arena = False\n        sess_options.enable_mem_pattern = False")])
    original.__globals__["G2PWOnnxConverter"] = adapted.G2PWOnnxConverter
    def initialize(self, *args, **kwargs):
        kwargs["model_dir"] = assets["g2pw_root"]
        return original(self, *args, **kwargs)
    g2pw.G2PWPinyin.__init__ = initialize
    # Even if the installed optional English dictionary cache is absent, writes
    # go into this run's cache. Source dictionaries remain read from the engine.
    _load_source("text.english", engine / "GPT_SoVITS/text/english.py", [
        ('os.path.join(current_file_path, "engdict_cache.pickle")', repr(str(Path.cwd() / "cache/engdict_cache.pickle"))),
        ('os.path.join(current_file_path, "namedict_cache.pickle")', repr(str(Path.cwd() / "cache/namedict_cache.pickle")))])
    import sv
    sv.sv_path = assets["sv_weights"]
    # Installed streaming code skips overlap removal for the final chunk even
    # though its existing slice expression handles that case. Apply the one-line
    # correction in memory, keeping the shared engine file untouched.
    _load_source("TTS_infer_pack.TTS", engine / "GPT_SoVITS/TTS_infer_pack/TTS.py", [
        ("elif not is_first_chunk and not is_final:", "elif not is_first_chunk:"),
        # The old route created FP32 BERT on CPU and GPU, then converted it to
        # the already-selected FP16. Load at the final precision from the start.
        ("AutoModelForMaskedLM.from_pretrained(base_path)",
         "AutoModelForMaskedLM.from_pretrained(base_path, torch_dtype=torch.float16 if self.configs.is_half else torch.float32)")])
    from TTS_infer_pack.TTS import TTS, TTS_Config
    _use_bundled_language_model()
    print("Loading resident voice weights", flush=True)
    options = {"device": config["device"], "is_half": config["device"] == "cuda", "version": "v2Pro",
        "t2s_weights_path": config["voice"]["gpt_weights"], "vits_weights_path": config["voice"]["sovits_weights"],
        "bert_base_path": assets["bert_root"], "cnhuhbert_base_path": assets["hubert_root"]}
    for key in ("bert_base_path", "cnhuhbert_base_path", "t2s_weights_path", "vits_weights_path"):
        if not Path(options[key]).exists():
            raise RuntimeError("Required local asset is missing: " + key)
    tts_config = TTS_Config({"custom": options})
    tts_config.configs_path = str(Path.cwd() / "engine-config.yaml")
    pipeline = TTS(tts_config)
    # Keep the allocator/reference features resident; release on process close.
    pipeline.empty_cache = lambda: None
    return pipeline, torch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    run_dir = Path(args.config).resolve().parent
    os.chdir(run_dir)
    sys.dont_write_bytecode = True
    _readonly_guard(str(run_dir))
    state = {"ready": False, "error": None, "pid": os.getpid(), "instance": config["instance"]}
    pipeline, torch = None, None
    lock = threading.Lock()
    active = {"request_id": None, "cancelled": False}
    jobs = Queue(maxsize=1)

    def synthesize_on_worker(text, emit=None, speed=1.0):
        voice = config["voice"]
        started = time.perf_counter()
        sent_at = started
        audio = []
        import numpy as np
        with torch.inference_mode():
            for rate, chunk in pipeline.run({"text": text, "text_lang": "zh", "prompt_lang": "zh",
                    "prompt_text": voice["reference_text"], "ref_audio_path": voice["reference_audio"],
                    "text_split_method": "cut0", "batch_size": 1, "parallel_infer": emit is None,
                    "streaming_mode": emit is not None, "fixed_length_chunk": True,
                    "min_chunk_length": 32, "overlap_length": 4,
                    "fragment_interval": 0.0, "speed_factor": voice["speed"] * speed, "seed": 42,
                    "top_k": 15, "top_p": 1.0, "temperature": 1.0, "repetition_penalty": 1.35}):
                if active["cancelled"]:
                    raise InterruptedError("cancelled")
                if not len(chunk):
                    continue
                if emit is not None:
                    chunk = chunk.astype(np.int16)
                    if not audio:
                        audible = np.flatnonzero(np.abs(chunk.astype(np.int32)) > 96)
                        if not audible.size:
                            continue
                        chunk = chunk[max(0, audible[0] - int(rate * .025)):]
                    now = time.perf_counter()
                    emit({"sample_rate": rate, "pcm": base64.b64encode(chunk.tobytes()).decode("ascii"),
                          "synthesis_seconds": now - sent_at, "final": False})
                    sent_at = now
                audio.append(chunk)
        if not audio:
            raise RuntimeError("Inference returned no audio")
        if emit is not None:
            emit({"sample_rate": rate, "pcm": "", "synthesis_seconds": time.perf_counter() - sent_at, "final": True})
            return None
        samples = np.concatenate(audio).astype(np.int16)
        # Retain a short natural margin, remove model-generated long boundary
        # silence so two queued sentences do not carry artificial 300 ms gaps.
        indices = np.flatnonzero(np.abs(samples.astype(np.int32)) > 96)
        if not indices.size:
            raise RuntimeError("Inference returned silence")
        pad = int(rate * .025)
        samples = samples[max(0, indices[0] - pad):min(len(samples), indices[-1] + pad + 1)]
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(rate); wav.writeframes(samples.tobytes())
        return output.getvalue(), time.perf_counter() - started, len(samples) / rate

    def inference_worker():
        while True:
            text, emit, speed, finished, result = jobs.get()
            try:
                result.append(synthesize_on_worker(text, emit, speed))
            except Exception as exc:
                result.append(exc)
            finally:
                finished.set()

    def synthesize(text, emit=None, speed=1.0):
        # Warm-up and every sentence use the same CUDA thread/context. HTTP
        # request threads remain free to accept health and cancellation calls.
        finished, result = threading.Event(), []
        jobs.put((text, emit, speed, finished, result))
        finished.wait()
        if isinstance(result[0], Exception):
            raise result[0]
        return result[0]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, status, data, media="application/json", extra=None):
            if not isinstance(data, bytes):
                data = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", media)
            self.send_header("Content-Length", str(len(data)))
            for key, value in (extra or {}).items():
                self.send_header(key, str(value))
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def authorized(self):
            import hmac
            return hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + config["token"])

        def do_GET(self):
            if not self.authorized():
                self.send(403, {"code": "UNAUTHORIZED"}); return
            if self.path != "/health":
                self.send(404, {}); return
            metrics = dict(state)
            if torch is not None and config["device"] == "cuda":
                metrics.update(cuda_allocated_bytes=torch.cuda.memory_allocated(), cuda_reserved_bytes=torch.cuda.memory_reserved(),
                               cuda_peak_bytes=torch.cuda.max_memory_allocated())
            self.send(200, metrics)

        def do_POST(self):
            if not self.authorized():
                self.send(403, {"code": "UNAUTHORIZED"}); return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 16000:
                    raise ValueError("size")
                body = json.loads(self.rfile.read(size))
            except (ValueError, TypeError):
                self.send(400, {"code": "INVALID_REQUEST"}); return
            if self.path == "/cancel":
                if active["request_id"] == body.get("request_id"):
                    active["cancelled"] = True
                    if pipeline:
                        pipeline.stop()
                self.send(200, {}); return
            if self.path not in {"/synthesize", "/stream"} or not state["ready"]:
                self.send(503, {"code": "NOT_READY"}); return
            text = body.get("text", "")
            if not isinstance(text, str) or not text.strip() or len(text) > 120:
                self.send(400, {"code": "INVALID_TEXT"}); return
            speed = body.get("speed", 1.0)
            if (isinstance(speed, bool) or not isinstance(speed, (int, float))
                    or not math.isfinite(speed) or not .5 <= speed <= 2):
                self.send(400, {"code": "INVALID_SPEED"}); return
            if not lock.acquire(blocking=False):
                self.send(409, {"code": "BUSY"}); return
            streaming = self.path == "/stream"
            def emit(frame):
                try:
                    self.wfile.write(json.dumps(frame).encode("utf-8") + b"\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    raise InterruptedError("client disconnected") from None
            try:
                active.update(request_id=body.get("request_id"), cancelled=False)
                if streaming:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/x-ndjson")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    synthesize(text, emit, speed)
                else:
                    wav, elapsed, duration = synthesize(text, speed=speed)
                    self.send(200, wav, "audio/wav", {"X-Synthesis-Seconds": elapsed, "X-Audio-Seconds": duration})
            except InterruptedError:
                if not streaming:
                    self.send(409, {"code": "CANCELLED"})
            except Exception:
                traceback.print_exc()
                if streaming:
                    try:
                        emit({"error": "SYNTHESIS_FAILED"})
                    except InterruptedError:
                        pass
                else:
                    self.send(500, {"code": "SYNTHESIS_FAILED"})
            finally:
                active.update(request_id=None, cancelled=False)
                lock.release()

    def parent_watch():
        # A normal close and a crashed/killed parent both close this private pipe.
        sys.stdin.buffer.read()
        os._exit(0)
    try:
        import faulthandler
        faulthandler.dump_traceback_later(45, repeat=True)
        started = time.perf_counter()
        pipeline, torch = _make_pipeline(config)
        threading.Thread(target=inference_worker, name="mf-tts-inference", daemon=True).start()
        print("Warming voice", flush=True)
        synthesize("我们开始学习吧。")
        synthesize("我们开始学习吧。", lambda frame: None)
        state.update(ready=True, cold_seconds=time.perf_counter() - started)
        faulthandler.cancel_dump_traceback_later()
        # Initialize native numerical DLLs before starting Python threads:
        # Windows OpenBLAS can deadlock in DllMain when another thread starts
        # while numpy/torch is being imported.
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        (run_dir / "endpoint.json").write_text(json.dumps({"port": server.server_port, **state}), encoding="utf-8")
        threading.Thread(target=server.serve_forever, daemon=True).start()
        threading.Thread(target=parent_watch, daemon=True).start()
        gc.collect()
        while True:
            time.sleep(1)
    except Exception as exc:
        traceback.print_exc()
        state.update(error=type(exc).__name__)
        time.sleep(2)
        return 1


if __name__ == "__main__":
    sys.exit(main())
