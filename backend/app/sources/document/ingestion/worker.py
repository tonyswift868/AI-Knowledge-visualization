"""Private subprocess entry point for Windows Docling PDF conversion."""

from __future__ import annotations

import argparse
import json
import locale
import socket
import sys
from pathlib import Path
from typing import Any

from .contracts import DoclingCompileMode
from .execution import DOCLING_WORKER_PROTOCOL


class _OfflineGuards:
    def __init__(self) -> None:
        self.network_attempts = 0
        self.model_download_attempts = 0

    def install(self) -> None:
        # This module runs only in a one-shot child process. These guards die
        # with that worker and never mutate the long-lived application process.
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


def _run(request: dict[str, Any], guards: _OfflineGuards) -> dict[str, Any]:
    from ..contracts import DocumentSourcePolicy
    from ..source import build_docling_converter

    if request.get("protocol") != DOCLING_WORKER_PROTOCOL:
        raise ValueError("Docling worker protocol mismatch")
    policy_payload = request["policy"]
    artifacts = policy_payload.get("pdf_artifacts_path")
    policy = DocumentSourcePolicy(
        input_root=Path(policy_payload["input_root"]),
        pdf_artifacts_path=Path(artifacts) if artifacts else None,
        pdf_table_structure_enabled=bool(
            policy_payload.get("pdf_table_structure_enabled", False)
        ),
    )
    converter = build_docling_converter(policy)
    compile_mode = _configure_compile_mode(
        converter,
        disabled=bool(request.get("disable_heron_torch_compile", True)),
    )
    conversion_kwargs = dict(request.get("conversion_kwargs") or {})
    if isinstance(conversion_kwargs.get("page_range"), list):
        conversion_kwargs["page_range"] = tuple(conversion_kwargs["page_range"])

    results: list[dict[str, Any]] = []
    for result in converter.convert_all(
        [Path(value) for value in request["sources"]],
        **conversion_kwargs,
    ):
        document = getattr(result, "document", None)
        results.append(
            {
                "status": str(getattr(getattr(result, "status", None), "value", "")),
                "document": (
                    document.model_dump(mode="json", by_alias=True)
                    if document is not None
                    else None
                ),
                "errors": [
                    {"error_class": type(error).__name__}
                    for error in (getattr(result, "errors", ()) or ())
                ],
            }
        )
    return {
        "protocol": DOCLING_WORKER_PROTOCOL,
        "status": "SUCCESS",
        "utf8_mode": bool(sys.flags.utf8_mode),
        "default_encoding": locale.getpreferredencoding(False),
        "compile_mode": compile_mode.value,
        "network_attempts": guards.network_attempts,
        "model_download_attempts": guards.model_download_attempts,
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--response", type=Path, required=True)
    args = parser.parse_args()
    guards = _OfflineGuards()
    guards.install()
    try:
        request = json.loads(args.request.read_text(encoding="utf-8"))
        response = _run(request, guards)
        exit_code = 0
    except Exception as error:
        response = {
            "protocol": DOCLING_WORKER_PROTOCOL,
            "status": "FAILED",
            "utf8_mode": bool(sys.flags.utf8_mode),
            "default_encoding": locale.getpreferredencoding(False),
            "compile_mode": (
                DoclingCompileMode.DISABLED_UNCOMPILED_HERON.value
                if "request" in locals()
                and bool(request.get("disable_heron_torch_compile", True))
                else DoclingCompileMode.RUNTIME_DEFAULT.value
            ),
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
