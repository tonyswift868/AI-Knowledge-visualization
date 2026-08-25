"""Private persistent subprocess entry point for local Docling conversion."""

from __future__ import annotations

import argparse
import hashlib
import json
import locale
import platform
import socket
import sys
from pathlib import Path
from typing import Any, BinaryIO

from .contracts import DoclingCompileMode
from .pool import (
    DOCLING_WORKER_FRAME_PREFIX,
    DOCLING_WORKER_PROTOCOL,
    HERON_MODEL_FILENAME,
    HERON_REPOSITORY_FOLDER,
)


class _OfflineGuards:
    def __init__(self) -> None:
        self.network_attempts = 0
        self.model_download_attempts = 0

    def install(self) -> None:
        # This module runs only in a child process. These guards die with that
        # worker and never mutate the long-lived application process.
        guard = self
        original_socket = socket.socket

        class OfflineSocket(original_socket):
            def connect(self, address: Any) -> None:
                guard.network_attempts += 1
                raise PermissionError("Network access is disabled in Docling worker")

            def connect_ex(self, address: Any) -> int:
                guard.network_attempts += 1
                raise PermissionError("Network access is disabled in Docling worker")

        def blocked_create_connection(*args: Any, **kwargs: Any) -> None:
            guard.network_attempts += 1
            raise PermissionError("Network access is disabled in Docling worker")

        socket.socket = OfflineSocket
        socket.create_connection = blocked_create_connection

        from docling.models.utils.hf_model_download import (
            HuggingFaceModelDownloadMixin,
        )

        def blocked_download(*args: Any, **kwargs: Any) -> Path:
            guard.model_download_attempts += 1
            raise PermissionError("Model downloads are disabled in Docling worker")

        HuggingFaceModelDownloadMixin.download_models = staticmethod(blocked_download)


