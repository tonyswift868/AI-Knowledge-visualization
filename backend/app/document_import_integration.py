"""Host-owned HTTP adapter for the delivered Document Import application."""

from __future__ import annotations

import os
import shutil
import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from .runtime_resources import project_resource_root
from typing import Any

from fastapi import APIRouter, Body, FastAPI, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from .knowledge_policy import CONVERSION_POLICY
from .sources.document import DocumentSourcePolicy
from .sources.document.ingestion import (
    DocumentImportApplication,
    DocumentImportStateError,
    DocumentIngestionApplication,
)
from .upload_limits import UploadLimitExceeded, read_upload_limited


DOCUMENT_IMPORT_ROUTE_PREFIX = "/api/document-import"
DOCUMENT_IMPORT_MAX_FILES = 20
PROJECT_ROOT = project_resource_root()


class DocumentBatchStartIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected_file_ids: list[str] | None = None
    conversion_batch_size: int = Field(default=1, ge=1, le=DOCUMENT_IMPORT_MAX_FILES)


ApplicationFactory = Callable[
    [DocumentSourcePolicy, Path],
    DocumentImportApplication,
]


def _default_runtime_root() -> Path:
    explicit = str(os.getenv("MENTORFLOW_DOCUMENT_IMPORT_ROOT") or "").strip()
    if explicit:
        candidate = Path(explicit).expanduser()
    else:
        user_root = str(os.getenv("MENTORFLOW_USER_DATA_ROOT") or "").strip()
        candidate = (
            Path(user_root).expanduser() / "document-import"
            if user_root
            else PROJECT_ROOT / "data" / "document-import"
        )
    if not candidate.is_absolute():
        raise RuntimeError("document import runtime root must be absolute")
    return candidate.resolve()


def _configured_artifacts_root() -> Path | None:
    configured = str(
        os.getenv("MENTORFLOW_DOCUMENT_ARTIFACTS_ROOT")
        or os.getenv("MENTORFLOW_TEST_DOCLING_ARTIFACTS")
        or ""
    ).strip()
    if not configured:
        return None
    candidate = Path(configured).expanduser()
    if not candidate.is_absolute():
        raise RuntimeError("document artifacts root must be absolute")
    return candidate.resolve()


def _default_application_factory(
    policy: DocumentSourcePolicy,
    state_root: Path,
) -> DocumentImportApplication:
    return DocumentImportApplication(
        DocumentIngestionApplication(policy),
        state_root=state_root,
    )


class DocumentImportHost:
    """Own upload staging and one local single-user application instance."""

    def __init__(
        self,
        *,
        runtime_root: Path | None = None,
        application_factory: ApplicationFactory = _default_application_factory,
    ) -> None:
        self._configured_root = runtime_root
        self._application_factory = application_factory
        self._lock = threading.RLock()
        self._application: DocumentImportApplication | None = None
        self._input_root: Path | None = None

    def application(self) -> DocumentImportApplication:
        with self._lock:
            if self._application is not None:
                return self._application
            root = (
                self._configured_root.resolve()
                if self._configured_root is not None
                else _default_runtime_root()
            )
            input_root = (root / "uploads").resolve()
            state_root = (root / "state").resolve()
            input_root.mkdir(parents=True, exist_ok=True)
            state_root.mkdir(parents=True, exist_ok=True)
            policy = DocumentSourcePolicy(
                input_root=input_root,
                hard_max_file_bytes=CONVERSION_POLICY.maximum_input_bytes,
                max_batch_files=DOCUMENT_IMPORT_MAX_FILES,
                pdf_artifacts_path=_configured_artifacts_root(),
            )
            self._application = self._application_factory(policy, state_root)
            self._input_root = input_root
            return self._application

    @property
    def input_root(self) -> Path:
        self.application()
        assert self._input_root is not None
        return self._input_root

    async def select_uploads(
        self,
        uploads: list[UploadFile],
    ) -> dict[str, Any]:
        if not uploads:
            raise HTTPException(
                status_code=422,
                detail={"code": "DOCUMENT_SELECTION_EMPTY", "message": "请至少选择一个文档"},
            )
        if len(uploads) > DOCUMENT_IMPORT_MAX_FILES:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "DOCUMENT_BATCH_SIZE_LIMIT",
                    "message": f"一次最多选择 {DOCUMENT_IMPORT_MAX_FILES} 个文档",
                },
            )

        selection_root = (
            self.input_root / f"selection-{uuid.uuid4().hex}"
        ).resolve()
        if self.input_root not in selection_root.parents:
            raise RuntimeError("document selection root escaped the configured input root")
        selection_root.mkdir(parents=False, exist_ok=False)
        staged: list[Path] = []
        aggregate_used = 0
        aggregate_limit = (
            int(CONVERSION_POLICY.maximum_input_bytes) * DOCUMENT_IMPORT_MAX_FILES
        )
        try:
            for index, upload in enumerate(uploads, start=1):
                raw_name = str(upload.filename or f"document-{index}")
                safe_name = Path(raw_name.replace("\\", "/")).name.strip()
                if not safe_name or safe_name in {".", ".."}:
                    safe_name = f"document-{index}"
                target = (selection_root / safe_name).resolve()
                if target.exists():
                    target = (
                        selection_root
                        / f"{Path(safe_name).stem}-{index}{Path(safe_name).suffix}"
                    ).resolve()
                if selection_root not in target.parents:
                    raise HTTPException(
                        status_code=422,
                        detail={
                            "code": "DOCUMENT_FILENAME_INVALID",
                            "message": "文件名无效，请重新选择文档",
                        },
                    )
                payload = await read_upload_limited(
                    upload,
                    int(CONVERSION_POLICY.maximum_input_bytes),
                    aggregate_limit=aggregate_limit,
                    aggregate_used=aggregate_used,
                )
                aggregate_used += len(payload)
                with target.open("xb") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                staged.append(target)
            return self.application().select(staged).to_dict()
        except UploadLimitExceeded as error:
            shutil.rmtree(selection_root, ignore_errors=True)
            raise HTTPException(
                status_code=413,
                detail={
                    "code": error.code,
                    "message": "文档超过本次导入大小上限",
                },
            ) from error
        except BaseException:
            shutil.rmtree(selection_root, ignore_errors=True)
            raise
        finally:
            for upload in uploads:
                await upload.close()

    def close(self) -> None:
        with self._lock:
            application = self._application
            self._application = None
            self._input_root = None
        if application is not None:
            application.close()


