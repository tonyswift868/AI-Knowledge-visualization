from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.document_import_integration import DocumentImportHost, install_document_import
from app.sources.document import DoclingDependencyInfo
from app.sources.document.ingestion import (
    DocumentImportApplication,
    DocumentIngestionApplication,
)


AVAILABLE = DoclingDependencyInfo(
    available=True,
    version="2.119.0",
    module_file="E:/runtime/docling/__init__.py",
)


class _Value:
    def __init__(self, value: str) -> None:
        self.value = value


class _Ref:
    def __init__(self, value: str) -> None:
        self.cref = value


@dataclass
class _Provenance:
    page_no: int | None = None
    bbox: Any = None
    charspan: tuple[int, int] = (0, 80)


@dataclass
class _Item:
    self_ref: str
    label: Any
    text: str
    parent: Any = None
    level: int | None = None
    prov: tuple[Any, ...] = ()
    content_layer: Any = _Value("body")


class _Document:
    name = "document-import-http-fixture"

    def iterate_items(self, with_groups: bool = False):
        assert with_groups is True
        provenance = (_Provenance(),)
        yield _Item(
            "#/texts/0",
            _Value("section_header"),
            "Exercises",
            parent=_Ref("#/body"),
            level=1,
            prov=provenance,
        ), 1
        yield _Item(
            "#/texts/1",
            _Value("text"),
            "Question: Which token is local?",
            parent=_Ref("#/texts/0"),
            prov=provenance,
        ), 1
        yield _Item(
            "#/texts/2",
            _Value("text"),
            "Answer: LOCAL-ONLY is the source token.",
            parent=_Ref("#/texts/0"),
            prov=provenance,
        ), 1


class _SuccessResult:
    status = _Value("success")
    errors: tuple[Any, ...] = ()
    document = _Document()


class _Converter:
    def convert_all(self, source: list[Path], **_kwargs: Any):
        for _path in source:
            yield _SuccessResult()

    def close(self) -> None:
        return None


def _host(tmp_path: Path) -> DocumentImportHost:
    def factory(policy, state_root: Path) -> DocumentImportApplication:
        ingestion = DocumentIngestionApplication(
            policy,
            dependency_probe=lambda: AVAILABLE,
            converter_factory=lambda _policy: _Converter(),
        )
        return DocumentImportApplication(ingestion, state_root=state_root)

    return DocumentImportHost(
        runtime_root=(tmp_path / "document-runtime").resolve(),
        application_factory=factory,
    )


def _app(tmp_path: Path) -> FastAPI:
    app = FastAPI()
    install_document_import(app, host=_host(tmp_path))
    return app


def test_document_import_http_flow_stages_preflights_and_returns_preview(
    tmp_path: Path,
) -> None:
    with TestClient(_app(tmp_path)) as client:
        selected = client.post(
            "/api/document-import/selections",
            files={
                "files": (
                    "lesson.md",
                    b"# Exercises\n\nQuestion: Which token is local?",
                    "text/markdown",
                )
            },
        )
        assert selected.status_code == 200
        selection = selected.json()
        assert selection["preflight_complete"] is False

        preflight = client.post(
            f"/api/document-import/selections/{selection['selection_id']}/preflight",
            json={},
        )
        assert preflight.status_code == 200
        candidate = preflight.json()["candidates"][0]
        assert candidate["display_name"] == "lesson.md"
        assert candidate["selectable"] is True

        started = client.post(
            f"/api/document-import/selections/{selection['selection_id']}/batches",
            json={"selected_file_ids": [candidate["file_id"]]},
        )
        assert started.status_code == 200
        batch_id = started.json()["batch_id"]

        result = None
        for _ in range(100):
            response = client.get(
                f"/api/document-import/batches/{batch_id}/result"
            )
            assert response.status_code == 200
            result = response.json()
            if result is not None:
                break
            time.sleep(0.01)

        assert result is not None
        assert result["state"] == "COMPLETED"
        assert result["progress"]["completed_files"] == 1
        assert Path(result["files"][0]["source_file"]).name == "lesson.md"
        assert result["provenance_preview"]
        assert "Which token is local?" in result["exercise_preview"][0][
            "question"
        ]
        assert result["knowledge_handoff_status"] == "NOT_CONFIGURED"


def test_document_import_http_adapter_sanitizes_names_and_bounds_file_count(
    tmp_path: Path,
) -> None:
    with TestClient(_app(tmp_path)) as client:
        selected = client.post(
            "/api/document-import/selections",
            files={"files": ("../../safe.md", b"# safe", "text/markdown")},
        )
        assert selected.status_code == 200
        selection_id = selected.json()["selection_id"]
        preflight = client.post(
            f"/api/document-import/selections/{selection_id}/preflight",
            json={},
        ).json()
        assert preflight["candidates"][0]["display_name"] == "safe.md"
        assert ".." not in preflight["candidates"][0]["source_file"]

        too_many = [
            ("files", (f"file-{index}.md", b"# safe", "text/markdown"))
            for index in range(21)
        ]
        rejected = client.post(
            "/api/document-import/selections",
            files=too_many,
        )
        assert rejected.status_code == 422
        assert rejected.json()["detail"]["code"] == "DOCUMENT_BATCH_SIZE_LIMIT"


def test_canonical_app_registers_all_document_import_http_seams() -> None:
    from app.main import app

    paths = set(app.openapi()["paths"])
    assert {
        "/api/document-import/selections",
        "/api/document-import/selections/{selection_id}/preflight",
        "/api/document-import/selections/{selection_id}/batches",
        "/api/document-import/batches/{batch_id}/progress",
        "/api/document-import/batches/{batch_id}/result",
        "/api/document-import/batches/{batch_id}/cancel",
        "/api/document-import/batches/{batch_id}/resume",
    } <= paths