def _configure_compile_mode(converter: Any, *, disabled: bool) -> DoclingCompileMode:
    if not disabled:
        return DoclingCompileMode.RUNTIME_DEFAULT

    from docling.datamodel.base_models import InputFormat

    if InputFormat.PDF not in converter.format_to_options:
        return DoclingCompileMode.NOT_APPLICABLE
    pipeline_options = converter.format_to_options[InputFormat.PDF].pipeline_options
    layout_options = pipeline_options.layout_options
    engine_options = layout_options.engine_options
    pipeline_options.layout_options = layout_options.model_copy(
        update={
            "engine_options": engine_options.model_copy(update={"compile_model": False})
        }
    )
    if pipeline_options.layout_options.engine_options.compile_model:
        raise RuntimeError("Docling Heron compile mode remained enabled")
    return DoclingCompileMode.DISABLED_UNCOMPILED_HERON


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class _DoclingWorkerRuntime:
    def __init__(self, config: dict[str, Any], guards: _OfflineGuards) -> None:
        from ..contracts import DocumentSourcePolicy
        from ..source import build_docling_converter

        if config.get("protocol") != DOCLING_WORKER_PROTOCOL:
            raise ValueError("Docling worker protocol mismatch")
        policy_payload = config["policy"]
        artifacts = policy_payload.get("pdf_artifacts_path")
        self.policy = DocumentSourcePolicy(
            input_root=Path(policy_payload["input_root"]),
            pdf_artifacts_path=Path(artifacts) if artifacts else None,
            pdf_table_structure_enabled=bool(
                policy_payload.get("pdf_table_structure_enabled", False)
            ),
        )
        self.worker_id = str(config["worker_id"])
        self.guards = guards
        self.converter = build_docling_converter(self.policy)
        self.compile_mode = _configure_compile_mode(
            self.converter,
            disabled=bool(config.get("disable_heron_torch_compile", True)),
        )

    def startup_response(self) -> dict[str, Any]:
        artifact = self._artifact_path()
        return {
            "protocol": DOCLING_WORKER_PROTOCOL,
            "event": "STARTUP",
            "status": "READY",
            "worker_id": self.worker_id,
            "platform": platform.platform(),
            "utf8_mode": bool(sys.flags.utf8_mode),
            "default_encoding": locale.getpreferredencoding(False),
            "compile_mode": self.compile_mode.value,
            "artifact_available": artifact is not None,
            "artifact_sha256": _hash_file(artifact) if artifact is not None else None,
            "network_attempts": self.guards.network_attempts,
            "model_download_attempts": self.guards.model_download_attempts,
        }

    def convert(self, request: dict[str, Any]) -> dict[str, Any]:
        job_id = str(request.get("job_id") or "")
        try:
            sources = tuple(
                Path(value).resolve(strict=False) for value in request["sources"]
            )
            input_root = self.policy.input_root.resolve(strict=False)
            for source in sources:
                source.relative_to(input_root)
            conversion_kwargs = dict(request.get("conversion_kwargs") or {})
            if isinstance(conversion_kwargs.get("page_range"), list):
                conversion_kwargs["page_range"] = tuple(
                    conversion_kwargs["page_range"]
                )
            results = [
                self._serialize_result(result)
                for result in self.converter.convert_all(
                    sources,
                    **conversion_kwargs,
                )
            ]
            return {
                "protocol": DOCLING_WORKER_PROTOCOL,
                "event": "CONVERSION",
                "status": "SUCCESS",
                "worker_id": self.worker_id,
                "job_id": job_id,
                "network_attempts": self.guards.network_attempts,
                "model_download_attempts": self.guards.model_download_attempts,
                "results": results,
            }
        except Exception as error:
            return {
                "protocol": DOCLING_WORKER_PROTOCOL,
                "event": "CONVERSION",
                "status": "FAILED",
                "worker_id": self.worker_id,
                "job_id": job_id,
                "network_attempts": self.guards.network_attempts,
                "model_download_attempts": self.guards.model_download_attempts,
                "error_class": type(error).__name__,
                "error_code": "DOCLING_WORKER_CONVERSION_FAILED",
                "results": [],
            }

    @staticmethod
    def _serialize_result(result: Any) -> dict[str, Any]:
        document = getattr(result, "document", None)
        return {
            "status": str(getattr(getattr(result, "status", None), "value", "")),
            "document": (
                document.model_dump(mode="json", by_alias=True)
                if document is not None
                else None
            ),
            "errors": [
                {
                    "error_class": type(error).__name__,
                    "error_code": str(
                        getattr(error, "error_code", "DOCLING_CONVERSION_FAILED")
                    ),
                    "retryable": bool(getattr(error, "retryable", False)),
                }
                for error in (getattr(result, "errors", ()) or ())
            ],
        }

    def _artifact_path(self) -> Path | None:
        artifacts = self.policy.pdf_artifacts_path
        if artifacts is None:
            return None
        path = artifacts / HERON_REPOSITORY_FOLDER / HERON_MODEL_FILENAME
        return path if path.is_file() else None


def _write_frame(stream: BinaryIO, payload: dict[str, Any]) -> None:
    data = DOCLING_WORKER_FRAME_PREFIX + json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    stream.write(data)
    stream.flush()