router = APIRouter(prefix=DOCUMENT_IMPORT_ROUTE_PREFIX, tags=["document-import"])


def _host(request: Request) -> DocumentImportHost:
    return request.app.state.document_import_host


def _state_error(error: DocumentImportStateError) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={"code": "DOCUMENT_IMPORT_STATE_INVALID", "message": str(error)},
    )


@router.post("/selections")
async def select_documents(
    request: Request,
    files: list[UploadFile] = File(...),
) -> dict[str, Any]:
    return await _host(request).select_uploads(files)


@router.post("/selections/{selection_id}/preflight")
def preflight_documents(request: Request, selection_id: str) -> dict[str, Any]:
    try:
        return _host(request).application().preflight(selection_id).to_dict()
    except DocumentImportStateError as error:
        raise _state_error(error) from error


@router.post("/selections/{selection_id}/batches")
def start_document_batch(
    request: Request,
    selection_id: str,
    body: DocumentBatchStartIn = Body(),
) -> dict[str, Any]:
    try:
        return _host(request).application().start_batch(
            selection_id,
            selected_file_ids=body.selected_file_ids,
            conversion_batch_size=body.conversion_batch_size,
        ).to_dict()
    except DocumentImportStateError as error:
        raise _state_error(error) from error


@router.get("/batches/{batch_id}/progress")
def document_batch_progress(request: Request, batch_id: str) -> dict[str, Any]:
    try:
        return _host(request).application().progress(batch_id).to_dict()
    except DocumentImportStateError as error:
        raise _state_error(error) from error


@router.get("/batches/{batch_id}/result")
def document_batch_result(request: Request, batch_id: str) -> dict[str, Any] | None:
    try:
        result = _host(request).application().result(batch_id)
        return result.to_dict() if result is not None else None
    except DocumentImportStateError as error:
        raise _state_error(error) from error


@router.post("/batches/{batch_id}/cancel")
def cancel_document_batch(request: Request, batch_id: str) -> dict[str, Any]:
    try:
        return _host(request).application().cancel(batch_id).to_dict()
    except DocumentImportStateError as error:
        raise _state_error(error) from error


@router.post("/batches/{batch_id}/resume")
def resume_document_batch(request: Request, batch_id: str) -> dict[str, Any]:
    try:
        return _host(request).application().resume(batch_id).to_dict()
    except DocumentImportStateError as error:
        raise _state_error(error) from error


def install_document_import(
    app: FastAPI,
    *,
    host: DocumentImportHost | None = None,
) -> None:
    document_host = host or DocumentImportHost()
    app.state.document_import_host = document_host
    app.include_router(router)


__all__ = [
    "DOCUMENT_IMPORT_ROUTE_PREFIX",
    "DocumentImportHost",
    "install_document_import",
    "router",
]