def _serve(config_path: Path) -> int:
    guards = _OfflineGuards()
    guards.install()
    output = sys.stdout.buffer
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        runtime = _DoclingWorkerRuntime(config, guards)
        _write_frame(output, runtime.startup_response())
    except Exception as error:
        _write_frame(
            output,
            {
                "protocol": DOCLING_WORKER_PROTOCOL,
                "event": "STARTUP",
                "status": "FAILED",
                "worker_id": (
                    str(config.get("worker_id")) if "config" in locals() else "unknown"
                ),
                "utf8_mode": bool(sys.flags.utf8_mode),
                "default_encoding": locale.getpreferredencoding(False),
                "compile_mode": DoclingCompileMode.RUNTIME_DEFAULT.value,
                "network_attempts": guards.network_attempts,
                "model_download_attempts": guards.model_download_attempts,
                "error_class": type(error).__name__,
                "error_code": "DOCLING_WORKER_STARTUP_FAILED",
            },
        )
        return 1

    for line in iter(sys.stdin.buffer.readline, b""):
        if not line.startswith(DOCLING_WORKER_FRAME_PREFIX):
            continue
        try:
            request = json.loads(
                line[len(DOCLING_WORKER_FRAME_PREFIX) :].decode("utf-8")
            )
            if request.get("protocol") != DOCLING_WORKER_PROTOCOL:
                raise ValueError("Docling worker protocol mismatch")
            event = request.get("event")
            if event == "CONVERT":
                _write_frame(output, runtime.convert(request))
                continue
            if event == "SHUTDOWN":
                _write_frame(
                    output,
                    {
                        "protocol": DOCLING_WORKER_PROTOCOL,
                        "event": "SHUTDOWN_ACK",
                        "status": "SUCCESS",
                        "worker_id": runtime.worker_id,
                        "network_attempts": guards.network_attempts,
                        "model_download_attempts": guards.model_download_attempts,
                    },
                )
                return 0
            raise ValueError("unsupported Docling worker event")
        except Exception as error:
            _write_frame(
                output,
                {
                    "protocol": DOCLING_WORKER_PROTOCOL,
                    "event": "CONVERSION",
                    "status": "FAILED",
                    "worker_id": runtime.worker_id,
                    "job_id": (
                        str(request.get("job_id") or "")
                        if "request" in locals()
                        else ""
                    ),
                    "network_attempts": guards.network_attempts,
                    "model_download_attempts": guards.model_download_attempts,
                    "error_class": type(error).__name__,
                    "error_code": "DOCLING_WORKER_PROTOCOL_FAILED",
                    "results": [],
                },
            )
    return 1


def _run_legacy(request: dict[str, Any], guards: _OfflineGuards) -> dict[str, Any]:
    config = {
        "protocol": request.get("protocol"),
        "worker_id": "legacy-one-shot",
        "policy": request["policy"],
        "disable_heron_torch_compile": request.get(
            "disable_heron_torch_compile",
            True,
        ),
    }
    runtime = _DoclingWorkerRuntime(config, guards)
    response = runtime.convert(
        {
            "protocol": DOCLING_WORKER_PROTOCOL,
            "event": "CONVERT",
            "job_id": "legacy-one-shot",
            "sources": request["sources"],
            "conversion_kwargs": request.get("conversion_kwargs") or {},
        }
    )
    startup = runtime.startup_response()
    response.update(
        {
            "utf8_mode": startup["utf8_mode"],
            "default_encoding": startup["default_encoding"],
            "compile_mode": startup["compile_mode"],
            "artifact_available": startup["artifact_available"],
            "artifact_sha256": startup["artifact_sha256"],
        }
    )
    return response


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--request", type=Path)
    parser.add_argument("--response", type=Path)
    args = parser.parse_args()
    if args.serve:
        if args.config is None:
            parser.error("--serve requires --config")
        return _serve(args.config)
    if args.request is None or args.response is None:
        parser.error("legacy mode requires --request and --response")
    guards = _OfflineGuards()
    guards.install()
    try:
        request = json.loads(args.request.read_text(encoding="utf-8"))
        response = _run_legacy(request, guards)
        exit_code = 0 if response.get("status") == "SUCCESS" else 1
    except Exception as error:
        response = {
            "protocol": DOCLING_WORKER_PROTOCOL,
            "status": "FAILED",
            "utf8_mode": bool(sys.flags.utf8_mode),
            "default_encoding": locale.getpreferredencoding(False),
            "compile_mode": DoclingCompileMode.RUNTIME_DEFAULT.value,
            "network_attempts": guards.network_attempts,
            "model_download_attempts": guards.model_download_attempts,
            "error_class": type(error).__name__,
            "error_code": "DOCLING_WORKER_UNHANDLED_EXCEPTION",
            "results": [],
        }
        exit_code = 1
    args.response.write_text(
        json.dumps(response, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
